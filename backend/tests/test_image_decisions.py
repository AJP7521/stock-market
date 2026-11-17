"""Prompt 16: image-only chart decision (long, short or wait), without a live model.

TEST DATA only: the labelled TESTSMA fixture (SMA2/SMA3 cross upward at 15:04). PNGs are generated here. The provider
is the labelled test double (call_kind 'mock').

    python3 -m unittest backend.tests.test_image_decisions -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from backend.chart_images import ImageRejected, data_url
from backend.decisions import ProviderTimeout, TestDoubleDecisions, claude_params, create_snapshot
from backend.fixtures import SMA_SYMBOL, write_sma_fixture
from backend.image_decisions import build_request, decide_image, get_image_decision, list_image_decisions
from backend.paper import open_account
from backend.recorder import connect
from backend.tests.test_candles import ms
from backend.tests.test_chart_decisions import make_png


class ImageDecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test-image.sqlite"
        write_sma_fixture(self.path)
        self.conn = connect(self.path)
        open_account(self.conn, "test", 1000)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def snapshot(self, candle_time="2026-11-09T15:04:00Z"):
        return create_snapshot(self.conn, SMA_SYMBOL, "1m", 2, 3, "test", 1, ms("15:20:00"),
                               candle_time=candle_time, chart_candles=60)

    @staticmethod
    def capture(snap):
        return {"snapshot_id": snap["snapshot_id"], "symbol": snap["symbol"], "interval": snap["interval"],
                "cutoff": snap["candle"]["timestamp"]}

    def count(self, table):
        return self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def test_model_receives_only_the_image(self):
        snap, raw = self.snapshot(), make_png(8, 6)
        fake = TestDoubleDecisions("stance=long")
        d = decide_image(self.conn, snap["snapshot_id"], fake, data_url(raw), self.capture(snap))
        sent = fake.received[0]
        user = sent["input"][1]["content"]
        self.assertEqual([p["type"] for p in user], ["input_text", "input_image"])     # no input_json
        self.assertEqual([q["name"] for q in sent["questions"]], ["stance"])
        self.assertEqual([c["value"] for c in sent["questions"][0]["choices"]], ["long", "short", "wait"])
        text = json.dumps(sent).replace(user[1]["image_url"], "")
        for leaked in (SMA_SYMBOL, "15:04", "19.0", "crossover_at_candle", "cash"):
            self.assertNotIn(leaked, text)
        # the test double saw the identical PNG and no snapshot data
        response = d["response"]
        self.assertEqual(response["received_images"][0]["sha256"], d["image"]["sha256"])
        self.assertEqual(response["received_snapshot"], {"snapshot_id": None, "symbol": None, "candle_time": None})
        self.assertEqual((d["status"], d["stance"], d["call_kind"]), ("decided", "long", "mock"))
        self.assertEqual((d["symbol"], d["candle_time"], d["python_crossover"]),
                         (SMA_SYMBOL, "2026-11-09T15:04:00Z", "up"))
        self.assertFalse(d["eligible_for_order"])
        self.assertTrue((self.path.parent / d["image"]["path"]).read_bytes() == raw)
        self.assertTrue(d["request_sent"]["input"][1]["content"][1]["image_url"].startswith("stored:captures/"))

    def test_claude_request_has_image_and_stance_schema_only(self):
        params = claude_params(build_request("claude-opus-5-5", data_url(make_png())))
        blocks = params["messages"][0]["content"]
        self.assertEqual([b["type"] for b in blocks], ["text", "image", "text"])
        self.assertEqual(blocks[1]["source"]["media_type"], "image/png")
        schema = params["output_config"]["format"]["schema"]
        self.assertEqual(schema["properties"], {"stance": {"type": "string", "enum": ["long", "short", "wait"]}})

    def test_no_trade_and_no_decision_row(self):
        snap = self.snapshot()
        before = (self.count("paper_orders"), self.count("paper_fills"), self.count("decisions"))
        for stance in ("long", "short", "wait"):
            decide_image(self.conn, snap["snapshot_id"], TestDoubleDecisions(f"stance={stance}"),
                         data_url(make_png()), self.capture(snap))
        self.assertEqual(before, (self.count("paper_orders"), self.count("paper_fills"), self.count("decisions")))
        self.assertEqual(self.count("image_decisions"), 3)

    def test_bad_answers_and_failures_are_stored_with_status(self):
        snap = self.snapshot()
        cases = {"stance=buy": "malformed_answer", "action=buy": "malformed_answer",
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
        fake = TestDoubleDecisions("stance=long")
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
        decide_image(self.conn, a["snapshot_id"], TestDoubleDecisions("stance=wait"), data_url(make_png()),
                     self.capture(a))
        decide_image(self.conn, b["snapshot_id"], TestDoubleDecisions("stance=long"), data_url(make_png()),
                     self.capture(b))
        decide_image(self.conn, b["snapshot_id"], TestDoubleDecisions("stance=short"), data_url(make_png()),
                     self.capture(b))
        decide_image(self.conn, b["snapshot_id"], TestDoubleDecisions("fail=timeout"), data_url(make_png()),
                     self.capture(b))
        got = [(d["candle_time"][11:16], d["stance"]) for d in list_image_decisions(self.conn, SMA_SYMBOL, "1m")]
        self.assertEqual(got, [("15:04", "short"), ("15:03", "wait")])
        self.assertEqual(list_image_decisions(self.conn, SMA_SYMBOL, "5m"), [])
        self.assertIsNone(get_image_decision(self.conn, 9999))

    def test_timeout_from_provider_class(self):
        class Slow(TestDoubleDecisions):
            def send(self, body):
                raise ProviderTimeout("no response within 20 s")
        snap = self.snapshot()
        d = decide_image(self.conn, snap["snapshot_id"], Slow(), data_url(make_png()), self.capture(snap))
        self.assertEqual((d["status"], d["error"]), ("timeout", "no response within 20 s"))


if __name__ == "__main__":
    unittest.main()
