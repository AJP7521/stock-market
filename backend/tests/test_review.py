"""Prompt 11: analyst + reviewer, with fixed replies from the labelled provider test double (no live model).

TEST DATA: the TESTSMA fixture (SMA2/SMA3 cross upward at the 15:04 candle, close 19). Accounts are test accounts in a
disposable database: "test" starts with $1,000, "poor" with $10 (cannot pay for one share at $19).

    python3 -m unittest backend.tests.test_review -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from backend.chart_images import data_url
from backend.decisions import ProviderTimeout, TestDoubleDecisions, create_snapshot, decide, model_input
from backend.fixtures import SMA_SYMBOL, write_sma_fixture
from backend.paper import open_account
from backend.recorder import connect
from backend.review import REVIEWER_INSTRUCTIONS
from backend.tests.test_candles import ms
from backend.tests.test_chart_decisions import make_png


class RaisingReviewer:
    name, model, call_kind = "test-double", "test-double-reviewer", "mock"

    def __init__(self, exc):
        self.exc, self.calls = exc, 0

    def send(self, body):
        self.calls += 1
        raise self.exc


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test-review.sqlite"
        write_sma_fixture(self.path)
        self.conn = connect(self.path)
        open_account(self.conn, "test", 1000)
        open_account(self.conn, "poor", 10)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def snap(self, account="test", as_of="15:05:10"):
        return create_snapshot(self.conn, SMA_SYMBOL, "1m", 2, 3, account, 1, ms(as_of))

    def paper(self):
        return [self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("paper_orders", "paper_fills")]

    def failed(self, d):
        return [g["gate"] for g in d["final_check"]["gates"] if not g["passed"]]

    # ---------------------------------------------------------------- the three cases in the prompt
    def test_case1_agreed_eligible_proposal(self):
        snap = self.snap()
        fake = TestDoubleDecisions("action=buy,verdict=accept")
        d = decide(self.conn, snap["snapshot_id"], fake, review=True)
        self.assertEqual(len(fake.received), 2)                        # one call per role
        analyst, reviewer = fake.received
        self.assertEqual([q["name"] for q in analyst["questions"]], ["action"])
        self.assertEqual([q["name"] for q in reviewer["questions"]], ["verdict"])
        self.assertNotEqual(analyst["input"][0], reviewer["input"][0])   # separate instructions
        self.assertEqual(reviewer["input"][0]["content"][0]["text"], REVIEWER_INSTRUCTIONS)
        sent = reviewer["input"][1]["content"][-1]["data"]
        self.assertEqual(sent["analyst"]["proposal"], "buy")
        self.assertEqual(sent["snapshot"], model_input(snap))                     # the same frozen snapshot
        self.assertEqual(sent["snapshot"], analyst["input"][1]["content"][-1]["data"])
        self.assertEqual(sent["backend_facts"]["order_cost_at_close"], 19.0)
        # saved result
        self.assertEqual((d["status"], d["model_proposal"]["action"]), ("decided", "buy"))
        self.assertEqual((d["review"]["status"], d["review"]["verdict"]), ("reviewed", "accept"))
        self.assertTrue(d["final_check"]["eligible"])
        self.assertTrue(d["eligible_for_order"])
        self.assertIn("cash $1,000.00 ≥ cost of 1 share(s) at close $19.00 = $19.00", d["review"]["evidence"])
        self.assertIn("Final check: eligible.", d["explanation"])
        row = self.conn.execute("SELECT snapshot_id, model, reviewer_model, verdict, reviewer_status FROM decisions "
                                "WHERE id = ?", (d["decision_id"],)).fetchone()
        self.assertEqual(tuple(row), (snap["snapshot_id"], "test-double", "test-double", "accept", "reviewed"))
        self.assertEqual(self.paper(), [0, 0])

    def test_case2_reviewer_rejection(self):
        d = decide(self.conn, self.snap()["snapshot_id"],
                   TestDoubleDecisions("action=buy,verdict=reject_unsupported_signal"), review=True)
        self.assertEqual((d["validation"]["status"], d["review"]["verdict"]), ("valid", "reject_unsupported_signal"))
        self.assertFalse(d["eligible_for_order"])
        self.assertEqual(self.failed(d), ["reviewer accepted"])
        self.assertEqual(self.paper(), [0, 0])

    def test_case3_unaffordable_buy_accepted_by_both(self):
        snap = self.snap(account="poor")
        d = decide(self.conn, snap["snapshot_id"], TestDoubleDecisions("action=buy,verdict=accept"), review=True)
        self.assertEqual((d["model_proposal"]["action"], d["review"]["verdict"]), ("buy", "accept"))
        self.assertEqual(d["validation"]["status"], "invalid")
        self.assertFalse(d["eligible_for_order"])
        self.assertEqual(self.failed(d), ["account rule: cash covers 1 share(s) at the candle close ($19.00)"])
        self.assertIn("cash $10.00 < cost of 1 share(s) at close $19.00 = $19.00", d["review"]["evidence"])
        self.assertEqual(self.paper(), [0, 0])

    # ---------------------------------------------------------------- other failures
    def test_reviewer_timeout_and_invalid_reply_are_ineligible(self):
        sid = self.snap()["snapshot_id"]
        slow = RaisingReviewer(ProviderTimeout("no response within 20 s"))
        d = decide(self.conn, sid, TestDoubleDecisions("action=buy"), review=True, reviewer=slow)
        self.assertEqual((d["review"]["status"], slow.calls, d["eligible_for_order"]), ("timeout", 1, False))
        bad = decide(self.conn, sid, TestDoubleDecisions("action=buy,verdict=maybe"), review=True)
        self.assertEqual((bad["review"]["status"], bad["eligible_for_order"]), ("malformed_answer", False))
        none = decide(self.conn, sid, TestDoubleDecisions("action=buy"), review=True)
        self.assertEqual(none["review"]["status"], "malformed_answer")

    def test_strategy_rule_enforced_even_when_accepted(self):
        d = decide(self.conn, self.snap()["snapshot_id"], TestDoubleDecisions("action=wait,verdict=accept"),
                   review=True)
        self.assertEqual(d["validation"]["status"], "valid")              # flat account: wait passes account rule
        self.assertFalse(d["eligible_for_order"])
        self.assertEqual(self.failed(d), ["strategy: crossover up with position 0 allows buy"])

    def test_no_reviewer_call_without_a_proposal(self):
        fake = TestDoubleDecisions("verdict=accept")                        # analyst gives no action
        d = decide(self.conn, self.snap()["snapshot_id"], fake, review=True)
        self.assertEqual((d["status"], d["review"]["status"], len(fake.received)), ("malformed_answer", "not_run", 1))
        self.assertFalse(d["eligible_for_order"])

    def test_chart_question_passes_same_image_and_named_answers(self):
        snap = create_snapshot(self.conn, SMA_SYMBOL, "1m", 2, 3, "test", 1, ms("15:20:00"),
                               candle_time="2026-11-09T15:04:00Z")
        capture = {"snapshot_id": snap["snapshot_id"], "symbol": SMA_SYMBOL, "interval": "1m",
                   "cutoff": "2026-11-09T15:04:00Z"}
        fake = TestDoubleDecisions("action=buy,crossover=up,evidence=sufficient,verdict=accept")
        d = decide(self.conn, snap["snapshot_id"], fake, image=data_url(make_png()), capture=capture, review=True)
        analyst_img = next(p for p in fake.received[0]["input"][1]["content"] if p["type"] == "input_image")
        reviewer_img = next(p for p in fake.received[1]["input"][1]["content"] if p["type"] == "input_image")
        self.assertEqual(analyst_img, reviewer_img)
        sent = fake.received[1]["input"][1]["content"][-1]["data"]
        self.assertEqual(sent["analyst"]["named_answers"], {"action": "buy", "crossover": "up", "evidence": "sufficient"})
        self.assertTrue(d["eligible_for_order"])
        stored = json.dumps(d["review"]["request"])
        self.assertNotIn("base64", stored)                                   # image kept as a file reference
        self.assertIn(d["image"]["sha256"], stored)

    def test_without_review_prompt10_behaviour_is_unchanged(self):
        fake = TestDoubleDecisions("action=buy")
        d = decide(self.conn, self.snap()["snapshot_id"], fake)
        self.assertEqual((len(fake.received), d["review"], d["final_check"]), (1, None, None))
        self.assertTrue(d["eligible_for_order"])


if __name__ == "__main__":
    unittest.main()
