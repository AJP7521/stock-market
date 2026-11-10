"""Prompt 9B: indicator interface, rolling price range, stored configurations (TEST DATA).

Fixture candles (high, low): (12, 9), (15, 10), (13, 11).
  Range 2 at candle 3 = max(15, 13) - min(10, 11) = 5
  Range 3 at candle 3 = max(12, 15, 13) - min(9, 10, 11) = 6

Run from the project root:
    python3 -m unittest backend.tests.test_indicator_types -v
"""

import tempfile
import unittest
from pathlib import Path

from backend.candles import minute_candles
from backend.fixtures import RANGE_SYMBOL, write_range_fixture
from backend.indicator_store import add_config, list_configs
from backend.indicators import compute, describe_types, sma_crossover, validate_settings
from backend.recorder import connect

CANDLES = [{"timestamp": f"2026-11-09T15:0{i}:00Z", "open": 0, "high": h, "low": l, "close": c, "volume": 1}
           for i, (h, l, c) in enumerate([(12, 9, 11), (15, 10, 14), (13, 11, 12)])]


def values(points):
    return [p["value"] for p in points]


class IndicatorTypeTests(unittest.TestCase):
    def test_rolling_range(self):
        self.assertEqual(values(compute("price_range", CANDLES, {"window": 2})), [None, 6.0, 5.0])
        self.assertEqual(values(compute("price_range", CANDLES, {"window": 3})), [None, None, 6.0])
        self.assertEqual(values(compute("price_range", CANDLES, {"window": 1})), [3.0, 5.0, 2.0])

    def test_one_point_per_candle_and_forming_candle_is_a_gap(self):
        forming = dict(CANDLES[2], complete=False)
        pts = compute("price_range", CANDLES[:2] + [forming], {"window": 2})
        self.assertEqual([p["timestamp"] for p in pts], [c["timestamp"] for c in CANDLES])
        self.assertEqual(values(pts), [None, 6.0, None])
        self.assertEqual(pts[2]["note"], "candle still forming")

    def test_sma_reuses_the_same_interface_as_the_crossover(self):
        closes = [13, 10, 7, 13, 19, 7, 1]
        candles = [{"timestamp": f"2026-11-09T15:0{i}:00Z", "close": c} for i, c in enumerate(closes)]
        cross = sma_crossover(candles, 2, 3)["points"]
        self.assertEqual(values(compute("sma", candles, {"window": 2})), [p["short"] for p in cross])
        self.assertEqual(values(compute("sma", candles, {"window": 3})), [p["long"] for p in cross])

    def test_settings_validation(self):
        bad = [("price_range", {"window": 0}), ("price_range", {"window": -2}), ("price_range", {"window": 2.5}),
               ("price_range", {"window": "3"}), ("price_range", {"window": None}),
               ("price_range", {"window": 3, "colour": "red"}), ("nope", {"window": 3})]
        for type_key, settings in bad:
            with self.assertRaises(ValueError, msg=(type_key, settings)):
                validate_settings(type_key, settings)
        with self.assertRaisesRegex(ValueError, "must be at least 1, got 0"):
            validate_settings("price_range", {"window": 0})
        self.assertEqual(validate_settings("price_range", {}), {"window": 14})   # default applied

    def test_types_are_described_for_vue(self):
        types = {t["key"]: t for t in describe_types()}
        self.assertEqual(set(types), {"sma", "price_range"})
        self.assertNotIn("compute", types["price_range"])
        self.assertEqual(types["price_range"]["settings"][0]["name"], "window")
        self.assertIn("price difference", types["price_range"]["units"])


class StoredConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test-range.sqlite"
        write_range_fixture(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fixture_candles(self):
        with connect(self.path) as conn:
            candles = [c["candle"] for c in minute_candles(conn, RANGE_SYMBOL)]
        self.assertEqual([(c["high"], c["low"]) for c in candles], [(12, 9), (15, 10), (13, 11)])

    def test_configs_persist_and_invalid_one_is_not_stored(self):
        conn = connect(self.path)
        add_config(conn, RANGE_SYMBOL, "Range 2", "price_range", {"window": 2})
        add_config(conn, RANGE_SYMBOL, "Range 3", "price_range", {"window": 3})
        with self.assertRaisesRegex(ValueError, "at least 1"):
            add_config(conn, RANGE_SYMBOL, "Range 0", "price_range", {"window": 0})
        with self.assertRaisesRegex(ValueError, "already has an indicator named"):
            add_config(conn, RANGE_SYMBOL, "Range 2", "price_range", {"window": 4})
        conn.close()

        reopened = connect(self.path)                       # a new connection, as after a restart
        configs = list_configs(reopened, RANGE_SYMBOL)
        candles = [{**c["candle"], "complete": c["status"]["complete"]} for c in minute_candles(reopened, RANGE_SYMBOL)]
        reopened.close()
        self.assertEqual([(c["name"], c["settings"]) for c in configs],
                         [("Range 2", {"window": 2}), ("Range 3", {"window": 3})])
        third = [compute(c["type"], candles, c["settings"])[2]["value"] for c in configs]
        self.assertEqual(third, [5.0, 6.0])


if __name__ == "__main__":
    unittest.main()
