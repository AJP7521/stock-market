"""Prompts 16, 16A and 16C: image-only chart decision (chart_signal: long, short or wait), without a live model.

TEST DATA only: the labelled TESTSMA fixture (SMA2/SMA3 cross upward at 15:04; closes 13 at 15:03 and 19 at 15:04, so
Python's chart_signal at the 15:04 cutoff is long), plus TESTOC candles written here with chosen opens and closes. PNGs are generated here. The provider is the labelled test double (call_kind 'mock'). The backend clock is
fixed with recorder.TEST_NOW_MS so the capture time and staleness are repeatable.

    python3 -m unittest backend.tests.test_image_decisions -v
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import backend.recorder as recorder
from backend.chart_images import ImageRejected, data_url
from backend.decisions import (ProviderTimeout, TestDoubleDecisions, build_request as decision_request,
                               claude_answers, claude_params, create_snapshot, create_test_snapshot)
from backend.fixtures import SMA_SYMBOL, write_sma_fixture
from backend.image_decisions import (SIGNAL_QUESTION, build_request, check_probabilities, decide_image,
                                     ensure_schema, get_image_decision, list_image_decisions, position_of,
                                     python_signal)
from backend.paper import open_account
from backend.recorder import connect, record
from backend.tests.test_candles import ms
from backend.tests.test_chart_decisions import make_png


class ImageDecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test-image.sqlite"
        write_sma_fixture(self.path)
        self.conn = connect(self.path)
        open_account(self.conn, "test", 1000)
        recorder.TEST_NOW_MS = ms("15:05:20")      # 20 s after the 15:04 candle completed: not stale

    def tearDown(self):
        recorder.TEST_NOW_MS = None
        self.conn.close()
        self.tmp.cleanup()

    def write_candles(self, symbol, bars):
        """One 1m candle per (minute, open, close): an opening price at :10 and a closing price at :50."""
        for minute, open_, close in bars:
            for sec, price in ((10, open_), (50, close)):
                t = ms(f"15:{minute:02d}:{sec}")
                record(self.conn, {"id": symbol, "price": price, "time": str(t)}, t + 100, source="test")

    def snapshot(self, candle_time="2026-11-09T15:04:00Z", symbol=SMA_SYMBOL):
        return create_snapshot(self.conn, symbol, "1m", 2, 3, "test", 1, ms("15:20:00"),
                               candle_time=candle_time, chart_candles=60)

    @staticmethod
    def capture(snap):
        return {"snapshot_id": snap["snapshot_id"], "symbol": snap["symbol"], "interval": snap["interval"],
                "cutoff": snap["candle"]["timestamp"]}

    def count(self, table):
        return self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def test_model_receives_only_the_image(self):
        snap, raw = self.snapshot(), make_png(8, 6)
        fake = TestDoubleDecisions("chart_signal=long")
        d = decide_image(self.conn, snap["snapshot_id"], fake, data_url(raw), self.capture(snap))
        sent = fake.received[0]
        user = sent["input"][1]["content"]
        self.assertEqual([p["type"] for p in user], ["input_text", "input_text", "input_image"])   # no input_json
        self.assertEqual(user[0]["text"], "Capture time: 2026-11-09T15:05:20.000Z (UTC). Chart interval: 1m. "
                         "Context: not stale. The last candle on the chart completed within one interval before "
                         "the capture time. Position context (application-defined): position exists = no; the "
                         "position is explicitly empty.")
        self.assertEqual(sent["questions"], [SIGNAL_QUESTION])
        self.assertEqual(SIGNAL_QUESTION["name"], "chart_signal")
        self.assertEqual([c["value"] for c in sent["questions"][0]["choices"]], ["long", "short", "hold", "exit", "wait"])
        text = json.dumps(sent).replace(user[2]["image_url"], "")
        for leaked in (SMA_SYMBOL, "15:04", "19.0", "crossover_at_candle", "cash", '"open"', '"close"', "shares"):
            self.assertNotIn(leaked, text)
        # the test double saw the identical PNG and no snapshot data
        response = d["response"]
        self.assertEqual(response["received_images"][0]["sha256"], d["image"]["sha256"])
        self.assertEqual(response["received_snapshot"], {"snapshot_id": None, "symbol": None, "candle_time": None})
        self.assertEqual((d["status"], d["stance"], d["call_kind"]), ("decided", "long", "mock"))
        # TESTSMA closes 13 (15:03) then 19 (15:04): Python expects long, matching the test double's answer
        self.assertEqual((d["capture_time"], d["stale"], d["python_signal"], d["signal_match"]),
                         ("2026-11-09T15:05:20.000Z", False, "long", True))
        self.assertEqual((d["symbol"], d["candle_time"], d["python_crossover"]),
                         (SMA_SYMBOL, "2026-11-09T15:04:00Z", "up"))
        self.assertEqual(d["position"], {"state": "empty", "direction": None, "shares": 0, "source": "paper account"})
        self.assertFalse(d["eligible_for_order"])
        self.assertTrue((self.path.parent / d["image"]["path"]).read_bytes() == raw)
        self.assertTrue(d["request_sent"]["input"][1]["content"][2]["image_url"].startswith("stored:captures/"))

    def test_claude_request_has_image_and_stance_schema_only(self):
        params = claude_params(build_request("claude-opus-5-5", data_url(make_png()), "Capture time: x."))
        blocks = params["messages"][0]["content"]
        self.assertEqual([b["type"] for b in blocks], ["text", "text", "image", "text"])
        self.assertEqual(blocks[2]["source"]["media_type"], "image/png")
        self.assertIn(SIGNAL_QUESTION["instructions"], blocks[3]["text"])
        schema = params["output_config"]["format"]["schema"]
        self.assertEqual(schema["properties"], {
            "chart_signal": {"type": "string", "enum": ["long", "short", "hold", "exit", "wait"]},
            "chart_signal_probabilities": {
                "type": "object", "additionalProperties": False, "required": ["long", "short", "hold", "exit", "wait"],
                "properties": {v: {"type": "number"} for v in ["long", "short", "hold", "exit", "wait"]}}})
        self.assertEqual(schema["required"], ["chart_signal", "chart_signal_probabilities"])
        self.assertIn("estimated probability (0 to 1) for each allowed answer", blocks[3]["text"])

    def test_claude_reply_becomes_the_excerpt_shape(self):
        params = claude_params(build_request("claude-opus-5-5", data_url(make_png()), "ctx"))
        reply = {"chart_signal": "hold", "chart_signal_probabilities":            # keys in any order
                 {"wait": 0.10, "hold": 0.70, "long": 0.05, "exit": 0.10, "short": 0.05}}
        message = SimpleNamespace(stop_reason="end_turn",
                                  content=[SimpleNamespace(type="text", text=json.dumps(reply))])
        self.assertEqual(claude_answers(message, params), [{
            "type": "choice", "name": "chart_signal", "choice": "hold", "confidence": None,
            "probabilities": [{"value": "long", "probability": 0.05}, {"value": "short", "probability": 0.05},
                              {"value": "hold", "probability": 0.70}, {"value": "exit", "probability": 0.10},
                              {"value": "wait", "probability": 0.10}]}])

    def test_other_decision_requests_ask_for_no_probabilities(self):
        snap = create_test_snapshot(self.conn, symbol="TESTX", interval="1m", decision_time_ms=ms("15:05:00"),
                                    candle={"timestamp": "2026-11-09T15:04:00Z", "close": 1},
                                    indicators={"sma_short_window": 2, "sma_long_window": 3}, account={"cash": 1})
        params = claude_params(decision_request(snap, "claude-opus-5-5"))
        self.assertEqual(list(params["output_config"]["format"]["schema"]["properties"]), ["action"])

    def test_probability_checks(self):
        ok = [{"value": "long", "probability": 0.1}, {"value": "short", "probability": 0.05},
              {"value": "hold", "probability": 0.0}, {"value": "exit", "probability": 0.0},
              {"value": "wait", "probability": 0.85}]
        self.assertEqual(check_probabilities(ok), "valid")
        self.assertEqual(check_probabilities(list(reversed(ok))), "valid")      # matched by value, any order
        self.assertEqual(check_probabilities(None), "missing")
        cases = {
            "sum": [{**ok[0], "probability": 0.5}, *ok[1:]],                     # 1.40
            "range": [{**ok[0], "probability": -0.1}, *ok[1:4], {**ok[4], "probability": 1.05}],
            "values": ok[:2] + ok[4:],                                           # the old three values only
            "duplicate": [ok[0], ok[0], *ok[2:]],
            "unknown value": [*ok[:4], {"value": "close", "probability": 0.85}],
            "type": [{**ok[0], "probability": "0.1"}, *ok[1:]],
            "shape": {"long": 0.1},
        }
        for label, value in cases.items():
            with self.subTest(label):
                self.assertTrue(check_probabilities(value).startswith("invalid: "))
        self.assertEqual(check_probabilities([{**ok[0], "probability": 0.11}, *ok[1:]]), "valid")   # 1.01

    def test_label_uses_the_chosen_probability_only_when_valid(self):
        snap = self.snapshot()
        good = decide_image(self.conn, snap["snapshot_id"],
                            TestDoubleDecisions("chart_signal=wait,"
                                                "chart_signal@p=wait:0.85;long:0.10;short:0.05;hold:0;exit:0"),
                            data_url(make_png()), self.capture(snap))
        # the chosen probability is found by value, although "wait" comes first here
        self.assertEqual((good["label"], good["chosen_probability"], good["probabilities_check"],
                          good["probabilities_source"]), ("wait 85%", 0.85, "valid", "test double (fixed)"))
        self.assertEqual(good["probabilities"][0], {"value": "wait", "probability": 0.85})
        old_three = decide_image(self.conn, snap["snapshot_id"],
                                 TestDoubleDecisions("chart_signal=wait,chart_signal@p=long:0.10;short:0.05;wait:0.85"),
                                 data_url(make_png()), self.capture(snap))
        self.assertEqual(old_three["label"], "wait")                       # three estimates: not shown
        self.assertTrue(old_three["probabilities_check"].startswith("invalid: values ['long', 'short', 'wait']"))
        bad = decide_image(self.conn, snap["snapshot_id"],
                           TestDoubleDecisions("chart_signal=wait,"
                                               "chart_signal@p=long:0.50;short:0.05;hold:0;exit:0;wait:0.85"),
                           data_url(make_png()), self.capture(snap))
        self.assertEqual((bad["status"], bad["label"], bad["chosen_probability"]), ("decided", "wait", None))
        self.assertEqual(bad["probabilities_check"], "invalid: probabilities add up to 1.400, not 1")
        none = decide_image(self.conn, snap["snapshot_id"], TestDoubleDecisions("chart_signal=long"),
                            data_url(make_png()), self.capture(snap))
        self.assertEqual((none["label"], none["probabilities_check"], none["probabilities"]), ("long", "missing", None))
        failed = decide_image(self.conn, snap["snapshot_id"], TestDoubleDecisions("fail=timeout"),
                              data_url(make_png()), self.capture(snap))
        self.assertEqual((failed["label"], failed["probabilities_check"]), (None, None))
        marker = list_image_decisions(self.conn, SMA_SYMBOL, "1m")[0]                # latest decided: "long"
        self.assertEqual(marker["label"], "long")

    def test_python_signal_with_position(self):
        def snap(closes, shares="missing", stale_candles=2):
            account = None if shares == "missing" else {"position": shares, "cash": 1000}
            return {"chart": {"candles": [{"close": c} for c in closes]}, "account": account}
        empty, long_, short_, unknown = (position_of(snap([], 0)), position_of(snap([], 3)),
                                         position_of(snap([], -2)), position_of(snap([])))
        acct = {"source": "paper account"}
        self.assertEqual([empty, long_, short_, unknown], [
            {"state": "empty", "direction": None, "shares": 0, **acct},
            {"state": "open", "direction": "long", "shares": 3, **acct},
            {"state": "open", "direction": "short", "shares": -2, **acct},
            {"state": "unknown", "direction": None, "shares": None, **acct}])
        table = [  # (position, older close, newer close, stale) -> expected
            (empty, 10, 11, False, "long"), (empty, 11, 10, False, "short"), (empty, 10, 10, False, "wait"),
            (long_, 10, 11, False, "hold"), (long_, 11, 10, False, "exit"), (long_, 10, 10, False, "wait"),
            (short_, 11, 10, False, "hold"), (short_, 10, 11, False, "exit"),
            (unknown, 10, 11, False, "wait"), (unknown, 11, 10, False, "wait"),       # unknown position first
            (empty, 10, 11, True, "wait"), (long_, 10, 11, True, "wait"),             # stale first
        ]
        for pos, older, newer, stale, expected in table:
            with self.subTest(pos=pos["state"], direction=pos["direction"], closes=(older, newer), stale=stale):
                self.assertEqual(python_signal(snap([older, newer]), stale, pos), expected)
        self.assertEqual(python_signal(snap([11]), False, long_), "wait")               # one candle only

    def test_open_and_unknown_positions_are_supplied_as_context(self):
        snap = self.snapshot()
        snapshot_json = json.loads(self.conn.execute("SELECT snapshot_json FROM decision_snapshots WHERE id = ?",
                                                     (snap["snapshot_id"],)).fetchone()[0])
        for account, context, expected in (
                ({**snapshot_json["account"], "position": 3},
                 "position exists = yes; direction = long.", "hold"),          # closes 13 -> 19 with a long position
                (None, "position state is unknown (missing).", "wait")):
            with self.subTest(context):
                self.conn.execute("UPDATE decision_snapshots SET snapshot_json = ? WHERE id = ?",
                                  (json.dumps({**snapshot_json, "account": account}), snap["snapshot_id"]))
                self.conn.commit()
                fake = TestDoubleDecisions(f"chart_signal={expected}")
                d = decide_image(self.conn, snap["snapshot_id"], fake, data_url(make_png()), self.capture(snap))
                self.assertTrue(fake.received[0]["input"][1]["content"][0]["text"].endswith(context))
                self.assertEqual((d["stance"], d["python_signal"], d["signal_match"]), (expected, expected, True))
        stored = self.conn.execute("SELECT position_state, position_direction, position_shares FROM image_decisions "
                                   "ORDER BY id").fetchall()
        self.assertEqual([tuple(r) for r in stored], [("open", "long", 3), ("unknown", None, None)])

    def test_test_position_override_is_labelled_and_released(self):
        import backend.image_decisions as image_module
        snap = self.snapshot()                                   # paper account: 0 shares
        try:
            image_module.TEST_POSITION = {"state": "open", "direction": "short"}
            fake = TestDoubleDecisions("chart_signal=exit")
            d = decide_image(self.conn, snap["snapshot_id"], fake, data_url(make_png()), self.capture(snap))
            self.assertTrue(fake.received[0]["input"][1]["content"][0]["text"].endswith(
                "position exists = yes; direction = short."))
            self.assertEqual(d["position"], {"state": "open", "direction": "short", "shares": None,
                                             "source": "TEST override"})
            self.assertEqual((d["python_signal"], d["signal_match"]), ("exit", True))   # 13 -> 19 against a short
        finally:
            image_module.TEST_POSITION = None
        d = decide_image(self.conn, snap["snapshot_id"], TestDoubleDecisions("chart_signal=long"),
                         data_url(make_png()), self.capture(snap))
        self.assertEqual(d["position"]["source"], "paper account")

    def test_older_table_is_rebuilt_for_the_new_values_keeping_rows(self):
        path = Path(self.tmp.name) / "old.sqlite"
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE image_decisions (id INTEGER PRIMARY KEY, snapshot_id INTEGER NOT NULL,
                created_ms INTEGER NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL, candle_ts TEXT NOT NULL,
                provider TEXT NOT NULL, model TEXT NOT NULL, call_kind TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('decided', 'malformed_answer', 'timeout', 'provider_error')),
                stance TEXT CHECK (stance IS NULL OR stance IN ('long', 'short', 'wait')), python_crossover TEXT,
                image_path TEXT NOT NULL, image_json TEXT NOT NULL, request_json TEXT NOT NULL, response_json TEXT,
                error TEXT, latency_ms INTEGER);
            CREATE INDEX image_decisions_by_chart ON image_decisions (symbol, interval, candle_ts);
            INSERT INTO image_decisions VALUES (1, 9, 1, 'AAPL', '1m', '2026-11-17T19:05:00Z', 'anthropic',
                'claude-opus-5-5', 'live', 'decided', 'wait', 'none', 'captures/x.png', '{}', '{}', NULL, NULL, 2660);
        """)
        with self.assertRaises(sqlite3.IntegrityError):                       # the old CHECK rejects "hold"
            old.execute("UPDATE image_decisions SET stance = 'hold' WHERE id = 1")
        ensure_schema(old)
        self.assertEqual(old.execute("SELECT id, symbol, stance, latency_ms FROM image_decisions").fetchall(),
                         [(1, "AAPL", "wait", 2660)])
        old.execute("UPDATE image_decisions SET stance = 'hold' WHERE id = 1")   # now accepted
        with self.assertRaises(sqlite3.IntegrityError):
            old.execute("UPDATE image_decisions SET stance = 'buy' WHERE id = 1")
        names = {r[0] for r in old.execute("SELECT name FROM sqlite_master")}
        self.assertIn("image_decisions_by_chart", names)
        self.assertNotIn("image_decisions_before_16e", names)
        ensure_schema(old)                                                      # second run: nothing to do
        self.assertEqual(old.execute("SELECT count(*) FROM image_decisions").fetchone()[0], 1)
        old.close()

    def test_no_trade_and_no_decision_row(self):
        snap = self.snapshot()
        before = (self.count("paper_orders"), self.count("paper_fills"), self.count("decisions"))
        for stance in ("long", "short", "wait"):
            decide_image(self.conn, snap["snapshot_id"], TestDoubleDecisions(f"chart_signal={stance}"),
                         data_url(make_png()), self.capture(snap))
        self.assertEqual(before, (self.count("paper_orders"), self.count("paper_fills"), self.count("decisions")))
        self.assertEqual(self.count("image_decisions"), 3)

    def test_bad_answers_and_failures_are_stored_with_status(self):
        snap = self.snapshot()
        cases = {"chart_signal=buy": "malformed_answer", "stance=long": "malformed_answer",
                 "fail=timeout": "timeout", "fail=provider_error": "provider_error"}
        for spec, status in cases.items():
            with self.subTest(spec):
                d = decide_image(self.conn, snap["snapshot_id"], TestDoubleDecisions(spec), data_url(make_png()),
                                 self.capture(snap))
                self.assertEqual((d["status"], d["stance"]), (status, None))
                self.assertTrue(d["error"])
        self.assertEqual(list_image_decisions(self.conn, SMA_SYMBOL, "1m"), [])      # none decided: no markers

    def test_unreadable_or_mismatched_capture_is_rejected_before_any_call(self):
        snap = self.snapshot()
        fake = TestDoubleDecisions("chart_signal=long")
        wrong = {**self.capture(snap), "cutoff": "2026-11-09T15:03:00Z"}
        for image, capture in (("data:image/png;base64,@@", self.capture(snap)), (data_url(make_png()), wrong),
                               (data_url(make_png()), None)):
            with self.assertRaises(ImageRejected) as ctx:
                decide_image(self.conn, snap["snapshot_id"], fake, image, capture)
            self.assertTrue(ctx.exception.recapture)
        with self.assertRaises(ImageRejected):
            decide_image(self.conn, 9999, fake, data_url(make_png()), self.capture(snap))
        self.assertEqual(fake.received, [])
        self.assertEqual(self.count("image_decisions"), 0)

    def test_markers_list_latest_decided_stance_per_candle(self):
        a, b = self.snapshot("2026-11-09T15:03:00Z"), self.snapshot("2026-11-09T15:04:00Z")
        decide_image(self.conn, a["snapshot_id"], TestDoubleDecisions("chart_signal=wait"), data_url(make_png()),
                     self.capture(a))
        decide_image(self.conn, b["snapshot_id"], TestDoubleDecisions("chart_signal=long"), data_url(make_png()),
                     self.capture(b))
        decide_image(self.conn, b["snapshot_id"], TestDoubleDecisions("chart_signal=short"), data_url(make_png()),
                     self.capture(b))
        decide_image(self.conn, b["snapshot_id"], TestDoubleDecisions("fail=timeout"), data_url(make_png()),
                     self.capture(b))
        got = [(d["candle_time"][11:16], d["stance"]) for d in list_image_decisions(self.conn, SMA_SYMBOL, "1m")]
        self.assertEqual(got, [("15:04", "short"), ("15:03", "wait")])
        self.assertEqual(list_image_decisions(self.conn, SMA_SYMBOL, "5m"), [])
        self.assertIsNone(get_image_decision(self.conn, 9999))

    def test_python_signal_from_the_last_two_completed_candles(self):
        # (minute, open, close): closes 11, 12, 11, 10, 10, 12, 13; the 15:06 candle is red (open 14, close 13)
        self.write_candles("TESTOC", [(0, 10, 11), (1, 11, 12), (2, 12, 11), (3, 11, 10), (4, 10, 10), (5, 10, 12),
                                      (6, 14, 13)])
        cases = {"15:01": "long",     # 12 > 11
                 "15:02": "short",    # 11 < 12
                 "15:03": "short",    # 10 < 11
                 "15:04": "wait",     # 10 = 10: equal closes
                 "15:06": "long",     # 13 > 12 although the candle itself closed below its open: opens don't count
                 "15:00": "wait"}     # only one candle on the chart
        for cutoff, expected in cases.items():
            with self.subTest(cutoff):
                snap = self.snapshot(f"2026-11-09T{cutoff}:00Z", symbol="TESTOC")
                recorder.TEST_NOW_MS = snap["decision_ms"] + 5_000
                d = decide_image(self.conn, snap["snapshot_id"], TestDoubleDecisions(f"chart_signal={expected}"),
                                 data_url(make_png()), self.capture(snap))
                self.assertEqual((d["stale"], d["python_signal"], d["signal_match"]), (False, expected, True))

    def test_stale_context_is_supplied_and_python_expects_wait(self):
        self.write_candles("TESTOC", [(0, 10, 11), (1, 11, 12), (2, 12, 13)])
        snap = self.snapshot("2026-11-09T15:01:00Z", symbol="TESTOC")            # completed 15:02:00
        for now_ms, stale in ((ms("15:03:00"), False), (ms("15:03:00") + 1, True)):   # one interval / 1 ms more
            with self.subTest(stale=stale):
                recorder.TEST_NOW_MS = now_ms
                fake = TestDoubleDecisions("chart_signal=long")
                d = decide_image(self.conn, snap["snapshot_id"], fake, data_url(make_png()), self.capture(snap))
                context = fake.received[0]["input"][1]["content"][0]["text"]
                self.assertEqual(d["stale"], stale)
                self.assertEqual("Context: STALE." in context, stale)
                self.assertEqual((d["python_signal"], d["signal_match"]), ("wait", False) if stale else ("long", True))

    def test_timeout_from_provider_class(self):
        class Slow(TestDoubleDecisions):
            def send(self, body):
                raise ProviderTimeout("no response within 20 s")
        snap = self.snapshot()
        d = decide_image(self.conn, snap["snapshot_id"], Slow(), data_url(make_png()), self.capture(snap))
        self.assertEqual((d["status"], d["error"]), ("timeout", "no response within 20 s"))


if __name__ == "__main__":
    unittest.main()
