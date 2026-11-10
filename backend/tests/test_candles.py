"""One-minute candle checks for Prompt 7, using TEST DATA in a disposable SQLite file.

Run from the project root:
    python3 -m unittest backend.tests.test_candles -v
"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from backend.candles import minute_candles
from backend.recorder import connect, record


def ms(hms: str, day: str = "2026-11-09") -> int:
    """'15:00:05' on the given UTC day -> epoch milliseconds."""
    return round(datetime.fromisoformat(f"{day}T{hms}+00:00").timestamp() * 1000)


def msg(hms: str, price: float, cum: int | None, day: str = "2026-11-09", **extra) -> dict:
    m = {"id": "TEST", "price": price, "time": str(ms(hms, day)), **extra}
    if cum is not None:
        m["day_volume"] = str(cum)
    return m


# Prompt 7 test observations (UTC, 2026-11-09); volume is the running session total.
PROMPT_ROWS = [("14:59:59", 99, 1000), ("15:00:05", 100, 1004), ("15:00:20", 103, 1009), ("15:00:50", 101, 1011)]
EXPECTED_1500 = {"timestamp": "2026-11-09T15:00:00Z", "open": 100.0, "high": 103.0, "low": 100.0,
                 "close": 101.0, "volume": 11}


class MinuteCandleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "test-candles.sqlite")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add(self, hms, price, cum, received_hms=None, day="2026-11-09", **kw):
        received = ms(received_hms or hms, day) + 300  # arrives 0.3 s after source time unless delayed
        return record(self.conn, msg(hms, price, cum, day), received, source="test", **kw)

    def candle_at(self, minute: str, as_of_hms: str, day="2026-11-09"):
        for item in minute_candles(self.conn, "TEST", ms(as_of_hms, day)):
            if item["candle"]["timestamp"] == f"{day}T{minute}:00Z":
                return item
        return None

    def test_prompt_example_before_and_after_minute_end(self):
        for row in PROMPT_ROWS:
            self.add(*row)
        before = self.candle_at("15:00", "15:00:30")
        self.assertEqual(before["candle"], {"timestamp": "2026-11-09T15:00:00Z", "open": 100.0, "high": 103.0,
                                            "low": 100.0, "close": 103.0, "volume": 9})
        self.assertFalse(before["status"]["complete"])

        after = self.candle_at("15:00", "15:01:00")
        self.assertEqual(after["candle"], EXPECTED_1500)
        self.assertEqual(list(after["candle"]), ["timestamp", "open", "high", "low", "close", "volume"])
        self.assertTrue(after["status"]["complete"])
        self.assertEqual(after["status"]["observation_count"], 3)

    def test_middle_observation_delivered_late(self):
        self.add("14:59:59", 99, 1000)
        self.add("15:00:05", 100, 1004)
        self.add("15:00:50", 101, 1011)
        self.add("15:00:20", 103, 1009, received_hms="15:01:10")   # middle observation arrives late

        ended_without = self.candle_at("15:00", "15:01:05")
        self.assertEqual(ended_without["candle"], {**EXPECTED_1500, "high": 101.0})
        self.assertTrue(ended_without["status"]["complete"])

        final = self.candle_at("15:00", "15:01:15")
        self.assertEqual(final["candle"], EXPECTED_1500)
        self.assertEqual(final["status"]["last_received_time"], "2026-11-09T15:01:10.300Z")

    def test_identified_correction_replaces_original(self):
        for row in PROMPT_ROWS:
            self.add(*row)
        original = self.conn.execute("SELECT id FROM observations WHERE price = 103").fetchone()[0]
        self.add("15:00:20", 102, 1009, received_hms="15:01:20", revises_id=original)
        self.assertEqual(self.candle_at("15:00", "15:01:10")["candle"]["high"], 103.0)
        self.assertEqual(self.candle_at("15:00", "15:01:30")["candle"]["high"], 102.0)

    def test_volume_unknown_without_baseline(self):
        for row in PROMPT_ROWS[1:]:   # no 14:59:59 observation
            self.add(*row)
        item = self.candle_at("15:00", "15:01:00")
        self.assertIsNone(item["candle"]["volume"])
        self.assertFalse(item["status"]["volume_known"])
        self.assertIn("no earlier running total", item["status"]["volume_note"])
        self.assertEqual(item["candle"]["close"], 101.0)

    def test_volume_unknown_after_reset(self):
        self.add("14:59:59", 99, 1000)
        self.add("15:00:05", 100, 1004)
        self.add("15:00:20", 103, 5)       # running total drops: session reset
        self.add("15:01:10", 104, 8)
        self.assertIsNone(self.candle_at("15:00", "15:02:00")["candle"]["volume"])
        self.assertEqual(self.candle_at("15:01", "15:02:00")["candle"]["volume"], 3)  # baseline restarts at 5

    def test_volume_unknown_after_gap(self):
        self.add("14:57:30", 98, 900)      # last running total is two minutes before 15:00
        self.add("15:00:05", 100, 1004)
        self.add("15:00:50", 101, 1011)
        item = self.candle_at("15:00", "15:01:00")
        self.assertIsNone(item["candle"]["volume"])
        self.assertIn("gap", item["status"]["volume_note"])

    def test_known_zero_volume_differs_from_unknown(self):
        self.add("14:59:59", 99, 1000)
        self.add("15:00:30", 99, 1000)
        item = self.candle_at("15:00", "15:01:00")
        self.assertEqual(item["candle"]["volume"], 0)
        self.assertTrue(item["status"]["volume_known"])

    def test_new_session_has_no_baseline(self):
        self.add("20:59:00", 99, 1000)                                  # 15:59 ET on Nov 9
        self.add("14:30:10", 101, 50, day="2026-11-10")                 # next session
        self.add("14:30:40", 102, 60, day="2026-11-10")
        item = self.candle_at("14:30", "14:31:00", day="2026-11-10")
        self.assertIsNone(item["candle"]["volume"])


if __name__ == "__main__":
    unittest.main()
