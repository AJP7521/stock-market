"""Prompt 10: decision snapshots and POST /v1/decisions handling, without a live model.

Uses TEST DATA only: a constructed test snapshot and the labelled TESTSMA fixture (closes 13, 10,
7, 13, 19, 7, 1 from 15:00Z; SMA2/SMA3 cross upward at the 15:04 candle). The provider is a test
double, or the real HTTP client pointed at a local socket that never answers (controlled failure).

Run from the project root:
    python3 -m unittest backend.tests.test_decisions -v
"""

import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from backend.decisions import (ClaudeDecisions, OpenAIDecisions, ProviderTimeout, build_request, claude_answers,
                               claude_params, create_snapshot, create_test_snapshot, decide, get_decision,
                               make_provider)
from backend.fixtures import SMA_SYMBOL, write_sma_fixture
from backend.paper import open_account
from backend.recorder import connect
from backend.tests.test_candles import ms


class FakeProvider:
    """Test double: returns a fixed response or raises, and records what it was sent."""
    name, model, call_kind = "fake", "test-double", "mock"

    def __init__(self, response=None, raises=None):
        self.response, self.raises, self.sent = response, raises, []

    def send(self, body):
        self.sent.append(body)
        if self.raises:
            raise self.raises
        return self.response


def answer(choice):
    return {"model": "test-double", "answers": [{"type": "choice", "name": "action", "choice": choice,
                                                 "probabilities": [{"value": choice, "probability": 0.9}],
                                                 "confidence": 0.8}]}


def paper_tables(conn):
    return [conn.execute(f"SELECT * FROM {t}").fetchall()
            for t in ("paper_accounts", "paper_positions", "paper_orders", "paper_fills")]


class DecisionFixture(unittest.TestCase):
    """Disposable database with the TESTSMA fixture and a test paper account."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test-decisions.sqlite"
        write_sma_fixture(self.path)
        self.conn = connect(self.path)
        open_account(self.conn, "test", 1000)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def app_snapshot(self, as_of):
        return create_snapshot(self.conn, SMA_SYMBOL, "1m", 2, 3, "test", 1, ms(as_of))

    def fixed_test_snapshot(self, position=0):
        return create_test_snapshot(
            self.conn, symbol="TESTFIXED", interval="1m", decision_time_ms=ms("15:05:00"),
            candle={"timestamp": "2026-11-09T15:04:00Z", "open": 19, "high": 19, "low": 19, "close": 19, "volume": 100},
            indicators={"sma_short_window": 2, "sma_long_window": 3, "sma_short": 16.0, "sma_long": 13.0,
                        "short_vs_long": "above", "crossover_at_candle": "up"},
            account={"account_id": "test", "cash": 1000.0, "position": position, "order_size": 1})


class DecisionTests(DecisionFixture):
    def test_snapshot_freezes_values_as_of_the_decision_time(self):
        snap = self.app_snapshot("15:05:10")          # 15:04 has completed, 15:05 is forming
        self.assertTrue(snap["complete"])
        self.assertEqual(snap["candle"]["timestamp"], "2026-11-09T15:04:00Z")
        self.assertEqual(snap["candle"]["close"], 19.0)
        self.assertEqual((snap["indicators"]["sma_short"], snap["indicators"]["sma_long"],
                          snap["indicators"]["crossover_at_candle"]), (16.0, 13.0, "up"))
        self.assertEqual((snap["account"]["cash"], snap["account"]["position"]), (1000.0, 0))
        self.assertEqual(snap["market_data_labels"], ["test"])
        # Replay: as of 15:04:40 the 15:04 candle is still forming, so the snapshot uses 15:03.
        earlier = self.app_snapshot("15:04:40")
        self.assertEqual(earlier["candle"]["timestamp"], "2026-11-09T15:03:00Z")
        self.assertEqual(earlier["indicators"]["crossover_at_candle"], "none")
        self.assertNotEqual(earlier["snapshot_id"], snap["snapshot_id"])

    def test_fixed_test_snapshot_buy_is_decided_but_not_eligible(self):
        snap = self.fixed_test_snapshot()
        fake = FakeProvider(answer("buy"))
        d = decide(self.conn, snap["snapshot_id"], fake)
        self.assertEqual((d["status"], d["model_proposal"]["action"], d["validation"]["status"]),
                         ("decided", "buy", "valid"))
        self.assertFalse(d["eligible_for_order"])             # test snapshots never place orders
        self.assertEqual(d["backend_facts"]["crossover_at_candle"], "up")
        self.assertIn("Model proposal (test-double): buy", d["explanation"])
        self.assertEqual(get_decision(self.conn, d["decision_id"]), d)   # saved under one decision ID
        sent = fake.sent[0]
        self.assertEqual(sent["questions"][0]["name"], "action")
        self.assertEqual([c["value"] for c in sent["questions"][0]["choices"]], ["buy", "sell", "hold", "wait", "close"])
        self.assertEqual(sent["input"][1]["content"][1]["type"], "input_json")
        self.assertEqual(sent["input"][1]["content"][1]["data"]["snapshot_id"], snap["snapshot_id"])

    def test_application_snapshot_is_eligible_and_account_untouched(self):
        before = paper_tables(self.conn)
        d = decide(self.conn, self.app_snapshot("15:05:10")["snapshot_id"], FakeProvider(answer("buy")))
        self.assertTrue(d["eligible_for_order"])
        self.assertEqual(paper_tables(self.conn), before)

    def test_valid_hold_and_wait_are_decisions(self):
        flat = decide(self.conn, self.fixed_test_snapshot(position=0)["snapshot_id"], FakeProvider(answer("wait")))
        held = decide(self.conn, self.fixed_test_snapshot(position=2)["snapshot_id"], FakeProvider(answer("hold")))
        self.assertEqual((flat["status"], flat["validation"]["status"]), ("decided", "valid"))
        self.assertEqual((held["status"], held["validation"]["status"]), ("decided", "valid"))

    def test_rule_violations_are_invalid(self):
        hold_flat = decide(self.conn, self.fixed_test_snapshot(0)["snapshot_id"], FakeProvider(answer("hold")))
        sell_flat = decide(self.conn, self.fixed_test_snapshot(0)["snapshot_id"], FakeProvider(answer("sell")))
        for d in (hold_flat, sell_flat):
            self.assertEqual((d["status"], d["validation"]["status"], d["eligible_for_order"]),
                             ("decided", "invalid", False))
        self.assertIn("Rule check fails", sell_flat["explanation"])

    def test_missing_input_makes_no_provider_call(self):
        warmup = self.app_snapshot("15:01:10")         # only one completed candle: SMA3 not available
        self.assertFalse(warmup["complete"])
        fake = FakeProvider(answer("buy"))
        d = decide(self.conn, warmup["snapshot_id"], fake)
        self.assertEqual((d["status"], d["eligible_for_order"]), ("missing_input", False))
        self.assertEqual(fake.sent, [])
        self.assertEqual(decide(self.conn, 9999, fake)["status"], "missing_input")

    def test_malformed_answers(self):
        sid = self.fixed_test_snapshot()["snapshot_id"]
        for response in (answer("short"), {"answers": []}, {"answers": [{"name": "other", "choice": "buy"}]},
                         {"answers": "nonsense"}):
            d = decide(self.conn, sid, FakeProvider(response))
            self.assertEqual(d["status"], "malformed_answer", response)
            self.assertEqual(d["validation"]["status"], "not_checked")

    def test_timeout_from_test_double(self):
        d = decide(self.conn, self.fixed_test_snapshot()["snapshot_id"],
                   FakeProvider(raises=ProviderTimeout("no response within 1 s")))
        self.assertEqual((d["status"], d["error"]), ("timeout", "no response within 1 s"))

    def test_real_client_controlled_failures(self):
        sid = self.fixed_test_snapshot()["snapshot_id"]
        # 1. A local server that accepts the connection but never answers -> timeout.
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        accepted = []
        threading.Thread(target=lambda: accepted.append(listener.accept()), daemon=True).start()
        port = listener.getsockname()[1]
        slow = OpenAIDecisions(api_key="test-not-a-key", base_url=f"http://127.0.0.1:{port}/v1", timeout_s=0.5)
        self.assertEqual(decide(self.conn, sid, slow)["status"], "timeout")
        for conn, _addr in accepted:
            conn.close()
        listener.close()
        # 2. Nothing listening -> provider_error (connection refused).
        refused = OpenAIDecisions(api_key="test-not-a-key", base_url=f"http://127.0.0.1:{port}/v1", timeout_s=0.5)
        d = decide(self.conn, sid, refused)
        self.assertEqual(d["status"], "provider_error")
        self.assertIn("connection error", d["error"])
        # 3. No key configured -> provider_error, no request sent.
        nokey = decide(self.conn, sid, OpenAIDecisions(api_key="", base_url="http://127.0.0.1:9/v1"))
        self.assertEqual((nokey["status"], nokey["error"]),
                         ("provider_error", "OPENAI_API_KEY is not set in the backend environment"))

    def test_request_matches_documented_shape(self):
        body = build_request(self.fixed_test_snapshot(), "gpt-6-luna")
        self.assertEqual(body["model"], "gpt-6-luna")
        self.assertEqual(body["input"][0], {"role": "developer", "content": [
            {"type": "input_text", "text": body["input"][0]["content"][0]["text"]}]})
        self.assertEqual(body["questions"][0]["type"], "choice")
        json.dumps(body)                                   # serializable as sent


class FakeBlock:
    def __init__(self, type_, text=""):
        self.type, self.text = type_, text


class FakeMessage:
    """Stands in for an anthropic Message (test double; no API call)."""
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [FakeBlock("thinking"), FakeBlock("text", text)]
        self.stop_reason = stop_reason


class ClaudeProviderTests(DecisionFixture):
    """Claude path: request translation, reply parsing and controlled failures. No live call."""

    def test_claude_params_restrict_the_answer_to_five_choices(self):
        params = claude_params(build_request(self.fixed_test_snapshot(), "claude-opus-5-5"))
        schema = params["output_config"]["format"]["schema"]
        self.assertEqual(schema["properties"]["action"]["enum"], ["buy", "sell", "hold", "wait", "close"])
        self.assertEqual((schema["required"], schema["additionalProperties"]), (["action"], False))
        self.assertEqual(params["model"], "claude-opus-5-5")
        self.assertIn("long-only PAPER trading simulation", params["system"])
        self.assertIn('"symbol": "TESTFIXED"', params["messages"][0]["content"])
        self.assertEqual(params["fallbacks"], "default")
        json.dumps(params)

    def test_claude_answers_parsing(self):
        params = claude_params(build_request(self.fixed_test_snapshot(), "claude-opus-5-5"))
        self.assertEqual(claude_answers(FakeMessage('{"action": "wait"}'), params)[0]["choice"], "wait")
        self.assertEqual(claude_answers(FakeMessage("not json"), params), [])
        self.assertEqual(claude_answers(FakeMessage('{"other": 1}'), params), [])
        self.assertEqual(claude_answers(FakeMessage('{"action": "wa', "max_tokens"), params), [])

    def test_claude_answer_flows_through_decide(self):
        sid = self.fixed_test_snapshot()["snapshot_id"]
        params = claude_params(build_request(self.fixed_test_snapshot(), "claude-opus-5-5"))
        reply = {"model": "claude-opus-5-5", "answers": claude_answers(FakeMessage('{"action": "buy"}'), params)}
        d = decide(self.conn, sid, FakeProvider(reply))
        self.assertEqual((d["status"], d["model_proposal"]["action"], d["validation"]["status"]),
                         ("decided", "buy", "valid"))
        bad = {"answers": claude_answers(FakeMessage("garbled"), params)}
        self.assertEqual(decide(self.conn, sid, FakeProvider(bad))["status"], "malformed_answer")

    def test_claude_real_client_controlled_failures(self):
        sid = self.fixed_test_snapshot()["snapshot_id"]
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        accepted = []
        threading.Thread(target=lambda: accepted.append(listener.accept()), daemon=True).start()
        port = listener.getsockname()[1]
        slow = ClaudeDecisions(api_key="test-not-a-key", base_url=f"http://127.0.0.1:{port}", timeout_s=0.5,
                               max_retries=0)
        self.assertEqual(decide(self.conn, sid, slow)["status"], "timeout")
        for conn, _addr in accepted:
            conn.close()
        listener.close()
        refused = ClaudeDecisions(api_key="test-not-a-key", base_url=f"http://127.0.0.1:{port}", timeout_s=0.5,
                                  max_retries=0)
        d = decide(self.conn, sid, refused)
        self.assertEqual(d["status"], "provider_error")
        self.assertIn("connection error", d["error"])
        nokey = decide(self.conn, sid, ClaudeDecisions(api_key="", base_url="http://127.0.0.1:9"))
        self.assertEqual((nokey["status"], nokey["error"]),
                         ("provider_error", "ANTHROPIC_API_KEY is not set in the backend environment"))
        self.assertEqual((nokey["provider"], nokey["call_kind"]), ("anthropic", "live"))

    def test_make_provider_reads_the_environment(self):
        with mock.patch.dict(os.environ, {"DECISIONS_PROVIDER": "claude"}):
            self.assertIsInstance(make_provider(), ClaudeDecisions)
        with mock.patch.dict(os.environ, {}, clear=True):          # not set: Claude is the default
            self.assertIsInstance(make_provider(), ClaudeDecisions)
        with mock.patch.dict(os.environ, {"DECISIONS_PROVIDER": "openai"}):
            self.assertIsInstance(make_provider(), OpenAIDecisions)


if __name__ == "__main__":
    unittest.main()
