import contextlib
import io
import json
import os
import socket
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import bridge


def event(**overrides):
    base = {
        "state": "playing",
        "title": "Song",
        "artist": "Artist",
        "album": "Album",
        "durationMs": 200000,
        "positionMs": 20000,
        "playbackRate": 1.0,
        "timestamp": 1000,
        "coverUrl": "https://example.org/cover.jpg",
    }
    base.update(overrides)
    return base


def parsed(**overrides):
    return bridge.parse_event(json.dumps(event(**overrides)).encode())


class ParseEventTest(unittest.TestCase):
    def test_accepts_every_state(self):
        for state in ("starting", "playing", "paused", "stopped", "expired"):
            self.assertEqual(parsed(state=state)["state"], state)

    def test_stop_needs_no_track(self):
        body = json.dumps({"state": "stopped", "timestamp": 1000}).encode()
        self.assertEqual(bridge.parse_event(body)["state"], "stopped")

    def test_rejects_bad_input(self):
        bad = [
            b"not json",
            b"\xff\xfe",
            b"[]",
            json.dumps(event(state="rewinding")).encode(),
            json.dumps(event(title="")).encode(),
            json.dumps(event(timestamp="soon")).encode(),
            json.dumps(event(timestamp=True)).encode(),
            json.dumps(event(positionMs="x")).encode(),
            json.dumps(event(durationMs=-1)).encode(),
        ]
        for body in bad:
            with self.assertRaises(ValueError, msg=body):
                bridge.parse_event(body)


class BuildActivityTest(unittest.TestCase):
    def test_playing(self):
        activity, ends_at = bridge.build_activity(parsed())
        self.assertEqual(activity["type"], 2)
        self.assertEqual(activity["name"], "Navidrome")
        self.assertEqual(activity["details"], "Song")
        self.assertEqual(activity["state"], "Artist")
        self.assertEqual(activity["timestamps"], {"start": 980000, "end": 1180000})
        self.assertEqual(
            activity["assets"],
            {"large_image": "https://example.org/cover.jpg", "large_text": "Album"},
        )
        self.assertEqual(ends_at, 1180)

    def test_starting_matches_playing(self):
        self.assertEqual(bridge.build_activity(parsed(state="starting")), bridge.build_activity(parsed()))

    def test_paused_has_no_timer(self):
        activity, ends_at = bridge.build_activity(parsed(state="paused"))
        self.assertNotIn("timestamps", activity)
        self.assertEqual(activity["state"], "Paused · Artist")
        self.assertIsNone(ends_at)

    def test_stopped_and_expired_clear(self):
        self.assertEqual(bridge.build_activity(parsed(state="stopped")), (None, None))
        self.assertEqual(bridge.build_activity(parsed(state="expired")), (None, None))

    def test_playback_rate_scales_the_timer(self):
        activity, _ = bridge.build_activity(parsed(playbackRate=2.0))
        self.assertEqual(activity["timestamps"], {"start": 990000, "end": 1090000})

    def test_invalid_rate_counts_as_normal_speed(self):
        for rate in (0, -1):
            activity, _ = bridge.build_activity(parsed(playbackRate=rate))
            self.assertEqual(activity["timestamps"]["end"], 1180000)

    def test_unknown_duration_has_no_end(self):
        activity, ends_at = bridge.build_activity(parsed(durationMs=0))
        self.assertEqual(activity["timestamps"], {"start": 980000})
        self.assertIsNone(ends_at)

    def test_position_past_the_end_is_clamped(self):
        activity, _ = bridge.build_activity(parsed(positionMs=500000))
        self.assertEqual(activity["timestamps"], {"start": 800000, "end": 1000000})

    def test_long_text_is_truncated_without_splitting_characters(self):
        activity, _ = bridge.build_activity(parsed(title="🎵" * 300, artist="é" * 300))
        self.assertEqual(activity["details"], "🎵" * 128)
        self.assertEqual(activity["state"], "é" * 128)

    def test_short_text_is_padded(self):
        activity, _ = bridge.build_activity(parsed(title="X", artist="Y"))
        self.assertEqual(len(activity["details"]), 2)
        self.assertEqual(len(activity["state"]), 2)

    def test_missing_artist_has_a_fallback(self):
        activity, _ = bridge.build_activity(parsed(artist=""))
        self.assertEqual(activity["state"], "Unknown artist")

    def test_only_short_https_covers_are_used(self):
        for url in ("http://example.org/a.jpg", "file:///etc/passwd", "https://e.org/" + "a" * 300, ""):
            activity, _ = bridge.build_activity(parsed(coverUrl=url))
            self.assertNotIn("assets", activity, url)


class FakeClient:
    def __init__(self):
        self.sent = []
        self.connect_error = False
        self.send_error = False
        self.is_alive = True
        self.connects = 0

    def connect(self):
        if self.connect_error:
            raise OSError("no socket")
        self.connects += 1
        self.is_alive = True

    def alive(self):
        return self.is_alive

    def set_activity(self, activity):
        if self.send_error:
            raise OSError("broken pipe")
        self.sent.append(activity)

    def close(self):
        pass


class PresenceTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.presence = bridge.Presence(self.client)

    def details(self):
        return [a and a["details"] for a in self.client.sent]

    def test_sends_the_activity(self):
        self.presence.update(parsed())
        self.presence.tick(1000)
        self.assertEqual(self.details(), ["Song"])
        self.assertTrue(self.presence.connected)

    def test_does_not_resend_without_a_change(self):
        self.presence.update(parsed())
        self.presence.tick(1000)
        self.presence.tick(1010)
        self.assertEqual(len(self.client.sent), 1)

    def test_rate_limit_keeps_only_the_latest_event(self):
        self.presence.update(parsed(title="One"))
        self.presence.tick(1000)
        self.presence.update(parsed(title="Two"))
        self.presence.update(parsed(title="Three"))
        self.presence.tick(1001)
        self.assertEqual(self.details(), ["One"])
        self.presence.tick(1002)
        self.assertEqual(self.details(), ["One", "Three"])

    def test_stop_clears(self):
        self.presence.update(parsed())
        self.presence.tick(1000)
        self.presence.update(parsed(state="stopped"))
        self.presence.tick(1002)
        self.assertEqual(self.details(), ["Song", None])

    def test_older_event_is_ignored(self):
        self.assertTrue(self.presence.update(parsed(title="New", timestamp=2000)))
        self.assertFalse(self.presence.update(parsed(state="stopped", timestamp=1999)))
        self.presence.tick(2000)
        self.assertEqual(self.details(), ["New"])

    def test_event_from_the_same_second_is_accepted(self):
        self.presence.update(parsed(timestamp=2000))
        self.assertTrue(self.presence.update(parsed(state="stopped", timestamp=2000)))

    def test_stale_activity_clears_after_the_grace_period(self):
        self.presence.update(parsed())
        self.presence.tick(1000)
        self.presence.tick(1180 + 60)
        self.assertEqual(self.details(), ["Song"])
        self.presence.tick(1180 + 61)
        self.assertEqual(self.details(), ["Song", None])

    def test_unknown_duration_never_goes_stale(self):
        self.presence.update(parsed(durationMs=0))
        self.presence.tick(1000)
        self.presence.tick(1000000)
        self.assertEqual(self.details(), ["Song"])

    def test_paused_never_goes_stale(self):
        self.presence.update(parsed(state="paused"))
        self.presence.tick(1000)
        self.presence.tick(1000000)
        self.assertEqual(self.details(), ["Song"])

    def test_connect_retries_with_capped_backoff(self):
        self.client.connect_error = True
        self.presence.update(parsed())
        delays = []
        now = 1000
        for _ in range(8):
            self.presence.tick(now)
            delays.append(self.presence.next_retry - now)
            now = self.presence.next_retry
        self.assertEqual(delays, [1, 2, 4, 8, 16, 30, 30, 30])
        self.assertFalse(self.presence.connected)

    def test_waits_for_the_backoff_before_retrying(self):
        self.client.connect_error = True
        self.presence.tick(1000)
        self.client.connect_error = False
        self.presence.tick(1000.5)
        self.assertEqual(self.client.connects, 0)
        self.presence.tick(1001)
        self.assertEqual(self.client.connects, 1)

    def test_reconnect_restores_the_paused_status(self):
        self.presence.update(parsed(state="paused"))
        self.presence.tick(1000)
        self.client.is_alive = False
        self.presence.tick(1010)
        self.assertFalse(self.presence.connected)
        self.presence.tick(1011)
        self.assertTrue(self.presence.connected)
        self.assertEqual(len(self.client.sent), 2)
        self.assertEqual(self.client.sent[1], self.client.sent[0])
        self.assertNotIn("timestamps", self.client.sent[1])

    def test_failed_send_is_retried_after_reconnecting(self):
        self.presence.tick(1000)
        self.client.send_error = True
        self.presence.update(parsed())
        self.presence.tick(1002)
        self.assertFalse(self.presence.connected)
        self.client.send_error = False
        self.presence.tick(1003)
        self.assertEqual(self.details(), [None, "Song"])

    def test_connecting_clears_any_old_status(self):
        self.presence.tick(1000)
        self.assertEqual(self.client.sent, [None])


class FakeDiscord:
    def __init__(self, path):
        self.commands = []
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(path)
        self.server.listen()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    @staticmethod
    def read_frame(conn):
        header = conn.recv(8, socket.MSG_WAITALL)
        if len(header) < 8:
            return None
        op, length = struct.unpack("<II", header)
        return op, json.loads(conn.recv(length, socket.MSG_WAITALL))

    @staticmethod
    def write_frame(conn, op, payload):
        data = json.dumps(payload).encode()
        conn.sendall(struct.pack("<II", op, len(data)) + data)

    def run(self):
        conn, _ = self.server.accept()
        self.conn = conn
        _, handshake = self.read_frame(conn)
        self.client_id = handshake["client_id"]
        self.write_frame(conn, 1, {"cmd": "DISPATCH", "evt": "READY"})
        while True:
            frame = self.read_frame(conn)
            if frame is None:
                return
            self.commands.append(frame[1])
            self.write_frame(conn, 1, {"evt": "OTHER"})
            self.write_frame(conn, 1, {"cmd": "SET_ACTIVITY", "nonce": frame[1]["nonce"]})

    def close(self):
        self.conn.shutdown(socket.SHUT_RDWR)
        self.conn.close()
        self.server.close()


class IpcClientTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "discord-ipc-0")

    def test_handshake_and_set_activity(self):
        discord = FakeDiscord(self.path)
        client = bridge.IpcClient(self.path, "123")
        client.connect()
        client.set_activity({"details": "Song"})
        client.set_activity(None)
        client.close()
        discord.thread.join(2)
        self.assertEqual(discord.client_id, "123")
        self.assertEqual(discord.commands[0]["cmd"], "SET_ACTIVITY")
        self.assertEqual(discord.commands[0]["args"]["activity"], {"details": "Song"})
        self.assertEqual(discord.commands[0]["args"]["pid"], os.getpid())
        self.assertIsNone(discord.commands[1]["args"]["activity"])

    def test_alive_notices_a_closed_socket(self):
        discord = FakeDiscord(self.path)
        client = bridge.IpcClient(self.path, "123")
        client.connect()
        self.assertTrue(client.alive())
        discord.close()
        discord.thread.join(2)
        self.assertFalse(client.alive())
        client.close()

    def test_connect_fails_without_a_socket(self):
        with self.assertRaises(OSError):
            bridge.IpcClient(self.path, "123").connect()

    def test_alive_is_false_before_connecting(self):
        self.assertFalse(bridge.IpcClient(self.path, "123").alive())


SECRET = "0123456789abcdef"


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.presence = bridge.Presence(self.client)
        handler = bridge.make_handler(self.presence, SECRET)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def call(self, path, body=None, secret=SECRET, headers=None):
        request = urllib.request.Request(self.base + path, data=body)
        if secret:
            request.add_header("Authorization", f"Bearer {secret}")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as err:
            with err:
                return err.code, err.read()

    def test_event_is_applied(self):
        status, _ = self.call("/event", json.dumps(event()).encode())
        self.assertEqual(status, 204)
        self.assertEqual(self.presence.activity["details"], "Song")

    def test_event_is_logged(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.call("/event", json.dumps(event()).encode())
        self.assertIn("event: playing Song", output.getvalue())

    def test_wrong_or_missing_secret_is_refused(self):
        for secret in ("wrong", None, "é"):
            status, _ = self.call("/event", json.dumps(event()).encode(), secret=secret)
            self.assertEqual(status, 401, secret)
        self.assertIsNone(self.presence.activity)

    def test_invalid_body_is_refused(self):
        status, _ = self.call("/event", b"nope")
        self.assertEqual(status, 400)

    def test_large_body_is_refused(self):
        status, _ = self.call("/event", b"x" * (16 * 1024 + 1))
        self.assertEqual(status, 413)

    def test_body_at_the_limit_is_read(self):
        status, _ = self.call("/event", b" " * (16 * 1024))
        self.assertEqual(status, 400)

    def test_bad_content_length_is_refused(self):
        status, _ = self.call("/event", b"{}", headers={"Content-Length": "-5"})
        self.assertEqual(status, 400)

    def test_health_follows_the_socket_without_a_secret(self):
        status, body = self.call("/health", secret=None)
        self.assertEqual(status, 503)
        self.presence.tick(1000)
        status, body = self.call("/health", secret=None)
        self.assertEqual((status, body), (200, b""))

    def test_unknown_paths_are_not_found(self):
        self.assertEqual(self.call("/nope")[0], 404)
        self.assertEqual(self.call("/nope", b"{}")[0], 404)


class LoadConfigTest(unittest.TestCase):
    def test_defaults(self):
        env = {"CLIENT_ID": "123", "BRIDGE_SECRET": SECRET}
        self.assertEqual(bridge.load_config(env), ("123", SECRET, 6464))

    def test_port_override(self):
        env = {"CLIENT_ID": "123", "BRIDGE_SECRET": SECRET, "BRIDGE_PORT": "9000"}
        self.assertEqual(bridge.load_config(env)[2], 9000)

    def test_refuses_to_start_without_required_settings(self):
        for env in ({}, {"CLIENT_ID": "123"}, {"CLIENT_ID": "123", "BRIDGE_SECRET": "short"}, {"BRIDGE_SECRET": SECRET}):
            with self.assertRaises(SystemExit):
                bridge.load_config(env)
