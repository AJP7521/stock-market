"""Prompt 9C: long-only paper account (TEST DATA in a disposable SQLite file).

Expected sequence, zero fees, starting cash $1,000:
  buy 4 at $100   -> cash $600,   4 shares
  sell 1 at $110  -> cash $710,   3 shares
  close at $105   -> cash $1,025, 0 shares

Run from the project root:
    python3 -m unittest backend.tests.test_paper -v
"""

import tempfile
import unittest
from pathlib import Path

from backend.paper import PaperError, account_state, execute, fill_paper_orders, open_account
from backend.recorder import connect, record

SYMBOL = "TESTPAPER"


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test-paper.sqlite"
        self.conn = connect(self.path)
        open_account(self.conn, "test", 1000)
        self.clock = 1_000_000                      # milliseconds; advanced by each step
        self.volume = 0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def tick(self, ms=1000):
        self.clock += ms
        return self.clock

    def order(self, action, quantity=None, request_id=None):
        return execute(self.conn, "test", SYMBOL, action, quantity, request_id or f"req-{self.tick()}",
                       submitted_ms=self.clock)

    def price(self, p):
        """Deliver a TEST observation received now and let it fill pending orders."""
        self.volume += 10
        t = self.tick()
        r = record(self.conn, {"id": SYMBOL, "price": p, "time": str(t), "day_volume": str(self.volume)}, t,
                   source="test")
        return fill_paper_orders(self.conn, r["observation_id"])

    def state(self):
        s = account_state(self.conn, "test")
        return s["cash"], s["positions"].get(SYMBOL, 0), s["fill_count"]

    def test_buy_sell_close_sequence(self):
        self.assertEqual(self.order("buy", 4)["order"]["status"], "pending")
        self.assertEqual(self.state(), (1000.0, 0, 0))            # nothing changes until a price arrives
        self.price(100)
        self.assertEqual(self.state(), (600.0, 4, 1))
        self.order("sell", 1)
        self.price(110)
        self.assertEqual(self.state(), (710.0, 3, 2))
        self.order("close")
        self.price(105)
        self.assertEqual(self.state(), (1025.0, 0, 3))

        fills = self.conn.execute("SELECT side, quantity, price, observation_id, cash_after_micros, position_after "
                                  "FROM paper_fills ORDER BY id").fetchall()
        self.assertEqual([tuple(f) for f in fills], [("buy", 4, 100.0, 1, 600_000_000, 4),
                                                     ("sell", 1, 110.0, 2, 710_000_000, 3),
                                                     ("close", 3, 105.0, 3, 1_025_000_000, 0)])

    def test_fills_persist_after_reopening(self):
        self.test_buy_sell_close_sequence()
        self.conn.close()
        self.conn = connect(self.path)                            # as after a backend restart
        self.assertEqual(self.state(), (1025.0, 0, 3))

    def test_retry_with_same_request_id_creates_nothing(self):
        first = self.order("buy", 4, request_id="same")
        again = self.order("buy", 4, request_id="same")          # retried while pending
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["order"]["id"], first["order"]["id"])
        self.price(100)
        after_fill = self.order("buy", 4, request_id="same")     # retried after the fill
        self.assertTrue(after_fill["duplicate"])
        self.assertEqual(after_fill["order"]["fill"]["price"], 100.0)
        self.price(101)                                           # another price must not fill anything more
        self.assertEqual(self.state(), (600.0, 4, 1))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0], 1)

    def test_sell_from_empty_account_is_rejected(self):
        r = self.order("sell", 1)
        self.assertEqual(r["order"]["status"], "rejected")
        self.assertEqual(r["order"]["reason"], f"cannot sell 1 {SYMBOL}: only 0 shares available")
        self.assertEqual(self.order("close")["order"]["reason"], f"no open {SYMBOL} position to close")
        self.price(100)
        self.assertEqual(self.state(), (1000.0, 0, 0))

    def test_invalid_quantities(self):
        for q in (0, -1, 2.5, "3", None, True):
            r = self.order("buy", q)
            self.assertEqual(r["order"]["status"], "rejected", q)
            self.assertIn("positive whole number", r["order"]["reason"])
        with self.assertRaises(PaperError):
            self.order("short", 1)

    def test_hold_and_wait_create_no_order(self):
        self.assertIsNone(self.order("wait")["order"])
        self.order("buy", 2)
        self.price(100)
        r = self.order("hold")
        self.assertIsNone(r["order"])
        self.assertIn("keeping 2", r["note"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0], 1)

    def test_only_a_price_received_after_submission_fills(self):
        self.price(99)                                           # arrives before the order
        self.order("buy", 1)
        self.assertEqual(self.state(), (1000.0, 0, 0))
        self.price(100)
        self.assertEqual(self.state(), (900.0, 1, 1))

    def test_cash_rechecked_at_fill(self):
        self.order("buy", 4)                                     # accepted: price unknown at submission
        self.price(300)                                          # 4 x 300 = 1200 > 1000
        cash, held, fills = self.state()
        self.assertEqual((cash, held, fills), (1000.0, 0, 0))
        order = account_state(self.conn, "test")["orders"][0]
        self.assertEqual(order["status"], "rejected")
        self.assertIn("insufficient cash at fill", order["reason"])

    def test_pending_sales_reserve_shares(self):
        self.order("buy", 3)
        self.price(100)
        self.assertEqual(self.order("sell", 2)["order"]["status"], "pending")
        r = self.order("sell", 2)                                # only 1 left after the pending sale
        self.assertEqual(r["order"]["reason"], f"cannot sell 2 {SYMBOL}: only 1 shares available")


if __name__ == "__main__":
    unittest.main()
