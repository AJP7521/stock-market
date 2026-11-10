"""Prompt 9 replay checks using constructed TEST DATA in a disposable SQLite file.

Run from the project root:
    python3 -m unittest backend.tests.test_replay -v
"""

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.recorder import connect, record
from backend.replay import connect_readonly, replay, sessions
from backend.tests.test_candles import ms, msg


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test-replay.sqlite"
        conn = connect(self.path)
        # Prompt 7 observations; the middle one (15:00:20) arrives late, at 15:01:10.
        record(conn, msg("14:59:59", 99, 1000), ms("14:59:59") + 300, source="test")
        record(conn, msg("15:00:05", 100, 1004), ms("15:00:05") + 300, source="test")
        record(conn, msg("15:00:50", 101, 1011), ms("15:00:50") + 300, source="test")
        record(conn, msg("15:01:05", 102, 1015), ms("15:01:05") + 300, source="test")
        record(conn, msg("15:00:20", 103, 1009), ms("15:01:10") + 300, source="test")      # late
        record(conn, msg("15:00:20", 103, 1009), ms("15:01:11") + 300, source="test")      # exact repeat
        late_id = conn.execute("SELECT id FROM observations WHERE price = 103").fetchone()[0]
        record(conn, msg("15:00:20", 102.5, 1009), ms("15:01:20") + 300, source="test", revises_id=late_id)
        conn.close()
        self.ro = connect_readonly(self.path)

    def tearDown(self):
        self.ro.close()
        self.tmp.cleanup()

    def run_replay(self, timeframe="1m"):
        return replay(self.ro, "TEST", ms("14:59:00"), ms("15:02:00"), timeframe)

    def test_steps_follow_receipt_order_and_late_arrival_appears_when_received(self):
        r = self.run_replay()
        self.assertEqual(r["receipt_count"], 7)
        self.assertEqual(r["observation_count"], 6)                    # repeat is not a new observation
        sources = [s["observation"]["source_time"][11:19] for s in r["steps"]]
        self.assertEqual(sources, ["14:59:59", "15:00:05", "15:00:50", "15:01:05", "15:00:20", "15:00:20", "15:00:20"])
        # Before the late arrival, the 15:00 candle has high 101; at step 5 it becomes 103.
        self.assertEqual(r["steps"][2]["candle"]["high"], 101.0)
        late = r["steps"][4]
        self.assertTrue(late["observation"]["late"])
        self.assertEqual(late["replay_time"], "2026-11-09T15:01:10.300Z")
        self.assertEqual(late["candle"], {"timestamp": "2026-11-09T15:00:00Z", "open": 100.0, "high": 103.0,
                                          "low": 100.0, "close": 101.0, "volume": 11})
        self.assertTrue(r["steps"][5]["observation"]["is_repeat"])
        self.assertEqual(r["steps"][5]["candle"], late["candle"])       # repeat changes nothing

    def test_revision_replaces_original_at_its_arrival(self):
        r = self.run_replay()
        rev = r["steps"][6]
        self.assertIsNotNone(rev["observation"]["revises_id"])
        self.assertEqual(rev["candle"]["high"], 102.5)
        self.assertEqual(r["final"]["candles"][1]["high"], 102.5)

    def test_replay_is_repeatable_and_read_only(self):
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        first, second = self.run_replay(), self.run_replay()
        self.assertEqual(first, second)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), digest)
        with self.assertRaises(sqlite3.OperationalError):               # mode=ro refuses writes
            self.ro.execute("DELETE FROM observations")

    def test_empty_selection(self):
        r = replay(self.ro, "TEST", ms("16:00:00"), ms("16:05:00"))
        self.assertTrue(r["empty"])
        self.assertEqual((r["steps"], r["observation_count"]), ([], 0))
        self.assertTrue(replay(self.ro, "NOPE", ms("14:00:00"), ms("16:00:00"))["empty"])

    def test_five_minute_replay_and_sessions(self):
        r = self.run_replay("5m")
        self.assertEqual(r["final"]["candles"][0]["timestamp"], "2026-11-09T14:55:00Z")
        self.assertEqual(r["final"]["candles"][1]["timestamp"], "2026-11-09T15:00:00Z")
        self.assertEqual(sessions(self.ro)[0]["observations"], 6)


if __name__ == "__main__":
    unittest.main()
