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
