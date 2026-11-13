"""Recorder storage rules: duplicates, changed messages, identified revisions and out-of-order arrivals.

TEST DATA in a disposable SQLite file; symbol TEST on 2026-11-09. These test `recorder.record()` itself (what is stored)
and the candle that results; the replay side of the same cases is in test_replay.py.

    python3 -m unittest backend.tests.test_recorder -v
"""

import tempfile
import unittest
from pathlib import Path

from backend.candles import minute_candles
from backend.recorder import connect, record
from backend.tests.test_candles import ms, msg


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "test-recorder.sqlite")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def rows(self, sql, args=()):
        return [tuple(r) for r in self.conn.execute(sql, args)]

    def candle(self, minute, as_of):
        return next((i for i in minute_candles(self.conn, "TEST", ms(as_of))
                     if i["candle"]["timestamp"] == f"2026-11-09T{minute}:00Z"), None)

    def test_exact_duplicate_adds_a_receipt_not_an_observation(self):
        first = record(self.conn, msg("15:00:05", 100, 1000), ms("15:00:05") + 200, source="test")
        again = record(self.conn, msg("15:00:05", 100, 1000), ms("15:00:09"), source="test")
        self.assertEqual((first["is_repeat"], again["is_repeat"]), (False, True))
        self.assertEqual(first["observation_id"], again["observation_id"])
        self.assertEqual(self.rows("SELECT count(*), receipt_count FROM observations"), [(1, 2)])
        self.assertEqual(self.rows("SELECT observation_id, received_at_ms, is_repeat FROM receipts ORDER BY seq"),
                         [(1, ms("15:00:05") + 200, 0), (1, ms("15:00:09"), 1)])
        self.assertEqual(self.candle("15:00", "15:01:00")["status"]["observation_count"], 1)   # counted once

    def test_same_content_for_another_ticker_is_a_separate_observation(self):
        record(self.conn, msg("15:00:05", 100, 1000), ms("15:00:06"), source="test")
        record(self.conn, {**msg("15:00:05", 100, 1000), "id": "OTHER"}, ms("15:00:06"), source="test")
        self.assertEqual(self.rows("SELECT ticker FROM observations ORDER BY id"), [("TEST",), ("OTHER",)])

    def test_changed_message_with_same_source_time_keeps_both(self):
        record(self.conn, msg("15:00:05", 100, 1000), ms("15:00:06"), source="test")
        record(self.conn, msg("15:00:05", 101, 1003), ms("15:00:07"), source="test")
        self.assertEqual(self.rows("SELECT price, revises_id FROM observations ORDER BY id"),
                         [(100.0, None), (101.0, None)])
        c = self.candle("15:00", "15:01:00")["candle"]
        self.assertEqual((c["open"], c["close"], c["high"]), (100.0, 101.0, 101.0))   # both used, arrival order

    def test_identified_revision_is_new_row_and_original_is_unchanged(self):
        original = record(self.conn, msg("15:00:20", 103, 1009), ms("15:00:21"), source="test")["observation_id"]
        record(self.conn, msg("15:00:40", 101, 1011), ms("15:00:41"), source="test")
        before = self.rows("SELECT * FROM observations WHERE id = ?", (original,))
        revision = record(self.conn, msg("15:00:20", 102, 1009), ms("15:01:20"), source="test",
                          revises_id=original)["observation_id"]
        self.assertEqual(self.rows("SELECT * FROM observations WHERE id = ?", (original,)), before)  # untouched
        self.assertEqual(self.rows("SELECT revises_id, price FROM observations WHERE id = ?", (revision,)),
                         [(original, 102.0)])
        self.assertEqual(self.candle("15:00", "15:01:10")["candle"]["high"], 103.0)   # before the revision arrived
        self.assertEqual(self.candle("15:00", "15:01:30")["candle"]["high"], 102.0)   # after: revision replaces it

    def test_out_of_order_arrival_keeps_source_and_receipt_order_apart(self):
        # 15:00:40 arrives first; the older 15:00:10 observation arrives late, after the minute has ended.
        record(self.conn, msg("15:00:40", 105, 1010), ms("15:00:41"), source="test")
        record(self.conn, msg("15:00:10", 100, 1004), ms("15:01:05"), source="test")
        self.assertEqual(self.rows("SELECT id, source_time_ms, first_received_ms FROM observations ORDER BY id"),
                         [(1, ms("15:00:40"), ms("15:00:41")), (2, ms("15:00:10"), ms("15:01:05"))])
        self.assertEqual(self.rows("SELECT observation_id FROM receipts ORDER BY seq"), [(1,), (2,)])
        early = self.candle("15:00", "15:01:00")              # complete, but the late observation not yet received
        self.assertEqual((early["status"]["complete"], early["candle"]["open"]), (True, 105.0))
        late = self.candle("15:00", "15:01:10")               # recomputed: placed by source time
        self.assertEqual((late["candle"]["open"], late["candle"]["close"], late["status"]["observation_count"]),
                         (100.0, 105.0, 2))

    def test_completeness_is_reproducible_for_an_as_of_time(self):
        record(self.conn, msg("15:00:05", 100, 1000), ms("15:00:06"), source="test")
        self.assertFalse(self.candle("15:00", "15:00:30")["status"]["complete"])
        self.assertTrue(self.candle("15:00", "15:01:00")["status"]["complete"])
        self.assertEqual(self.candle("15:00", "15:00:30"), self.candle("15:00", "15:00:30"))   # same as-of, same answer


if __name__ == "__main__":
    unittest.main()
