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
