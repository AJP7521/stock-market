"""Compare chart_signal scenarios end to end (Prompt 16F): what is sent and what the panel shows.

    python3 -m backend.compare_chart_signal            provider test double (no network, repeatable)
    python3 -m backend.compare_chart_signal --live     Claude (uses ANTHROPIC_API_KEY from .env; about 16 calls)
    add --evidence up,down,mixed-higher,mixed-equal to run only some evidence rows (4 scenarios each)

TEST DATA only: a temporary SQLite database with the labelled TESTSIG fixture (backend/fixtures.py), FastAPI with
LIVE_FEED=off and TEST_FEED=on, Vite, and headless Chrome. The real paper account is never used: the position
context comes from the TEST_FEED-gated override POST /api/test/position, and every result is labelled
"TEST override". Ports 8000 and 5173 must be free.

Scenarios: four kinds of candle evidence x four position states.
    evidence  clear up (15:01: 11 -> 12), clear down (15:02: 12 -> 11),
              mixed: red candle but higher close (15:03: 11 -> 11.4),
              mixed: green candle but equal close (15:04: 11.4 -> 11.4)
    position  explicitly empty, open long, open short, unknown

For each scenario it clicks "Send chart image" in the Chart-only decision panel, then checks:
  * outgoing definitions: the question sent equals the approved JSON in prompts/16e-position-aware-chart-signal.md
    (parsed from that file, independent of the code), the context line names the expected position, is not stale,
    and carries no symbol or prices;
  * displayed answer: the answer, label, position and Python's expected answer on screen equal SQLite, and Python's
    answer equals the hand-written expectation below.
With the test double the provider answers the expected value, so any mismatch is a plumbing failure. With --live,
Claude's answer is recorded as agree / DISAGREE against the expectation; a disagreement is a finding about the model,
not a failure of the checks. Writes test-results/compare-<time>/report.md with one screenshot per scenario, and stops
every process and browser it started.
"""

import json
import re
import shutil
import sys
import traceback
from datetime import datetime, timezone

from backend.fixtures import SIGNAL_SYMBOL, write_signal_fixture
from backend.recorder import PROJECT_ROOT
from backend.smoke import UI, Smoke

EVIDENCE = [  # (key, description, cutoff minute, closes shown)
    ("up", "clear up: green candle, close 11 -> 12", 1, "11 -> 12"),
    ("down", "clear down: red candle, close 12 -> 11", 2, "12 -> 11"),
    ("mixed-higher", "mixed: RED candle but close higher, 11 -> 11.4", 3, "11 -> 11.4"),
    ("mixed-equal", "mixed: GREEN candle but close equal, 11.4 -> 11.4", 4, "11.4 -> 11.4"),
]
POSITIONS = [  # (key, override body, context sentence expected in the request, panel text expected)
    ("empty", {"state": "empty"}, "position exists = no; the position is explicitly empty.",
     "explicitly empty (TEST override)"),
    ("long", {"state": "open", "direction": "long"}, "position exists = yes; direction = long.",
     "open, long (TEST override)"),
    ("short", {"state": "open", "direction": "short"}, "position exists = yes; direction = short.",
     "open, short (TEST override)"),
    ("unknown", {"state": "unknown"}, "position state is unknown (missing).", "unknown (TEST override)"),
]
# Hand-written expectations (the rule table approved in Prompt 16E), independent of image_decisions.python_signal.
EXPECTED = {
    "up": {"empty": "long", "long": "hold", "short": "exit", "unknown": "wait"},
    "down": {"empty": "short", "long": "exit", "short": "hold", "unknown": "wait"},
    "mixed-higher": {"empty": "long", "long": "hold", "short": "exit", "unknown": "wait"},
    "mixed-equal": {"empty": "wait", "long": "wait", "short": "wait", "unknown": "wait"},
}
VALUES = ["long", "short", "hold", "exit", "wait"]


def approved_question() -> dict:
    """The question exactly as approved, parsed from the saved prompt (not from the code)."""
    text = (PROJECT_ROOT / "prompts" / "16e-position-aware-chart-signal.md").read_text()
    block = re.search(r"~~~json\n(.*?)\n~~~", text, re.S).group(1)
    return json.loads(block)


class Compare(Smoke):
    def __init__(self, live: bool, evidence: list[str] | None = None):
        super().__init__()
        self.live = live
        self.evidence = [e for e in EVIDENCE if not evidence or e[0] in evidence]
        self.rows: list[dict] = []
        stamp = self.out.name.removeprefix("smoke-")
        self.out.rmdir()
        self.out = PROJECT_ROOT / "test-results" / f"compare-{stamp}{'-live' if live else ''}"
        self.out.mkdir(parents=True, exist_ok=True)

    def backend_env(self) -> dict:
        env = super().backend_env()
        if self.live:
            env["DECISIONS_PROVIDER"] = "claude"
            env.pop("ANTHROPIC_API_KEY", None)      # let the backend read it from the ignored .env
        return env

    def scenario(self, n: int, evidence: tuple, position: tuple, question: dict):
        ev_key, ev_text, minute, closes = evidence
        pos_key, override, context_end, panel_text = position
        expected = EXPECTED[ev_key][pos_key]
        cutoff = f"2026-11-09T15:{minute:02d}:00Z"
        s = f"{n:02d} {ev_key} / {pos_key}"
        page = self.page
        self.clock(datetime(2026, 11, 9, 15, minute + 1, 20, tzinfo=timezone.utc))   # 20 s after it completed
        self.api("POST", "/api/test/position", override)
        if not self.live:
            probs = ";".join(f"{v}:{0.8 if v == expected else 0.05}" for v in VALUES)
            self.answers(f"chart_signal={expected},chart_signal@p={probs}")
        before = page.get_by_test_id("img-decision-id")
        previous = before.inner_text().strip() if before.count() else None
        page.get_by_test_id("img-cutoff").select_option(cutoff)
        page.get_by_test_id("img-button").click()
        page.wait_for_function(
            "prev => { const e = document.querySelector('[data-testid=img-decision-id]');"
            " return e && e.innerText.trim() !== prev && !document.querySelector('[data-testid=img-busy]') }",
            arg=previous, timeout=90000)
        image_id = int(self.text("img-decision-id").lstrip("#"))
        result = page.get_by_test_id("img-result")
        result.screenshot(path=str(self.out / f"scenario-{n:02d}-{ev_key}-{pos_key}.png"))
        shown = {
            "answer": page.get_by_test_id("img-stance").text_content().strip(),
            "label": page.get_by_test_id("img-label").get_attribute("data-label"),
            "status": self.text("img-status"),
            "position": self.text("img-position"),
            "python": self.text("img-python-signal"),
            "check": self.text("img-signal-check"),
            "bars": [" ".join(e.inner_text().split())
                     for e in page.get_by_test_id("img-probabilities").locator(".prob-row").all()]
            if page.get_by_test_id("img-probabilities").count() else [],
        }
        row = self.sql("SELECT status, stance, python_signal, position_state, position_direction, position_source, "
                       "request_json, probabilities_json, probabilities_check, latency_ms, stale "
                       "FROM image_decisions WHERE id = ?", (image_id,))[0]
        (status, stance, python, p_state, p_dir, p_source, request, probs_json, prob_check, latency, stale) = row
        request = json.loads(request)
        context = request["input"][1]["content"][0]["text"]
        outgoing = json.dumps(request).replace(request["input"][1]["content"][2]["image_url"], "")

        # outgoing definitions
        self.check(s, "question sent = approved JSON (prompts/16e)", question, request["questions"][0])
        self.check(s, "context names the position", True, context.endswith(context_end))
        self.check(s, "context: not stale", (True, 0), ("Context: not stale." in context, stale))
        self.check(s, "no symbol or candle prices sent", [],
                   [t for t in (SIGNAL_SYMBOL, "11.4", "12.3", '"close"', "shares") if t in outgoing])
        self.check(s, "stored position (state, direction, source)",
                   (override["state"], override.get("direction"), "TEST override"), (p_state, p_dir, p_source))
        # displayed answer vs stored vs expected
        self.check(s, "status", ("decided", "decided"), (status, shown["status"]))
        self.check(s, "answer on screen = SQLite", stance, shown["answer"])
        self.check(s, "position on screen", panel_text, shown["position"])
        self.check(s, "Python's answer = hand-written expectation", expected, python)
        self.check(s, "Python's answer on screen = SQLite", python, shown["python"])
        self.check(s, "match / CONFLICT on screen agrees with SQLite",
                   "match" if stance == python else "CONFLICT", shown["check"])
        self.check(s, "probability bars: five, in choice order", VALUES, [b.split()[0] for b in shown["bars"]])
        if not self.live:
            self.check(s, "test double answer shown", expected, shown["answer"])
            self.check(s, "label on screen", f"{expected} 80%", shown["label"])
        self.rows.append({"n": n, "evidence": ev_text, "closes": closes, "position": pos_key, "context": context_end,
                          "expected": expected, "python": python, "answer": stance, "label": shown["label"],
                          "bars": shown["bars"], "check": prob_check, "latency": latency, "id": image_id,
                          "agree": stance == expected})
        print(f"  {s}: expected {expected}, answer {stance} ({shown['label']})", flush=True)

    def write_report(self, error: str | None) -> None:
        failed = [r for r in self.results if not r["ok"]]
        agree = sum(r["agree"] for r in self.rows)
        provider = "Claude (live, claude-opus-5-5 by default)" if self.live else "provider test double (mocked)"
        lines = [f"# chart_signal scenario comparison {self.out.name}", "",
                 f"Checks: **{'PASS' if not failed and not error else 'FAIL'}** "
                 f"({len(self.results) - len(failed)}/{len(self.results)} passed). Provider: {provider}. "
                 f"Model answers equal to the expectation: **{agree}/{len(self.rows)}**.", "",
                 "Data: temporary SQLite DB with the labelled TESTSIG fixture (**test data**); position context from "
                 "the TEST_FEED-gated override (labelled TEST override); test clock 20 s after each cutoff completed "
                 "(not stale). No paper order is placed.", "",
                 "Outgoing definitions: every request carried the approved question (parsed from "
                 "`prompts/16e-position-aware-chart-signal.md`) and a context line ending with the position sentence "
                 "shown below.", "",
                 "| # | Evidence (newer close vs preceding) | Position | Context sent (end) | Expected | Python | "
                 "Answer shown | Label shown | Agree | Estimates (shown) | ms |",
                 "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in self.rows:
            lines.append(f"| {r['n']} | {r['evidence']} | {r['position']} | {r['context']} | {r['expected']} | "
                         f"{r['python']} | {r['answer']} | {r['label']} | {'agree' if r['agree'] else '**DISAGREE**'} "
                         f"| {', '.join(r['bars'])} ({r['check']}) | {r['latency']} |")
        if failed:
            lines += ["", "## Failed checks", "", "| Scenario | Check | Expected | Observed |", "|---|---|---|---|"]
            lines += [f"| {r['section']} | {r['check']} | `{r['expected']}` | `{r['observed']}` |" for r in failed]
        if error:
            lines += ["", "## Error", "", "```", error, "```"]
        (self.out / "report.md").write_text("\n".join(lines) + "\n")
        (self.out / "report.json").write_text(json.dumps({"rows": self.rows, "checks": self.results}, indent=2,
                                                         default=str))

    def run(self) -> int:
        error = None
        try:
            print(f"Comparison: artifacts in {self.out}", flush=True)
            write_signal_fixture(self.db)
            self.start_services()
            from playwright.sync_api import sync_playwright
            self.pw = sync_playwright().start()
            self.browser = self.pw.chromium.launch(channel="chrome", headless=True)
            self.page = self.browser.new_page(viewport={"width": 1280, "height": 1000})
            self.page.set_default_timeout(20000)
            console = open(self.out / "browser-console.log", "w")
            self.page.on("console", lambda m: console.write(f"{m.type}: {m.text}\n"))
            self.page.on("pageerror", lambda e: console.write(f"pageerror: {e}\n"))
            self.page.on("crash", lambda _p: console.write("CRASH: the page crashed\n"))
            self.browser.on("disconnected", lambda _b: console.write("DISCONNECTED: the browser closed\n"))
            self.page.goto(f"{UI}/?symbol={SIGNAL_SYMBOL}&interval=1m")
            self.page.get_by_test_id("img-cutoff").wait_for()
            question = approved_question()
            for evidence in self.evidence:
                for position in POSITIONS:
                    n = EVIDENCE.index(evidence) * len(POSITIONS) + POSITIONS.index(position) + 1
                    self.scenario(n, evidence, position, question)
        except Exception as exc:
            error = "".join(traceback.format_exception(exc))
            print(f"ERROR: {exc}", flush=True)
        finally:
            for release in (lambda: self.api("POST", "/api/test/position", {}),
                            lambda: self.api("POST", "/api/test/clock", {"time": None})):
                try:
                    release()
                except Exception:
                    pass
            for closer in (lambda: self.browser and self.browser.close(), lambda: self.pw and self.pw.stop()):
                try:
                    closer()
                except Exception:
                    pass
            self.stop_services()
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.write_report(error)
            failed = error or any(not r["ok"] for r in self.results)
            agree = sum(r["agree"] for r in self.rows)
            print(f"Comparison {'FAILED' if failed else 'PASSED'}: "
                  f"{len(self.results) - sum(not r['ok'] for r in self.results)}/{len(self.results)} checks; "
                  f"model answers equal to the expectation {agree}/{len(self.rows)}; report {self.out}/report.md",
                  flush=True)
        return 1 if failed else 0


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Compare chart_signal scenarios end to end (TEST data)")
    parser.add_argument("--live", action="store_true", help="ask Claude instead of the provider test double")
    parser.add_argument("--evidence", help="comma-separated subset of: " + ", ".join(e[0] for e in EVIDENCE))
    args = parser.parse_args()
    chosen = args.evidence.split(",") if args.evidence else None
    unknown = set(chosen or []) - {e[0] for e in EVIDENCE}
    if unknown:
        parser.error(f"unknown evidence: {', '.join(sorted(unknown))}")
    sys.exit(Compare(live=args.live, evidence=chosen).run())


if __name__ == "__main__":
    main()
