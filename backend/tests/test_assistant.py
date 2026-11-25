"""Prompt 18: trading assistant chatbot (TEST DATA, disposable SQLite, no live model).

Data: the labelled TESTBT fixture (backend/fixtures.py) with the server clock fixed at 2026-11-09 15:17:20 UTC: the
15:16 candle is the latest completed one and has an upward SMA(2)/SMA(4) crossover, the 15:17 candle is forming and
the latest observation was received 9.7 s earlier (15:17:10.3). The paper account starts with $1,000 and no shares.

    python3 -m unittest backend.tests.test_assistant -v
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend import assistant as A
from backend import recorder
from backend.fixtures import BACKTEST_SYMBOL as SYM, write_backtest_fixture
from backend.indicators import sma_crossover
from backend.paper import account_state, open_account
from backend.series import candle_series
from backend.tests.test_candles import ms

NOW = ms("15:17:20")


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "assistant.sqlite"
        write_backtest_fixture(path)
        self.conn = recorder.connect(path)
        open_account(self.conn, "paper", 1000)
        self.clock = mock.patch.object(recorder, "TEST_NOW_MS", NOW)
        self.clock.start()
        self.provider = A.TestDoubleAssistant()
        self.session = A.start_session(self.conn, SYM, "1m", 2, 4, "paper", self.provider)

    def tearDown(self):
        self.clock.stop()
        self.conn.close()
        self.tmp.cleanup()

    def ask(self, text):
        A.ask(self.conn, self.session["id"], text, self.provider)
        return A.get_session(self.conn, self.session["id"])["turns"][-1]

    def orders(self):
        return self.conn.execute("SELECT count(*) FROM paper_orders").fetchone()[0]

    # ------------------------------------------------------------------ tools return the app's own numbers
    def test_tools_match_the_underlying_functions(self):
        state = A.market_state(self.conn, SYM, "1m", 2, 4)
        rows = [{**c["candle"], "complete": c["status"]["complete"]} for c in candle_series(SYM, "1m", NOW, self.conn)["candles"]]
        expected = next(p for p in sma_crossover(rows, 2, 4)["points"] if p["timestamp"] == "2026-11-09T15:16:00Z")
        self.assertEqual((state["sma"]["latest"]["short"], state["sma"]["latest"]["long"], state["sma"]["latest"]["signal"]),
                         (expected["short"], expected["long"], "up"))
        self.assertEqual(state["completed_candles"][-1]["timestamp"], "2026-11-09T15:16:00Z")
        self.assertEqual(state["forming_candle"]["timestamp"], "2026-11-09T15:17:00Z")
        self.assertEqual((state["age_s"], state["data_labels"]), (9.7, ["test"]))   # received 15:17:10.3
        self.assertEqual(A.account(self.conn, "paper", SYM)["cash"], 1000.0)
        sig = A.strategy_signal(self.conn, "paper", SYM, "1m", 2, 4)
        self.assertEqual((sig["crossover_at_candle"], sig["rule_action"]), ("up", "buy"))

    # ------------------------------------------------------------------ ask -> recommendation -> check, no order
    def test_question_about_action_gets_checked_recommendation_and_no_order(self):
        t = self.ask("Should I buy or sell now?")
        self.assertEqual((t["recommendation"]["action"], t["recommendation"]["quantity"]), ("buy", 1))
        c = t["python_check"]
        self.assertTrue(c["actionable"])
        self.assertTrue(c["test_data"])
        self.assertEqual(c["order"], {"action": "buy", "quantity": 1})
        self.assertEqual([x["name"] for x in t["tool_calls"]], ["get_market_state", "get_account", "get_strategy_signal"])
        self.assertEqual(self.orders(), 0)                      # nothing trades from the chat

    def test_general_question_has_no_recommendation(self):
        t = self.ask("What is a moving average?")
        self.assertIsNone(t["recommendation"])
        self.assertIsNone(t["python_check"])
        self.assertIn("Test double reply to: What is a moving average?", t["text"])
        with self.assertRaises(A.AssistantError):
            A.place_order(self.conn, t["id"])

    # ------------------------------------------------------------------ place only on click, once
    def test_place_creates_one_assistant_order_even_when_clicked_twice(self):
        t = self.ask("Should I buy?")
        first = A.place_order(self.conn, t["id"])
        again = A.place_order(self.conn, t["id"])
        self.assertEqual((first["order"]["side"], first["order"]["quantity"], first["order"]["status"]),
                         ("buy", 1, "pending"))
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["order"]["id"], first["order"]["id"])
        self.assertEqual(self.orders(), 1)
        origin = self.conn.execute("SELECT origin, request_id FROM paper_orders").fetchone()
        self.assertEqual(tuple(origin), ("assistant", f"assistant-turn-{t['id']}"))

    def test_place_rechecks_at_click_time(self):
        t = self.ask("Should I buy?")
        with mock.patch.object(recorder, "TEST_NOW_MS", NOW + 600_000):     # 10 minutes later: data now stale
            with self.assertRaisesRegex(A.AssistantError, "younger than"):
                A.place_order(self.conn, t["id"])
        self.assertEqual(self.orders(), 0)

    # ------------------------------------------------------------------ Python check gates
    def check(self, **rec):
        return A.check_recommendation(self.conn, "paper", SYM, "1m", rec)

    def failed(self, c):
        return [g["gate"] for g in c["gates"] if not g["passed"]]

    def test_check_gates(self):
        self.assertEqual(self.failed(self.check(action="buy", quantity=100)), ["cash for quantity x last close"])
        self.assertEqual(self.failed(self.check(action="buy", quantity=0)),
                         ["whole quantity of at least 1"])
        self.assertEqual(self.failed(self.check(action="sell", quantity=1)), ["shares held (long only)"])
        self.assertEqual(self.failed(self.check(action="close", quantity=0)), ["shares held (long only)"])
        hold = self.check(action="hold", quantity=0)
        self.assertEqual((hold["places_order"], hold["actionable"], hold["order"]), (False, False, None))
        self.assertTrue(self.check(action="buy", quantity=40)["actionable"])  # 40 x 22 (last close) = 880 <= 1,000
        with mock.patch.object(recorder, "TEST_NOW_MS", NOW + 600_000):
            self.assertIn("latest observation younger than 300 s", self.failed(self.check(action="buy", quantity=1)))

    # ------------------------------------------------------------------ Claude loop with a stubbed client
    def test_claude_loop_runs_tools_then_returns_give_answer(self):
        tool_use = SimpleNamespace(type="tool_use", name="get_account", input={"symbol": SYM}, id="tu1")
        answer = SimpleNamespace(type="tool_use", name="give_answer", id="tu2", input={
            "reply": "Hold off.", "recommendation": {"action": "wait", "quantity": 0, "confidence": 0.6,
                                                     "reasons": [], "case_against": [], "would_change": []}})
        replies = [SimpleNamespace(model="claude-opus-5-5", stop_reason="tool_use", content=[tool_use]),
                   SimpleNamespace(model="claude-opus-5-5", stop_reason="tool_use", content=[answer])]
        sent = []

        def create(**kwargs):
            sent.append(kwargs)
            return replies[len(sent) - 1]

        fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
        with mock.patch("anthropic.Anthropic", return_value=fake):
            provider = A.ClaudeAssistant(api_key="test-key-not-real")
            A.ask(self.conn, self.session["id"], "Should I buy?", provider)
        t = A.get_session(self.conn, self.session["id"])["turns"][-1]
        self.assertEqual((t["text"], t["recommendation"]["action"], t["status"]), ("Hold off.", "wait", "ok"))
        self.assertEqual([c["name"] for c in t["tool_calls"]], ["get_account"])
        second = sent[1]["messages"]
        self.assertEqual(second[-1]["content"][0]["tool_use_id"], "tu1")           # tool result sent back
        self.assertEqual((sent[0]["tool_choice"], sent[0]["thinking"]), ({"type": "auto"}, {"type": "adaptive"}))
        self.assertEqual(self.orders(), 0)


if __name__ == "__main__":
    unittest.main()
