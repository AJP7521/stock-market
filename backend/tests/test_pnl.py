"""Prompt 9D: P&L history with weighted average cost (TEST DATA, disposable SQLite).

Expected rows, zero fees, starting cash $1,000:
  Event                      Cash   Shares  Realized  Unrealized  Total
  Buy 4 at $100               600      4        0          0         0
  Value the shares at $110    600      4        0         40        40
  Sell 1 at $110              710      3       10         30        40
  Value the shares at $95     710      3       10        -15        -5
  Close the last 3 at $105   1025      0       25          0        25

Timeline (UTC, 2026-11-09; every observation is labelled source='test'):
  15:00:05 buy 4 submitted   15:00:10 price 100 (fills)   15:00:50 price 110 (15:00 candle closes at 110)
  15:01:05 sell 1 submitted  15:01:10 price 110 (first values the 15:00 candle, then fills the sale)
  15:01:50 price 95 (15:01 candle closes at 95)
  15:02:05 close submitted   15:02:10 price 105 (values the 15:01 candle, then fills the close)

Run from the project root:
    python3 -m unittest backend.tests.test_pnl -v
"""

import tempfile
import unittest
from pathlib import Path

from backend.paper import execute, fill_paper_orders, open_account
from backend.pnl import history, record_fill, record_valuation, value_open_positions
from backend.recorder import connect, record
from backend.tests.test_candles import ms

SYMBOL = "TESTPNL"
EXPECTED = [  # (event, cash, shares, realized, unrealized, total)
    ("fill", 600.0, 4, 0.0, 0.0, 0.0),
    ("valuation", 600.0, 4, 0.0, 40.0, 40.0),
    ("fill", 710.0, 3, 10.0, 30.0, 40.0),
    ("valuation", 710.0, 3, 10.0, -15.0, -5.0),
    ("fill", 1025.0, 0, 25.0, 0.0, 25.0),
]


def table(rows):
    return [(r["event"], r["cash"], r["shares"], r["realized"], r["unrealized"], r["total"]) for r in rows]


class PnlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test-pnl.sqlite"
        self.conn = connect(self.path)
        open_account(self.conn, "test", 1000)
        self.volume = 0
        self.obs_ids = []

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def submit(self, hms, action, qty=None):
        return execute(self.conn, "test", SYMBOL, action, qty, f"req-{hms}", submitted_ms=ms(hms))

    def price(self, hms, p):
        self.volume += 10
        t = ms(hms)
        r = record(self.conn, {"id": SYMBOL, "price": p, "time": str(t), "day_volume": str(self.volume)}, t,
                   source="test")
        self.obs_ids.append(r["observation_id"])
        return fill_paper_orders(self.conn, r["observation_id"])

    def run_example(self):
        self.submit("15:00:05", "buy", 4)
        self.price("15:00:10", 100)
        self.price("15:00:50", 110)
        self.submit("15:01:05", "sell", 1)
        self.price("15:01:10", 110)
        self.price("15:01:50", 95)
        self.submit("15:02:05", "close")
        self.price("15:02:10", 105)

    def test_example_table(self):
        self.run_example()
        rows = history(self.conn, "test", SYMBOL)
        self.assertEqual(table(rows), EXPECTED)
        # Each row links to its event, time and price source.
        self.assertEqual([r["price"] for r in rows], [100.0, 110.0, 110.0, 95.0, 105.0])
        self.assertEqual(rows[1]["price_source"], "1m candle close 2026-11-09T15:00:00Z")
        self.assertEqual(rows[1]["price_time"], "2026-11-09T15:01:00.000Z")
        self.assertEqual(rows[3]["price_source"], "1m candle close 2026-11-09T15:01:00Z")
        self.assertTrue(rows[0]["price_source"].startswith("fill price (paper fill #1, observation #1"))
        self.assertEqual([r["average_cost"] for r in rows], [100.0, 100.0, 100.0, 100.0, None])

    def test_reprocessing_keeps_one_entry_per_event(self):
        self.run_example()
        before = table(history(self.conn, "test", SYMBOL))
        for fill_id in (1, 2, 3):
            with self.conn:
                self.assertFalse(record_fill(self.conn, fill_id))
        for obs_id in self.obs_ids:                       # replay every observation again
            fill_paper_orders(self.conn, obs_id)
        value_open_positions(self.conn, SYMBOL, ms("15:05:00"))
        self.assertEqual(table(history(self.conn, "test", SYMBOL)), before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0], 3)

    def test_rows_survive_reopening(self):
        self.run_example()
        self.conn.close()
        self.conn = connect(self.path)
        self.assertEqual(table(history(self.conn, "test", SYMBOL)), EXPECTED)

    def test_partial_sale_keeps_average_cost(self):
        self.submit("15:00:01", "buy", 3)
        self.price("15:00:02", 100)
        self.submit("15:00:03", "buy", 1)
        self.price("15:00:04", 104)                       # average cost (300 + 104) / 4 = 101
        self.submit("15:00:05", "sell", 2)
        self.price("15:00:06", 105)                       # realized 2 x (105 - 101) = 8
        last = history(self.conn, "test", SYMBOL)[-1]
        self.assertEqual((last["shares"], last["average_cost"], last["realized"], last["unrealized"]),
                         (2, 101.0, 8.0, 8.0))            # unrealized 2 x (105 - 101)

    def test_unavailable_valuation_is_labelled(self):
        self.submit("15:00:05", "buy", 2)
        self.price("15:00:10", 100)
        self.assertTrue(record_valuation(self.conn, "test", SYMBOL, None, ms("15:03:00")))
        last = history(self.conn, "test", SYMBOL)[-1]
        self.assertEqual((last["valuation"], last["price"], last["unrealized"], last["total"]),
                         ("unavailable", None, None, None))
        self.assertEqual(len(history(self.conn, "test", SYMBOL)), 2)   # the fill row is kept

    def test_forming_candle_does_not_value(self):
        self.submit("15:00:05", "buy", 1)
        self.price("15:00:10", 100)
        self.price("15:00:40", 120)                       # 15:00 candle still forming
        self.assertEqual([r["event"] for r in history(self.conn, "test", SYMBOL)], ["fill"])


if __name__ == "__main__":
    unittest.main()
