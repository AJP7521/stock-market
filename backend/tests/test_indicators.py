"""Prompt 9A: SMA and crossover checks against the hand calculation.

Closes 13, 10, 7, 13, 19, 7, 1 with windows 2 and 3 (TEST DATA):
  candle 3: 8.5 / 10 (first comparable pair -> side "below", no signal)
  candle 4: 10 / 10 tie (side kept)
  candle 5: 16 / 13 upward crossover
  candle 6: 13 / 13 tie (side kept)
  candle 7: 4 / 9 downward crossover

Run from the project root:
    python3 -m unittest backend.tests.test_indicators -v
"""

import tempfile
import unittest
from pathlib import Path

from backend.fixtures import SMA_SYMBOL, write_sma_fixture
from backend.indicators import sma_crossover, validate_windows
from backend.replay import connect_readonly, replay
from backend.tests.test_candles import ms

CLOSES = [13, 10, 7, 13, 19, 7, 1]
CANDLES = [{"timestamp": f"2026-11-09T15:0{i}:00Z", "close": c, "complete": True} for i, c in enumerate(CLOSES)]
EXPECTED = [  # (short, long, side, signal)
    (None, None, None, None),
    (11.5, None, None, None),
    (8.5, 10.0, "below", None),
    (10.0, 10.0, "below", None),
    (16.0, 13.0, "above", "up"),
    (13.0, 13.0, "above", None),
    (4.0, 9.0, "below", "down"),
]


def as_tuples(points):
    return [(p["short"], p["long"], p["side"], p["signal"]) for p in points]


class SmaTests(unittest.TestCase):
    def test_full_run_matches_hand_calculation(self):
        r = sma_crossover(CANDLES, 2, 3)
        self.assertEqual(as_tuples(r["points"]), EXPECTED)
        self.assertEqual([(c["timestamp"][11:16], c["signal"]) for c in r["crossovers"]],
                         [("15:04", "up"), ("15:06", "down")])

    def test_revealing_one_candle_at_a_time_matches_full_run(self):
        full = sma_crossover(CANDLES, 2, 3)
        signals = []
        for k in range(1, len(CANDLES) + 1):
            partial = sma_crossover(CANDLES[:k], 2, 3)
            self.assertEqual(partial["points"], full["points"][:k])        # every value, gaps included
            signals += [c for c in partial["crossovers"] if c not in signals]
        self.assertEqual(signals, full["crossovers"])

    def test_unfinished_candle_cannot_confirm_a_crossover(self):
        forming = {"timestamp": "2026-11-09T15:07:00Z", "close": 30, "complete": False}
        r = sma_crossover(CANDLES + [forming], 2, 3)
        self.assertEqual(len(r["crossovers"]), 2)
        self.assertEqual((r["points"][-1]["short"], r["points"][-1]["signal"]), (None, None))
        # The same close as a completed candle would cross upward: (1+30)/2 = 15.5 > (7+1+30)/3.
        done = sma_crossover(CANDLES + [dict(forming, complete=True)], 2, 3)
        self.assertEqual(done["crossovers"][-1]["signal"], "up")

    def test_exact_comparison_before_rounding(self):
        # Windows 1 and 3. Candle 3: 0.2 > 0.1333 -> "above". Candle 4: SMA(1) = 0.15 and
        # SMA(3) = (0.1 + 0.2 + 0.15) / 3 = 0.15 exactly: a tie, so the side stays "above".
        # Naive binary floats give 0.15000000000000002 > 0.15 and would report a false "down".
        self.assertGreater((0.1 + 0.2 + 0.15) / 3, 0.15)
        closes = [0.1, 0.1, 0.2, 0.15]
        c = [{"timestamp": f"2026-11-09T15:0{i}:00Z", "close": x} for i, x in enumerate(closes)]
        r = sma_crossover(c, 1, 3)
        self.assertEqual([p["side"] for p in r["points"]], [None, None, "above", "above"])
        self.assertEqual(r["crossovers"], [])

    def test_window_validation(self):
        for short, long in [(0, 3), (-1, 3), (3, 3), (4, 3), (2.5, 3), (2, "3")]:
            with self.assertRaises(ValueError):
                validate_windows(short, long)
        validate_windows(2, 3)


class SmaReplayTests(unittest.TestCase):
    """Replay the labelled fixture one observation at a time and compare with the full run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "test-sma.sqlite"
        write_sma_fixture(path)
        self.ro = connect_readonly(path)

    def tearDown(self):
        self.ro.close()
        self.tmp.cleanup()

    def test_replay_steps_match_full_calculation(self):
        r = replay(self.ro, SMA_SYMBOL, ms("15:00:00"), ms("15:08:00"), "1m", sma=(2, 3))
        self.assertEqual(len(r["steps"]), 8)
        full_points = r["final"]["sma"]["points"]
        self.assertEqual(as_tuples(full_points[:7]), EXPECTED)
        self.assertEqual(full_points[7]["short"], None)                  # 15:07 still forming
        self.assertEqual([c["signal"] for c in r["final"]["sma"]["crossovers"]], ["up", "down"])
        # At each step only completed candles count: step k (k >= 2) completes candle k-1.
        for k, step in enumerate(r["steps"], start=1):
            completed = k - 1
            self.assertEqual(as_tuples(step["sma"]["points"][:completed]), EXPECTED[:completed])
            self.assertEqual(len(step["sma"]["crossovers"]), (completed >= 5) + (completed >= 7))


if __name__ == "__main__":
    unittest.main()
