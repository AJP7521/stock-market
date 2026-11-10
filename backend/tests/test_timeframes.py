"""Prompt 7A checks: larger timeframes from one-minute candles, and the session calendar.

The three sample minutes come from prompts/data/sample-candles.csv (synthetic).
The extra minutes at 14:35 and on the next session are constructed TEST DATA.

Run from the project root:
    python3 -m unittest backend.tests.test_timeframes -v
"""

import unittest
from datetime import date

from backend.history import load_candles
from backend.market_calendar import early_closes, holidays, session_for
from backend.timeframes import aggregate, to_ms

SAMPLE = load_candles()
MINUTE_1435 = {"timestamp": "2026-11-09T14:35:00Z", "open": 104, "high": 106, "low": 103, "close": 105, "volume": 700}
NEXT_SESSION = {"timestamp": "2026-11-10T14:30:00Z", "open": 110, "high": 111, "low": 109, "close": 110, "volume": 400}
FIELDS = ["timestamp", "open", "high", "low", "close", "volume"]


class TimeframeTests(unittest.TestCase):
    def test_five_minute_from_sample(self):
        r = aggregate(SAMPLE, "5m")
        self.assertEqual(len(r["candles"]), 1)
        c, s = r["candles"][0]["candle"], r["candles"][0]["status"]
        self.assertEqual(list(c), FIELDS)
        self.assertEqual(c, {"timestamp": "2026-11-09T14:30:00Z", "open": 100.0, "high": 105.0,
                             "low": 99.0, "close": 104.0, "volume": 3550})
        self.assertEqual((s["minutes_present"], s["minutes_expected"], s["coverage_complete"]), (3, 5, False))
        self.assertTrue(s["volume_known"])

    def test_daily_from_sample_is_partial(self):
        c = aggregate(SAMPLE, "1d")["candles"][0]
        self.assertEqual(c["candle"]["timestamp"], "2026-11-09T14:30:00Z")      # 09:30 ET session open
        self.assertEqual(c["candle"]["volume"], 3550)
        self.assertEqual((c["status"]["minutes_present"], c["status"]["minutes_expected"]), (3, 390))

    def test_1435_starts_second_five_minute_but_same_day(self):
        five = aggregate(SAMPLE + [MINUTE_1435], "5m")["candles"]
        self.assertEqual([x["candle"]["timestamp"] for x in five], ["2026-11-09T14:30:00Z", "2026-11-09T14:35:00Z"])
        self.assertEqual(five[0]["candle"]["volume"], 3550)                      # unchanged
        self.assertEqual(five[1]["candle"], {**MINUTE_1435})
        day = aggregate(SAMPLE + [MINUTE_1435], "1d")["candles"]
        self.assertEqual(len(day), 1)
        self.assertEqual(day[0]["candle"], {"timestamp": "2026-11-09T14:30:00Z", "open": 100.0, "high": 106,
                                            "low": 99.0, "close": 105, "volume": 4250})
        self.assertEqual(day[0]["status"]["minutes_present"], 4)

    def test_next_session_starts_new_daily_candle(self):
        day = aggregate(SAMPLE + [MINUTE_1435, NEXT_SESSION], "1d")["candles"]
        self.assertEqual([x["candle"]["timestamp"] for x in day], ["2026-11-09T14:30:00Z", "2026-11-10T14:30:00Z"])
        self.assertEqual(day[1]["candle"]["volume"], 400)

    def test_fifteen_and_hourly_alignment(self):
        m = [{"timestamp": "2026-11-09T15:44:00Z", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
             {"timestamp": "2026-11-09T15:45:00Z", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 1},
             {"timestamp": "2026-11-09T20:59:00Z", "open": 3, "high": 3, "low": 3, "close": 3, "volume": 1}]
        self.assertEqual([x["candle"]["timestamp"] for x in aggregate(m, "15m")["candles"]],
                         ["2026-11-09T15:30:00Z", "2026-11-09T15:45:00Z", "2026-11-09T20:45:00Z"])
        hourly = aggregate(m, "1h")["candles"]
        self.assertEqual([x["candle"]["timestamp"] for x in hourly],          # 10:30 ET groups, last is 15:30 ET
                         ["2026-11-09T15:30:00Z", "2026-11-09T20:30:00Z"])
        self.assertEqual(hourly[1]["status"]["minutes_expected"], 30)         # 15:30–16:00 ET

    def test_revised_minute_replaces_earlier(self):
        revised = dict(SAMPLE[1], close=100.5, volume=900)
        c = aggregate(SAMPLE + [revised], "5m")["candles"][0]
        self.assertEqual(c["candle"]["volume"], 1200 + 900 + 1500)
        self.assertEqual(c["status"]["minutes_present"], 3)

    def test_unknown_volume_separate_from_zero_and_coverage(self):
        m = [dict(SAMPLE[0], volume=0), dict(SAMPLE[1], volume=None)]
        c = aggregate(m, "5m")["candles"][0]
        self.assertIsNone(c["candle"]["volume"])
        self.assertFalse(c["status"]["volume_known"])
        self.assertEqual(c["status"]["minutes_with_unknown_volume"], 1)
        zero = aggregate([dict(SAMPLE[0], volume=0)], "5m")["candles"][0]
        self.assertEqual(zero["candle"]["volume"], 0)
        self.assertTrue(zero["status"]["volume_known"])

    def test_as_of_uses_only_available_minutes(self):
        c = aggregate(SAMPLE, "5m", as_of_ms=to_ms("2026-11-09T14:32:00Z"))["candles"][0]
        self.assertEqual(c["candle"]["close"], 101.0)          # 14:32 has not ended yet
        self.assertEqual(c["candle"]["volume"], 2050)
        self.assertFalse(c["status"]["complete"])

    def test_outside_session_excluded(self):
        pre = {"timestamp": "2026-11-09T14:00:00Z", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 5}
        r = aggregate(SAMPLE + [pre], "1d")
        self.assertEqual(r["excluded_minutes"], 1)
        self.assertEqual(r["candles"][0]["candle"]["volume"], 3550)


class CalendarTests(unittest.TestCase):
    def test_2026_holidays(self):
        expected = {date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
                    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)}
        self.assertEqual(set(holidays(2026)), expected)

    def test_2026_early_closes(self):
        self.assertEqual(set(early_closes(2026)), {date(2026, 11, 27), date(2026, 12, 24)})
        _, close = session_for(date(2026, 11, 27))
        self.assertEqual(close.isoformat(), "2026-11-27T18:00:00+00:00")   # 13:00 EST

    def test_daylight_saving_moves_utc_open(self):
        self.assertEqual(session_for(date(2026, 3, 6))[0].isoformat(), "2026-03-06T14:30:00+00:00")  # EST
        self.assertEqual(session_for(date(2026, 3, 9))[0].isoformat(), "2026-03-09T13:30:00+00:00")  # EDT
        self.assertEqual(session_for(date(2026, 11, 2))[0].isoformat(), "2026-11-02T14:30:00+00:00")  # EST again

    def test_closed_days(self):
        self.assertIsNone(session_for(date(2026, 11, 26)))   # Thanksgiving
        self.assertIsNone(session_for(date(2026, 11, 7)))    # Saturday
        self.assertIsNone(session_for(date(2025, 1, 9)))     # special closure
        self.assertNotIn(date(2027, 12, 31), holidays(2027))  # Jan 1 2028 is Saturday: Dec 31 stays open

    def test_early_close_shortens_groups(self):
        m = [{"timestamp": "2026-11-27T17:45:00Z", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
        self.assertEqual(aggregate(m, "1d")["candles"][0]["status"]["minutes_expected"], 210)   # 09:30–13:00
        self.assertEqual(aggregate(m, "1h")["candles"][0]["status"]["minutes_expected"], 30)    # 12:30–13:00


if __name__ == "__main__":
    unittest.main()
