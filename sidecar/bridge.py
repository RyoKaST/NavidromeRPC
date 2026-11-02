#!/usr/bin/env python3
import hmac
import json
import os
import select
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 16 * 1024
MAX_TEXT = 128
MIN_TEXT = 2
MAX_IMAGE_URL = 256
MIN_SEND_INTERVAL = 2
MIN_BACKOFF = 1
MAX_BACKOFF = 30
STALE_SECONDS = 60
# discord rejects short text and trims spaces, a blank braille cell gets through
PADDING = "⠀"

ACTIVE_STATES = {"starting", "playing", "paused"}
STATES = ACTIVE_STATES | {"stopped", "expired"}


def number(value, default=0):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected a number")
    return value


def parse_event(body):
    try:
        event = json.loads(body)
    except (ValueError, UnicodeDecodeError) as err:
        raise ValueError("invalid JSON") from err
    if not isinstance(event, dict):
        raise ValueError("expected an object")
    state = event.get("state")
    if state not in STATES:
        raise ValueError("unknown state")
    timestamp = number(event.get("timestamp"), None)
    if timestamp is None or timestamp < 0:
        raise ValueError("invalid timestamp")
    title = event.get("title")
    if state in ACTIVE_STATES and not (isinstance(title, str) and title.strip()):
        raise ValueError("missing title")
    duration = number(event.get("durationMs"))
    position = number(event.get("positionMs"))
    if duration < 0 or position < 0:
        raise ValueError("negative time")
    return {
        "state": state,
        "title": title,
        "artist": event.get("artist"),
        "album": event.get("album"),
        "durationMs": duration,
        "positionMs": position,
        "playbackRate": number(event.get("playbackRate"), 1),
        "timestamp": timestamp,
        "coverUrl": event.get("coverUrl"),
    }


def clean_text(value, fallback):
    text = value.strip() if isinstance(value, str) else ""
    return (text or fallback)[:MAX_TEXT].ljust(MIN_TEXT, PADDING)


def cover_assets(event):
    url = event["coverUrl"]
    if not isinstance(url, str) or not url.startswith("https://") or len(url) > MAX_IMAGE_URL:
        return None
    return {"large_image": url, "large_text": clean_text(event["album"], "Navidrome")}


def build_activity(event):
    state = event["state"]
    if state not in ACTIVE_STATES:
        return None, None
    artist = clean_text(event["artist"], "Unknown artist")
    activity = {
        "type": 2,
        "name": "Navidrome",
        "details": clean_text(event["title"], "Unknown track"),
        "state": artist,
    }
    assets = cover_assets(event)
    if assets:
        activity["assets"] = assets
    if state == "paused":
        activity["state"] = clean_text(f"Paused · {artist}", "Paused")
        return activity, None

    rate = event["playbackRate"] if event["playbackRate"] > 0 else 1
    length = event["durationMs"] / rate
    elapsed = event["positionMs"] / rate
    if length:
        elapsed = min(elapsed, length)
    start = int(event["timestamp"] * 1000 - elapsed)
    activity["timestamps"] = {"start": start}
    if not length:
        return activity, None
    end = int(start + length)
    activity["timestamps"]["end"] = end
    return activity, end / 1000


def log(message):
    print(f"bridge: {message}", flush=True)


class IpcClient:
    # frames are op and length as little-endian u32, then json
    def __init__(self, path, client_id):
        self.path = path
        self.client_id = client_id
        self.sock = None
        self.nonce = 0

    def connect(self):
        self.close()
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(5)
        try:
            self.sock.connect(self.path)
            self._write(0, {"v": 1, "client_id": self.client_id})
            self._read()
        except (OSError, ValueError) as err:
            self.close()
            raise OSError(f"handshake failed: {err}") from err

    def alive(self):
        if self.sock is None:
            return False
        try:
            readable, _, _ = select.select([self.sock], [], [], 0)
            return not readable or bool(self.sock.recv(1, socket.MSG_PEEK))
        except OSError:
            return False

    def set_activity(self, activity):
        self.nonce += 1
        nonce = str(self.nonce)
        args = {"pid": os.getpid(), "activity": activity}
        self._write(1, {"cmd": "SET_ACTIVITY", "args": args, "nonce": nonce})
        try:
            while self._read().get("nonce") != nonce:
                pass
        except ValueError as err:
            raise OSError(f"bad reply: {err}") from err

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _write(self, op, payload):
        data = json.dumps(payload).encode()
        self.sock.sendall(struct.pack("<II", op, len(data)) + data)

    def _read(self):
        op, length = struct.unpack("<II", self._read_exact(8))
        payload = json.loads(self._read_exact(length))
        if op == 2:
            raise OSError(f"closed by Discord: {payload}")
        return payload

    def _read_exact(self, size):
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise OSError("socket closed")
            data += chunk
        return data


class Presence:
    def __init__(self, client):
        self.client = client
        self.lock = threading.Lock()
        self.activity = None
        self.ends_at = None
        self.last_timestamp = 0
        self.dirty = False
        self.connected = False
        self.last_sent = float("-inf")
        self.backoff = MIN_BACKOFF
        self.next_retry = 0

    def update(self, event):
        with self.lock:
            if event["timestamp"] < self.last_timestamp:
                return False
            self.last_timestamp = event["timestamp"]
            self.activity, self.ends_at = build_activity(event)
            self.dirty = True
            return True

    def tick(self, now):
        with self.lock:
            if self.ends_at is not None and now > self.ends_at + STALE_SECONDS:
                self.activity = None
                self.ends_at = None
                self.dirty = True
            if self.connected and not self.client.alive():
                log("socket closed")
                self._disconnect(now)
            if not self.connected and not self._connect(now):
                return
            if self.dirty and now - self.last_sent >= MIN_SEND_INTERVAL:
                self._send(now)

    def _connect(self, now):
        if now < self.next_retry:
            return False
        try:
            self.client.connect()
        except OSError as err:
            log(f"connect failed: {err}")
            self._schedule_retry(now)
            return False
        log("connected")
        self.connected = True
        self.backoff = MIN_BACKOFF
        self.dirty = True
        return True

    def _send(self, now):
        try:
            self.client.set_activity(self.activity)
        except OSError as err:
            log(f"send failed: {err}")
            self._disconnect(now)
            return
        self.dirty = False
        self.last_sent = now

    def _disconnect(self, now):
        self.client.close()
        self.connected = False
        self.dirty = True
        self._schedule_retry(now)

    def _schedule_retry(self, now):
        self.next_retry = now + self.backoff
        self.backoff = min(self.backoff * 2, MAX_BACKOFF)


def make_handler(presence, secret):
    expected = f"Bearer {secret}".encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/health":
                return self.reply(404)
            self.reply(200 if presence.connected else 503)

        def do_POST(self):
            if self.path != "/event":
                return self.reply(404)
            token = self.headers.get("Authorization", "").encode("latin-1", "replace")
            if not hmac.compare_digest(token, expected):
                return self.reply(401)
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self.reply(400)
            if length < 0:
                return self.reply(400)
            if length > MAX_BODY:
                return self.reply(413)
            try:
                event = parse_event(self.rfile.read(length))
            except ValueError:
                return self.reply(400)
            presence.update(event)
            self.reply(204)

        def reply(self, status):
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    return Handler


def load_config(env):
    client_id = env.get("CLIENT_ID", "")
    secret = env.get("BRIDGE_SECRET", "")
    if not client_id:
        sys.exit("bridge: CLIENT_ID is required")
    if len(secret) < 16:
        sys.exit("bridge: BRIDGE_SECRET must be at least 16 characters")
    return client_id, secret, int(env.get("BRIDGE_PORT", "7878"))


def main():
    client_id, secret, port = load_config(os.environ)
    path = os.path.join(os.environ["XDG_RUNTIME_DIR"], "discord-ipc-0")
    presence = Presence(IpcClient(path, client_id))
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(presence, secret))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"listening on port {port}")
    while True:
        presence.tick(time.time())
        time.sleep(0.25)


if __name__ == "__main__":
    main()
