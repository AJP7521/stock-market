"""Prompt 17A: rule-based paper automation (fixed SMA crossover rule, no model), with explicit times.

TEST DATA: symbol TESTAUTO, labelled test observations on 2026-11-09, one per minute from 15:00 with closes
13, 10, 7, 13, 19 (SMA2/SMA3 cross upward at the 15:04 candle), then 20 at 15:05:20 and 10 at 15:06:20 (SMA2 15 <
SMA3 16.33: downward crossover at 15:06). Test data trades only in the disposable "paper-test" account ($1,000).

    python3 -m unittest backend.tests.test_automation_rule -v
"""

import tempfile
import unittest
from pathlib import Path

from backend import automation as auto
from backend.paper import account_state, fill_paper_orders, open_account
from backend.recorder import connect, record
from backend.tests.test_automation import CLOSES, LONG, SHORT, SYM, TF
from backend.tests.test_candles import ms


class RuleAutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "rule.sqlite")
        open_account(self.conn, "paper", 1000)
        self.day_volume = 0
        for hms, price in CLOSES:
            self.price(price, hms)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def price(self, price, hms):
        self.day_volume += 100
        obs = record(self.conn, {"id": SYM, "price": price, "time": str(ms(hms)), "day_volume": str(self.day_volume)},
                     ms(hms), source="test")
        return fill_paper_orders(self.conn, obs["observation_id"])

    def start(self, mode="rule", at="15:03:30"):
        return auto.start(self.conn, SYM, TF, SHORT, LONG, paper_account="paper", test_account="paper-test",
                          test_starting_cash=1000, mode=mode, at_ms=ms(at))

    def claim(self, minute, at):
        return auto.claim_run(self.conn, SYM, TF, SHORT, LONG, f"2026-11-09T15:0{minute}:00Z", order_size=1,
                              at_ms=ms(at))["run"]

    def acct(self):
        a = account_state(self.conn, "paper-test")
        return a["cash"], a["positions"].get(SYM, 0), a["fill_count"]

    def count(self, table):
        try:
            return self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        except Exception:
            return 0

    def test_up_buys_flat_holds_down_closes_without_a_model(self):
        st = self.start()
        self.assertEqual((st["mode"], st["account_id"]), ("rule", "paper-test"))
        run = self.claim(4, "15:05:02")                      # up crossover, flat -> buy 1, decided at claim time
        self.assertEqual((run["mode"], run["rule_action"], run["status"], run["decision_id"]),
                         ("rule", "buy", "order_pending", None))
        self.assertIn("crossover at the candle: up", run["reason"])
        self.price(20, "15:05:20")                           # next observed price fills it
        self.assertEqual(self.acct(), (980.0, 1, 1))
        self.assertEqual(auto.get_run(self.conn, run["id"])["status"], "filled")

        run = self.claim(5, "15:06:02")                      # no crossover, 1 share -> hold, no order
        self.assertEqual((run["rule_action"], run["status"], run["order_id"]), ("hold", "no_order", None))

        self.price(10, "15:06:20")
        run = self.claim(6, "15:07:02")                      # down crossover, shares held -> close
        self.assertEqual((run["rule_action"], run["status"]), ("close", "order_pending"))
        self.price(11, "15:07:20")
        self.assertEqual(self.acct(), (991.0, 0, 2))
        realized = self.conn.execute("SELECT realized_micros FROM paper_cost_basis WHERE account_id = 'paper-test'"
                                     ).fetchone()[0]
        self.assertEqual(realized, -9_000_000)                # sold 1 @ 11 after buying @ 20
        self.assertEqual(self.count("decisions"), 0)         # no model decision was ever made
        self.assertEqual([r["proposal"] for r in auto.runs(self.conn, SYM, TF, SHORT, LONG)], ["close", "hold", "buy"])
        self.assertEqual(account_state(self.conn, "paper")["cash"], 1000.0)   # main account untouched

    def test_same_candle_twice_gives_one_order(self):
        self.start()
        first = self.claim(4, "15:05:02")
        again = auto.claim_run(self.conn, SYM, TF, SHORT, LONG, "2026-11-09T15:04:00Z", order_size=1,
                               at_ms=ms("15:05:05"))
        self.assertFalse(again["created"])
        self.assertEqual(again["run"]["id"], first["id"])
        self.assertEqual(self.count("paper_orders"), 1)

    def test_stop_before_fill_rejects_the_order(self):
        self.start()
        run = self.claim(4, "15:05:02")
        auto.stop(self.conn, SYM, TF, SHORT, LONG, at_ms=ms("15:05:10"))
        self.price(20, "15:05:20")
        self.assertEqual(self.acct(), (1000.0, 0, 0))
        self.assertEqual(auto.get_run(self.conn, run["id"])["status"], "rejected")

    def test_too_old_at_fill_is_rejected(self):
        self.start()
        run = self.claim(4, "15:05:02")                      # decision time = 15:05:00 (candle end)
        self.price(20, "15:10:30")                           # 330 s later > DECISION_MAX_AGE_S (300)
        self.assertEqual(self.acct(), (1000.0, 0, 0))
        run = auto.get_run(self.conn, run["id"])
        self.assertEqual(run["status"], "rejected")
        self.assertIn("too old", run["reason"])

    def test_ai_mode_is_still_the_default(self):
        st = auto.start(self.conn, SYM, TF, SHORT, LONG, paper_account="paper", test_account="paper-test",
                        test_starting_cash=1000, at_ms=ms("15:03:30"))
        self.assertEqual(st["mode"], "ai")
        run = self.claim(4, "15:05:02")
        self.assertEqual((run["mode"], run["status"], run["rule_action"]), ("ai", "analysing", None))

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(auto.AutomationError):
            self.start(mode="auto")


if __name__ == "__main__":
    unittest.main()
