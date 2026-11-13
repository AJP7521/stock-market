"""Prompt 10A: chart snapshots, PNG checks and the three chart questions, without a live model.

TEST DATA only: the labelled TESTSMA fixture (closes 13, 10, 7, 13, 19, 7, 1 from 15:00Z; SMA2/SMA3 cross upward at
15:04 and downward at 15:06). PNGs are generated here. The provider is the labelled test double (call_kind 'mock').

    python3 -m unittest backend.tests.test_chart_decisions -v
"""

import hashlib
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from backend.chart_images import ImageRejected, check_png, data_url, read_png
from backend.decisions import (TestDoubleDecisions, build_request, claude_params, create_snapshot,
                               create_test_snapshot, decide)
from backend.fixtures import SMA_SYMBOL, write_sma_fixture
from backend.paper import open_account
from backend.recorder import connect
from backend.tests.test_candles import ms


def make_png(width=4, height=3, rgb=(10, 120, 200)) -> bytes:
    """A valid RGB PNG built with the standard library."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class PngTests(unittest.TestCase):
    def test_valid_png(self):
        raw = make_png(5, 2)
        got, info = read_png(data_url(raw))
        self.assertEqual(got, raw)
        self.assertEqual((info["width"], info["height"], info["bytes"]), (5, 2, len(raw)))
        self.assertEqual(info["sha256"], hashlib.sha256(raw).hexdigest())

    def test_unreadable_images_are_rejected(self):
        raw = make_png()
        damaged = bytearray(raw)
        damaged[40] ^= 0xFF                                  # inside IDAT: checksum no longer matches
        cases = {
            "not base64": "data:image/png;base64,@@@",
            "wrong media type": "data:image/jpeg;base64," + data_url(raw).split(",")[1],
            "not a PNG": data_url(b"GIF89a" + raw[6:]),
            "damaged": data_url(bytes(damaged)),
            "truncated": data_url(raw[:-12]),
            "empty": "",
        }
        for label, value in cases.items():
            with self.subTest(label):
                with self.assertRaises(ImageRejected) as ctx:
                    read_png(value)
                self.assertTrue(ctx.exception.recapture)

    def test_pixel_data_must_match_header(self):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        short = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0))
                 + chunk(b"IDAT", zlib.compress(b"\x00" * 5)) + chunk(b"IEND", b""))
        with self.assertRaises(ImageRejected):
            check_png(short)


class ChartDecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test-chart.sqlite"
        write_sma_fixture(self.path)
        self.conn = connect(self.path)
        open_account(self.conn, "test", 1000)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def snapshot(self, candle_time="2026-11-09T15:04:00Z", chart_candles=60, as_of="15:20:00"):
        return create_snapshot(self.conn, SMA_SYMBOL, "1m", 2, 3, "test", 1, ms(as_of),
                               candle_time=candle_time, chart_candles=chart_candles)

    def capture(self, snap):
        return {"snapshot_id": snap["snapshot_id"], "symbol": snap["symbol"], "interval": snap["interval"],
                "cutoff": snap["candle"]["timestamp"]}

    def counts(self):
        return {t: self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("decisions", "paper_orders", "paper_fills")}

    # ------------------------------------------------------------------ snapshot at a selected candle
    def test_snapshot_at_selected_candle_has_chart_through_cutoff(self):
        snap = self.snapshot()
        self.assertTrue(snap["complete"], snap["missing"])
        self.assertEqual(snap["decision_time"], "2026-11-09T15:05:00.000Z")   # when the 15:04 candle completed
        self.assertEqual((snap["candle"]["timestamp"], snap["candle"]["close"]), ("2026-11-09T15:04:00Z", 19.0))
        self.assertEqual(snap["indicators"]["crossover_at_candle"], "up")
        chart = snap["chart"]
        self.assertEqual([c["timestamp"][11:16] for c in chart["candles"]], ["15:00", "15:01", "15:02", "15:03", "15:04"])
        self.assertEqual(chart["range"], {"first": "2026-11-09T15:00:00Z", "last": "2026-11-09T15:04:00Z",
                                          "candles": 5, "requested": 60})
        self.assertEqual([(c["timestamp"][11:16], c["signal"]) for c in chart["sma"]["crossovers"]], [("15:04", "up")])
        self.assertEqual(chart["sma"]["points"][-1]["short"], 16.0)

    def test_chart_range_limits_candles_not_indicator_history(self):
        chart = self.snapshot(chart_candles=3)["chart"]
        self.assertEqual([c["timestamp"][11:16] for c in chart["candles"]], ["15:02", "15:03", "15:04"])
        self.assertEqual(chart["sma"]["points"][0]["long"], 10.0)    # SMA3 at 15:02 uses 15:00 and 15:01 too

    def test_forming_or_unknown_candle_is_incomplete(self):
        forming = self.snapshot(candle_time="2026-11-09T15:07:00Z", as_of="15:07:30")   # last price 15:07:10
        self.assertFalse(forming["complete"])
        self.assertIn("has not completed", forming["missing"][0])
        unknown = self.snapshot(candle_time="2026-11-09T14:00:00Z")
        self.assertIn("no 1m candle", unknown["missing"][0])

    # ------------------------------------------------------------------ image request with the test double
    def test_test_double_receives_the_saved_png(self):
        snap, raw = self.snapshot(), make_png(8, 6)
        fake = TestDoubleDecisions("action=buy,crossover=up,evidence=sufficient")
        d = decide(self.conn, snap["snapshot_id"], fake, image=data_url(raw), capture=self.capture(snap))
        # the provider got the PNG as input_image, next to the snapshot in input_json, in one user message
        user = fake.received[0]["input"][1]
        self.assertEqual([p["type"] for p in user["content"]], ["input_text", "input_image", "input_json"])
        self.assertTrue(user["content"][1]["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(user["content"][2]["data"]["snapshot_id"], snap["snapshot_id"])
        self.assertEqual([q["name"] for q in fake.received[0]["questions"]], ["action", "crossover", "evidence"])
        received = read_png(user["content"][1]["image_url"])[0]
        saved = (self.path.parent / d["image"]["path"]).read_bytes()
        self.assertEqual(received, raw)
        self.assertEqual(saved, raw)
        self.assertEqual(d["image"]["sha256"], hashlib.sha256(raw).hexdigest())
        # stored request keeps a reference instead of the image bytes
        row = self.conn.execute("SELECT request_json, response_json FROM decisions WHERE id = ?",
                                (d["decision_id"],)).fetchone()
        self.assertNotIn("base64", row[0])
        self.assertIn(f"stored:{d['image']['path']}", row[0])
        self.assertEqual(json.loads(row[1])["received_images"][0]["sha256"], d["image"]["sha256"])
        # answers, checks, eligibility
        self.assertEqual(d["answers"], {"action": "buy", "crossover": "up", "evidence": "sufficient"})
        self.assertTrue(all(c["passed"] for c in d["chart_checks"]))
        self.assertEqual((d["status"], d["validation"]["status"], d["eligible_for_order"]), ("decided", "valid", True))
        self.assertEqual(d["call_kind"], "mock")
        self.assertEqual(self.counts()["paper_orders"], 0)

    def test_conflicting_unreadable_missing_or_insufficient_are_ineligible(self):
        snap = self.snapshot()
        cases = {
            "conflict": ("action=buy,crossover=none,evidence=sufficient", "matches the Python crossover"),
            "unreadable": ("action=buy,crossover=unreadable,evidence=sufficient", "was readable"),
            "missing crossover": ("action=buy,evidence=sufficient", "crossover answer is present"),
            "missing evidence": ("action=buy,crossover=up", "evidence answer is present"),
            "insufficient": ("action=buy,crossover=up,evidence=insufficient", "sufficient to assess"),
            "invalid value": ("action=buy,crossover=sideways,evidence=sufficient", "crossover answer is present"),
        }
        for label, (answers, failed_rule) in cases.items():
            with self.subTest(label):
                d = decide(self.conn, snap["snapshot_id"], TestDoubleDecisions(answers),
                           image=data_url(make_png()), capture=self.capture(snap))
                self.assertEqual((d["status"], d["validation"]["status"]), ("decided", "valid"))
                self.assertFalse(d["eligible_for_order"])
                failed = [c["rule"] for c in d["chart_checks"] if not c["passed"]]
                self.assertTrue(any(failed_rule in r for r in failed), failed)
                self.assertIn("Not eligible for automated execution", d["explanation"])

    def test_missing_action_is_malformed(self):
        snap = self.snapshot()
        d = decide(self.conn, snap["snapshot_id"], TestDoubleDecisions("crossover=up,evidence=sufficient"),
                   image=data_url(make_png()), capture=self.capture(snap))
        self.assertEqual((d["status"], d["eligible_for_order"]), ("malformed_answer", False))

    def test_unreadable_image_or_mismatched_capture_is_rejected_before_the_provider(self):
        snap = self.snapshot()
        fake = TestDoubleDecisions()
        with self.assertRaises(ImageRejected):
            decide(self.conn, snap["snapshot_id"], fake, image=data_url(make_png()[:-20]), capture=self.capture(snap))
        wrong = {**self.capture(snap), "cutoff": "2026-11-09T15:03:00Z"}
        with self.assertRaises(ImageRejected) as ctx:
            decide(self.conn, snap["snapshot_id"], fake, image=data_url(make_png()), capture=wrong)
        self.assertIn("cutoff", ctx.exception.problem)
        self.assertIn("15:04:00Z", ctx.exception.recapture)
        plain = create_snapshot(self.conn, SMA_SYMBOL, "1m", 2, 3, "test", 1, ms("15:05:10"))   # no chart section
        with self.assertRaises(ImageRejected):
            decide(self.conn, plain["snapshot_id"], fake, image=data_url(make_png()),
                   capture={**self.capture(plain), "cutoff": plain["candle"]["timestamp"]})
        self.assertEqual(fake.received, [])
        self.assertEqual(self.counts()["decisions"], 0)

    def test_text_only_decision_still_asks_one_question(self):
        snap = create_test_snapshot(
            self.conn, symbol="TESTFIXED", interval="1m", decision_time_ms=ms("15:05:00"),
            candle={"timestamp": "2026-11-09T15:04:00Z", "open": 19, "high": 19, "low": 19, "close": 19, "volume": 1},
            indicators={"sma_short_window": 2, "sma_long_window": 3, "sma_short": 16.0, "sma_long": 13.0,
                        "short_vs_long": "above", "crossover_at_candle": "up"},
            account={"account_id": "test", "cash": 1000.0, "position": 0, "order_size": 1})
        fake = TestDoubleDecisions()
        d = decide(self.conn, snap["snapshot_id"], fake)
        self.assertEqual([q["name"] for q in d["questions"]], ["action"])
        self.assertEqual(d["chart_checks"], [])
        self.assertIsNone(d["image"])

    # ------------------------------------------------------------------ Claude translation with an image
    def test_claude_params_with_image(self):
        snap, raw = self.snapshot(), make_png()
        params = claude_params(build_request(snap, "claude-opus-5-5", data_url(raw)))
        blocks = params["messages"][0]["content"]
        self.assertEqual(len(params["messages"]), 1)
        self.assertEqual([b["type"] for b in blocks], ["text", "image", "text", "text"])
        self.assertEqual(blocks[1]["source"]["media_type"], "image/png")
        self.assertEqual(blocks[1]["source"]["data"], data_url(raw).split(",")[1])
        self.assertIn('"snapshot_id"', blocks[2]["text"])
        props = params["output_config"]["format"]["schema"]["properties"]
        self.assertEqual(list(props), ["action", "crossover", "evidence"])
        self.assertEqual(props["crossover"]["enum"], ["up", "down", "none", "unreadable"])


if __name__ == "__main__":
    unittest.main()
