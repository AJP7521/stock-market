"""Prompt 12: paper automation from reviewed decisions, with explicit times and the provider test double.

TEST DATA: symbol TESTAUTO, one labelled test observation per minute on 2026-11-09 with closes 13, 10, 7, 13, 19 from
15:00 (SMA2/SMA3 cross upward at the 15:04 candle). Test-labelled data trades only in the disposable "paper-test"
account, starting with $1,000. Replies come from the labelled test double (no live model).

    python3 -m unittest backend.tests.test_automation -v
"""

import tempfile
import unittest
from pathlib import Path

from backend import automation as auto
from backend.chart_images import data_url
from backend.decisions import TestDoubleDecisions, create_snapshot, decide
from backend.paper import account_state, fill_paper_orders, open_account
from backend.recorder import connect, record
from backend.tests.test_candles import ms
from backend.tests.test_chart_decisions import make_png

SYM, TF, SHORT, LONG = "TESTAUTO", "1m", 2, 3
CLOSES = [("15:00:10", 13), ("15:01:10", 10), ("15:02:10", 7), ("15:03:10", 13), ("15:04:10", 19)]
APPROVE = "action=buy,crossover=up,evidence=sufficient,verdict=accept"


class StopDuringReply(TestDoubleDecisions):
    """Test double that presses Stop while the reviewer reply is pending."""

    def __init__(self, conn, answers):
        super().__init__(answers)
        self.conn = conn

    def send(self, body):
        if body["questions"][0]["name"] == "verdict":
            auto.stop(self.conn, SYM, TF, SHORT, LONG, at_ms=ms("15:05:03"))
        return super().send(body)


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test-auto.sqlite"
        self.conn = connect(self.path)
        open_account(self.conn, "paper", 1000)
        self.day_volume = 0
        for hms, price in CLOSES:
            self.price(price, hms)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def price(self, price, hms):
        """Record a labelled test observation received at `hms` and let it fill pending orders."""
        self.day_volume += 100
        obs = record(self.conn, {"id": SYM, "price": price, "time": str(ms(hms)), "day_volume": str(self.day_volume)},
                     ms(hms), source="test")
        return fill_paper_orders(self.conn, obs["observation_id"])

    def start(self, at="15:03:30"):
        return auto.start(self.conn, SYM, TF, SHORT, LONG, paper_account="paper", test_account="paper-test",
                          test_starting_cash=1000, at_ms=ms(at))

    def run_candle(self, answers=APPROVE, candle="2026-11-09T15:04:00Z", claim_at="15:05:02", apply_at="15:05:03",
                   provider=None):
        claimed = auto.claim_run(self.conn, SYM, TF, SHORT, LONG, candle, order_size=1, at_ms=ms(claim_at))
        snap = claimed["snapshot"]
        capture = {"snapshot_id": snap["snapshot_id"], "symbol": SYM, "interval": TF, "cutoff": candle}
        return auto.analyse_run(self.conn, claimed["run"]["id"], provider or TestDoubleDecisions(answers),
                                data_url(make_png()), capture, at_ms=ms(apply_at))

    def acct(self):
        a = account_state(self.conn, "paper-test")
        return a["cash"], a["positions"].get(SYM, 0), a["fill_count"], len(a["orders"])

    # ------------------------------------------------------------------ approved decision -> one fill
    def test_approved_buy_fills_with_decision_id_pnl_and_run(self):
        self.assertEqual(self.start()["account_id"], "paper-test")              # test data -> test account
        out = self.run_candle()
        did = out["decision"]["decision_id"]
        self.assertEqual((out["run"]["status"], out["order"]["status"], out["order"]["decision_id"]),
                         ("order_pending", "pending", did))
        self.assertEqual(self.acct(), (1000.0, 0, 0, 1))                         # nothing filled yet
        self.price(20, "15:05:20")                                               # next observed price
        self.assertEqual(self.acct(), (980.0, 1, 1, 1))
        fill = self.conn.execute("SELECT decision_id, price, cash_after_micros, position_after FROM paper_fills"
                                 ).fetchone()
        self.assertEqual(tuple(fill), (did, 20.0, 980_000_000, 1))
        pnl = self.conn.execute("SELECT event_type, cash_micros, quantity FROM pnl_history WHERE account_id = 'paper-test'"
                                ).fetchall()
        self.assertEqual([tuple(r) for r in pnl], [("fill", 980_000_000, 1)])
        run = auto.get_run(self.conn, out["run"]["id"])
        self.assertEqual((run["status"], run["fill_id"] is not None), ("filled", True))
        t = auto.trace(self.conn, did)
        self.assertEqual((t["crossover"]["indicators"]["crossover_at_candle"], t["analyst"]["proposal"],
                          t["review"]["verdict"], t["eligible"], t["order"]["fill"]["price"]),
                         ("up", "buy", "accept", True, 20.0))
        self.assertEqual((t["account_now"]["cash"], t["account_now"]["positions"][SYM]), (980.0, 1))
        self.assertEqual(account_state(self.conn, "paper")["cash"], 1000.0)     # main account untouched

    def test_same_decision_twice_and_after_restart_gives_one_fill(self):
        self.start()
        did = self.run_candle()["decision"]["decision_id"]
        self.price(20, "15:05:20")
        again = auto.apply_decision(self.conn, did, at_ms=ms("15:05:30"))
        self.assertTrue(again["duplicate"])
        self.price(21, "15:05:40")
        self.assertEqual(self.acct(), (980.0, 1, 1, 1))
        # restart: reopen the database; the same candle and the same decision are still handled once
        self.conn.close()
        self.conn = connect(self.path)
        claim = auto.claim_run(self.conn, SYM, TF, SHORT, LONG, "2026-11-09T15:04:00Z", order_size=1,
                               at_ms=ms("15:05:50"))
        self.assertFalse(claim["created"])
        self.assertTrue(auto.apply_decision(self.conn, did, at_ms=ms("15:05:55"))["duplicate"])
        self.price(22, "15:05:58")
        self.assertEqual(self.acct(), (980.0, 1, 1, 1))

    # ------------------------------------------------------------------ not executed
    def test_vetoed_proposal_leaves_account_unchanged(self):
        self.start()
        out = self.run_candle("action=buy,crossover=up,evidence=sufficient,verdict=reject_unsupported_signal")
        self.assertEqual((out["run"]["status"], out["order"]), ("skipped", None))
        self.assertIn("reviewer accepted", out["run"]["reason"])
        self.price(20, "15:05:20")
        self.assertEqual(self.acct(), (1000.0, 0, 0, 0))

    def test_stop_while_model_reply_pending(self):
        self.start()
        out = self.run_candle(provider=StopDuringReply(self.conn, APPROVE))
        self.assertEqual(out["run"]["status"], "stopped")
        self.assertTrue(out["decision"]["eligible_for_order"])                  # saved, but never executed
        self.price(20, "15:05:20")
        self.assertEqual(self.acct(), (1000.0, 0, 0, 0))

    def test_stop_rejects_pending_order(self):
        self.start()
        run_id = self.run_candle()["run"]["id"]
        auto.stop(self.conn, SYM, TF, SHORT, LONG, at_ms=ms("15:05:10"))
        self.price(20, "15:05:20")
        self.assertEqual(self.acct(), (1000.0, 0, 0, 1))
        self.assertEqual(auto.get_run(self.conn, run_id)["reason"], "automation stopped before the fill")

    def test_restart_after_stop_does_not_fill_old_generation(self):
        self.start()
        run_id = self.run_candle()["run"]["id"]
        self.conn.execute("UPDATE automation_state SET generation = generation + 1")   # a new Start in between
        self.conn.commit()
        self.price(20, "15:05:20")
        run = auto.get_run(self.conn, run_id)
        self.assertEqual((run["status"], run["reason"]), ("rejected", "automation stopped before the fill"))
        self.assertEqual(self.acct(), (1000.0, 0, 0, 1))

    def test_decision_too_old_at_fill(self):
        self.start()
        run_id = self.run_candle()["run"]["id"]
        self.price(20, "15:11:00")                                    # 360 s after the candle completed
        run = auto.get_run(self.conn, run_id)
        self.assertEqual(run["status"], "rejected")
        self.assertIn("decision too old at fill", run["reason"])
        self.assertEqual(self.acct(), (1000.0, 0, 0, 1))

    def test_approved_wait_records_no_order(self):
        self.start(at="15:02:30")
        out = self.run_candle("action=wait,crossover=none,evidence=sufficient,verdict=accept",
                              candle="2026-11-09T15:03:00Z", claim_at="15:04:02", apply_at="15:04:03")
        self.assertEqual((out["run"]["status"], out["order"]), ("no_order", None))
        self.assertEqual(self.acct(), (1000.0, 0, 0, 0))

    def test_only_newly_completed_candles_while_running(self):
        with self.assertRaises(auto.AutomationError):                 # not running
            auto.claim_run(self.conn, SYM, TF, SHORT, LONG, "2026-11-09T15:04:00Z", order_size=1, at_ms=ms("15:05:02"))
        self.start(at="15:06:00")
        with self.assertRaises(auto.AutomationError) as ctx:          # completed before Start
            auto.claim_run(self.conn, SYM, TF, SHORT, LONG, "2026-11-09T15:04:00Z", order_size=1, at_ms=ms("15:06:02"))
        self.assertIn("before automation started", str(ctx.exception))

    def test_failed_analysis_executes_nothing(self):
        self.start()
        claimed = auto.claim_run(self.conn, SYM, TF, SHORT, LONG, "2026-11-09T15:04:00Z", order_size=1,
                                 at_ms=ms("15:05:02"))
        run = auto.fail_run(self.conn, claimed["run"]["id"], "capture returned no image", at_ms=ms("15:05:03"))
        self.assertEqual(run["status"], "failed_analysis")
        self.assertIn("chart or image unavailable", run["reason"])
        out = auto.analyse_run(self.conn, run["id"], TestDoubleDecisions(APPROVE), None, None)
        self.assertEqual((out["run"]["status"], out["decision"]), ("failed_analysis", None))
        self.assertEqual(self.acct(), (1000.0, 0, 0, 0))

    def test_unreadable_image_is_a_failed_analysis(self):
        self.start()
        claimed = auto.claim_run(self.conn, SYM, TF, SHORT, LONG, "2026-11-09T15:04:00Z", order_size=1,
                                 at_ms=ms("15:05:02"))
        out = auto.analyse_run(self.conn, claimed["run"]["id"], TestDoubleDecisions(APPROVE), "data:image/png;base64,AAAA",
                               {}, at_ms=ms("15:05:03"))
        self.assertEqual(out["run"]["status"], "failed_analysis")

    def test_manual_decision_cannot_create_an_order(self):
        snap = create_snapshot(self.conn, SYM, TF, SHORT, LONG, "paper", 1, ms("15:05:10"))
        d = decide(self.conn, snap["snapshot_id"], TestDoubleDecisions("action=buy,verdict=accept"), review=True)
        self.assertTrue(d["eligible_for_order"])
        with self.assertRaises(auto.AutomationError) as ctx:
            auto.apply_decision(self.conn, d["decision_id"])
        self.assertIn("manual chart question never creates an order", str(ctx.exception))
        self.assertEqual(account_state(self.conn, "paper")["orders"], [])


if __name__ == "__main__":
    unittest.main()
