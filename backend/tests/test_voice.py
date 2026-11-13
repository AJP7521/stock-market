"""Prompt 13: the text side of the voice conversation (read-only record, sessions, turns), with the test double.

TEST DATA from test_automation (TESTAUTO, up crossover at 15:04, disposable paper-test account). No live model and no
audio: the browser's speech recognition and synthesis are checked in the browser.

    python3 -m unittest backend.tests.test_voice -v
"""

from backend import voice
from backend.decisions import TestDoubleDecisions, create_snapshot, decide
from backend.tests import test_automation as ta
from backend.tests.test_automation import LONG, SHORT, SYM, TF
from backend.tests.test_candles import ms


class FailingVoice:
    name, call_kind, model = "test-double", "mock", "test-double"

    def reply(self, summary, history):
        return {"text": "", "status": "provider_error", "model": self.model, "error": "connection error"}


class VoiceTests(ta.AutomationTests):
    # Reuse the automation fixture but not its tests.
    def _filled_decision(self):
        self.start()
        did = self.run_candle()["decision"]["decision_id"]
        self.price(20, "15:05:20")
        return did

    def counts(self):
        return [self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("paper_orders", "paper_fills", "decisions", "pnl_history")]

    def test_record_of_a_filled_decision(self):
        r = voice.record(self.conn, self._filled_decision())
        text = r["summary_text"]
        self.assertEqual((r["crossover"], r["proposal"], r["verdict"], r["outcome"]), ("up", "buy", "accept", "filled"))
        for fact in ("crossover up", "proposal buy", "verdict accept", "mocked test double", "TEST",
                     "Fill: 1 share(s) at 20.0", "cash after $980.00", "position after 1",
                     "Python final check: eligible", "now: cash $980.00, 1 TESTAUTO shares"):
            self.assertIn(fact, text)

    def test_record_of_a_vetoed_decision_says_no_trade(self):
        self.start()
        out = self.run_candle("action=buy,crossover=up,evidence=sufficient,verdict=reject_unsupported_signal")
        r = voice.record(self.conn, out["decision"]["decision_id"])
        self.assertEqual((r["verdict"], r["outcome"], r["fill"]), ("reject_unsupported_signal", "skipped", None))
        self.assertIn("No paper order was placed for this decision, so no simulated trade happened.", r["summary_text"])
        self.assertIn("failed: reviewer accepted", r["summary_text"])

    def test_record_of_a_manual_decision(self):
        snap = create_snapshot(self.conn, SYM, TF, SHORT, LONG, "paper", 1, ms("15:05:10"))
        d = decide(self.conn, snap["snapshot_id"], TestDoubleDecisions("action=buy"))
        r = voice.record(self.conn, d["decision_id"])
        self.assertIn("manual question", r["summary_text"])
        self.assertIn("no simulated trade happened", r["summary_text"])
        self.assertIn("No reviewer agent ran", r["summary_text"])

    def test_session_two_questions_keep_history_and_are_read_only(self):
        did = self._filled_decision()
        before = self.counts()
        provider = voice.TestDoubleVoice()
        s = voice.start_session(self.conn, did, provider)
        a1 = voice.ask(self.conn, s["session_id"], "What did the agents decide for this symbol?", provider)
        a2 = voice.ask(self.conn, s["session_id"], "What happened to the paper account?", provider)
        self.assertEqual((a1["status"], a1["call_kind"], a2["status"]), ("ok", "mock", "ok"))
        self.assertIn("proposal buy", a1["reply"])
        second = provider.received[1]
        self.assertEqual([m["role"] for m in second["history"]], ["user", "assistant", "user"])
        self.assertEqual(second["summary"], s["summary_text"])                 # same frozen record
        session = voice.get_session(self.conn, s["session_id"])
        self.assertEqual([t["role"] for t in session["turns"]], ["user", "assistant", "user", "assistant"])
        voice.end_session(self.conn, s["session_id"])
        with self.assertRaises(voice.VoiceError):
            voice.ask(self.conn, s["session_id"], "One more?", provider)
        self.assertEqual(self.counts(), before)                                 # nothing written outside voice

    def test_failed_reply_is_saved_and_left_out_of_history(self):
        did = self._filled_decision()
        s = voice.start_session(self.conn, did, voice.TestDoubleVoice())
        bad = voice.ask(self.conn, s["session_id"], "First question", FailingVoice())
        self.assertEqual(bad["status"], "provider_error")
        provider = voice.TestDoubleVoice()
        voice.ask(self.conn, s["session_id"], "Second question", provider)
        self.assertEqual(provider.received[0]["history"], [{"role": "user", "content": "Second question"}])

    def test_instructions_require_naming_missing_evidence(self):
        self.assertIn("say that the record does not show it and name the evidence that would be needed",
                      voice.INSTRUCTIONS)
        self.assertIn("read-only", voice.INSTRUCTIONS)

    def test_claude_voice_without_key(self):
        with self.assertRaises(voice.VoiceError):
            voice.ClaudeVoice(api_key="").reply("summary", [{"role": "user", "content": "hi"}])

    def test_unknown_decision(self):
        with self.assertRaises(voice.VoiceError):
            voice.start_session(self.conn, 999, voice.TestDoubleVoice())


# Run only this class's own tests (not the inherited automation tests) under this module.
for _name in [n for n in dir(ta.AutomationTests) if n.startswith("test_")]:
    if _name not in VoiceTests.__dict__:
        setattr(VoiceTests, _name, None)

if __name__ == "__main__":
    import unittest
    unittest.main()
