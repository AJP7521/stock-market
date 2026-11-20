"""Prompt 17: SMA crossover backtest (TEST DATA, disposable SQLite).

Hand calculation for the TESTBT fixture (backend/fixtures.py), SMA(2)/SMA(4), $1,000, fills at the next open:
  signal close   fill (next open)          cash after   shares   realized
  15:05 up       buy 48 @ 20.5   (15:06)       16.00       48       0.00
  15:08 down     sell 48 @ 21    (15:09)    1,024.00        0      24.00
  15:11 up       buy 47 @ 21.5   (15:12)       13.50       47      24.00
  15:13 down     sell 47 @ 18.5  (15:14)      883.00        0    -117.00
  15:16 up       buy 39 @ 22.5   (15:17)        5.50       39    -117.00
  last close 24: equity 941.50 (-5.85%), unrealized 58.50; buy and hold 50 @ 20 -> 1,200 (+20%);
  peak 1,120 (15:06 close 23), trough 859.50 (15:13 close 18): drawdown 260.50 = 23.2589%; 1 of 2 trips won;
  shares held at 6 of 18 closes.

Run from the project root:
    python3 -m unittest backend.tests.test_backtest -v
"""

import tempfile
import unittest
from pathlib import Path

from backend.backtest import BacktestError, decide, get_run, list_runs, run_backtest, simulate
from backend.fixtures import BACKTEST_MINUTES, BACKTEST_SYMBOL, write_backtest_fixture
from backend.indicators import sma_crossover
from backend.pnl import apply_fill
from backend.recorder import connect, record
from backend.tests.test_candles import ms

M = 1_000_000
EXPECTED_ORDERS = [  # (signal candle, side, qty, fill candle, price, cash after, shares after, realized after)
    ("15:05", "buy", 48, "15:06", 20.5, 16.0, 48, 0.0),
    ("15:08", "sell", 48, "15:09", 21.0, 1024.0, 0, 24.0),
    ("15:11", "buy", 47, "15:12", 21.5, 13.5, 47, 24.0),
    ("15:13", "sell", 47, "15:14", 18.5, 883.0, 0, -117.0),
    ("15:16", "buy", 39, "15:17", 22.5, 5.5, 39, -117.0),
]
AS_OF = ms("15:30:00")


def candles(minutes=BACKTEST_MINUTES):
    return [{"timestamp": f"2026-11-09T{m}:00Z", "open": o, "high": max(o, c), "low": min(o, c), "close": c,
             "volume": 100} for m, o, c in minutes]


class BacktestFixtureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "bt.sqlite"
        write_backtest_fixture(self.db)
        self.conn = connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_hand_calculation(self):
        run = run_backtest(self.conn, BACKTEST_SYMBOL, "1m", 2, 4, 1000, as_of_ms=AS_OF)
        got = [(o["signal_candle"][11:16], o["side"], o["quantity"], o["fill_candle"][11:16], o["price"],
                o["cash_after"], o["position_after"], o["realized_after"]) for o in run["orders"]]
        self.assertEqual(got, EXPECTED_ORDERS)
        self.assertTrue(all(o["status"] == "filled" for o in run["orders"]))
        s = run["summary"]
        self.assertEqual((s["final_equity"], s["final_cash"], s["final_shares"], s["realized"], s["unrealized"]),
                         (941.5, 5.5, 39, -117.0, 58.5))
        self.assertEqual((s["total_return_pct"], s["buy_hold_equity"], s["buy_hold_return_pct"]), (-5.85, 1200.0, 20.0))
        self.assertEqual((s["max_drawdown"], s["max_drawdown_pct"]), (260.5, 23.2589))
        self.assertEqual((s["round_trips"], s["wins"], s["exposure_candles"]), (2, 1, 6))
        self.assertEqual((run["candle_count"], run["excluded_count"], run["data_labels"]), (18, 0, "test"))
        self.assertEqual([t["pnl"] for t in run["trips"]], [24.0, -141.0, 58.5])
        self.assertEqual(run["trips"][-1]["status"], "open (valued at last close)")
        # Equity = cash + shares x close at every close; realized + unrealized = equity - starting cash.
        for e in run["equity"]:
            self.assertAlmostEqual(e["equity"], e["cash"] + e["shares"] * e["close"])
            self.assertAlmostEqual(e["realized"] + e["unrealized"], e["equity"] - 1000)

    def test_stored_once_and_read_back(self):
        run = run_backtest(self.conn, BACKTEST_SYMBOL, "1m", 2, 4, 1000, as_of_ms=AS_OF)
        self.assertEqual(get_run(self.conn, run["id"]), run)
        count = lambda t: self.conn.execute(f"SELECT COUNT(*) FROM {t} WHERE run_id = ?", (run["id"],)).fetchone()[0]
        self.assertEqual((count("backtest_fills"), count("backtest_equity")), (5, 18))
        self.assertEqual([r["id"] for r in list_runs(self.conn, BACKTEST_SYMBOL)], [run["id"]])

    def test_forming_candle_is_excluded(self):
        t = ms("15:18:10")
        record(self.conn, {"id": BACKTEST_SYMBOL, "price": 30, "time": str(t), "day_volume": "60000"}, t + 300,
               source="test")
        run = run_backtest(self.conn, BACKTEST_SYMBOL, "1m", 2, 4, 1000, as_of_ms=ms("15:18:30"))
        self.assertEqual((run["candle_count"], run["excluded_count"]), (18, 1))
        self.assertEqual(run["summary"]["final_equity"], 941.5)   # not valued at the forming price 30

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            run_backtest(self.conn, BACKTEST_SYMBOL, "1m", 4, 2, 1000, as_of_ms=AS_OF)
        with self.assertRaises(BacktestError):
            run_backtest(self.conn, BACKTEST_SYMBOL, "1m", 2, 30, 1000, as_of_ms=AS_OF)   # too few candles
        with self.assertRaises(BacktestError):
            run_backtest(self.conn, BACKTEST_SYMBOL, "1m", 2, 4, 0, as_of_ms=AS_OF)


class NoLookAheadTest(unittest.TestCase):
    def test_signals_equal_chart_crossovers(self):
        rows = candles()
        side, mine = None, []
        for i in range(len(rows)):
            side, signal = decide(rows[:i + 1], 2, 4, side)
            if signal:
                mine.append((rows[i]["timestamp"], signal))
        chart = [(c["timestamp"], c["signal"]) for c in sma_crossover(rows, 2, 4)["crossovers"]]
        self.assertEqual(mine, chart)

    def test_future_candles_do_not_change_earlier_decisions(self):
        rows = candles()
        full = simulate(rows, 2, 4, 1000 * M)
        for cut in range(5, len(rows)):
            cutoff = rows[cut]["timestamp"]
            # Remove everything after `cut`, or replace it with wild prices: orders decided by `cut` stay the same.
            for future in ([], [{**r, "open": 500, "close": 1, "high": 500, "low": 1} for r in rows[cut + 1:]]):
                other = simulate(rows[:cut + 1] + future, 2, 4, 1000 * M)
                decided = lambda res: [(o["signal_candle"], o["side"]) for o in res["orders"]
                                       if o["signal_candle"] <= cutoff]
                self.assertEqual(decided(other), decided(full), cutoff)
                self.assertEqual(other["equity"][:cut + 1], full["equity"][:cut + 1], cutoff)

    def test_fill_uses_next_open_not_signal_close(self):
        res = simulate(candles(), 2, 4, 1000 * M)
        first = res["orders"][0]
        self.assertEqual((first["signal_candle"][11:16], first["fill_candle"][11:16]), ("15:05", "15:06"))
        self.assertEqual(first["price_micros"], 20_500_000)   # 15:06 open, not the 15:05 close of 21

    def test_signal_on_last_candle_is_not_filled(self):
        res = simulate(candles(BACKTEST_MINUTES[:17]), 2, 4, 1000 * M)   # ends at 15:16, the last up signal
        last = res["orders"][-1]
        self.assertEqual((last["signal_candle"][11:16], last["status"]), ("15:16", "not_filled"))
        self.assertEqual(res["summary"]["final_shares"], 0)

    def test_cash_too_small_rejects_buy(self):
        res = simulate(candles(), 2, 4, 10 * M)   # $10 cannot buy one share at 20.5
        self.assertEqual(res["orders"][0]["status"], "rejected")
        self.assertEqual(res["summary"]["final_equity_micros"], 10 * M)


class ApplyFillTest(unittest.TestCase):
    def test_matches_paper_pnl_example(self):
        # test_pnl: buy 4 @ 100, sell 1 @ 110, close 3 @ 105 -> realized 10 then 25, basis 0 at the end.
        basis, realized = apply_fill(0, 0, 0, "buy", 4, 100 * M)
        self.assertEqual((basis, realized), (400 * M, 0))
        basis, realized = apply_fill(basis, realized, 4, "sell", 1, 110 * M)
        self.assertEqual((basis, realized), (300 * M, 10 * M))
        basis, realized = apply_fill(basis, realized, 3, "close", 3, 105 * M)
        self.assertEqual((basis, realized), (0, 25 * M))


if __name__ == "__main__":
    unittest.main()
