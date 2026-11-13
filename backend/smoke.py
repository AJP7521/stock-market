"""Workstation SmokeTest (Prompt 14): one command, real UI, isolated data, stubbed providers.

    python3 -m backend.smoke            (add SMOKE_HEADED=1 to watch the browser)

What it does
------------
* Creates a fresh temporary SQLite database with the labelled TESTSMA fixture (a recorded TEST session), so every run
  starts from a clean paper account.
* Starts FastAPI (LIVE_FEED=off: no Yahoo; DECISIONS_PROVIDER=test: no model; TEST_FEED=on: test prices, test clock
  and test-double answers) and Vite, and waits until both answer.
* Drives Google Chrome (Playwright, headless) through three checks, comparing the screen and SQLite with expected
  values:
    1. Replay the recorded session, add an SMA, change its window, compare with a hand calculation.
    2. Ask about chart: decodable chart pixels and the matching symbol/candle at the model boundary, known answers on
       screen, fill count unchanged; then a provider failure shown on screen.
    3. Paper automation with the test clock: buy 4 @ 100, sell 1 @ 110, close 3 @ 105 -> $1,025, 0 shares,
       $25 realized, $0 unrealized; a veto and a duplicate add no fill; after Stop a new candle places no order.
* Writes test-results/smoke-<time>/report.md (and a screenshot, logs and the database on failure), stops the browser
  and every process it started (also on failure), and exits 1 if any expected result is missing.

Ports 8000 and 5173 must be free (the Vite proxy targets 8000).
"""

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from backend.fixtures import SMA_CLOSES, SMA_SYMBOL, write_sma_fixture
from backend.recorder import PROJECT_ROOT

API = "http://127.0.0.1:8000"
UI = "http://127.0.0.1:5173"
AUTO = "TESTAUTO"
T0 = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)      # test-clock base for check 3 (a Monday)
APPROVE_BUY = "action=buy,crossover=up,evidence=sufficient,verdict=accept"


class SmokeFailure(Exception):
    pass


class Smoke:
    def __init__(self):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.out = PROJECT_ROOT / "test-results" / f"smoke-{stamp}"
        self.out.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="smoke-db-"))
        self.db = self.tmp / "smoke.sqlite"
        self.procs: list[subprocess.Popen] = []
        self.results: list[dict] = []
        self.browser = self.page = self.pw = None

    # ------------------------------------------------------------------ reporting
    def check(self, section: str, what: str, expected, observed) -> bool:
        ok = expected == observed
        self.results.append({"section": section, "check": what, "expected": expected, "observed": observed, "ok": ok})
        print(f"  [{'PASS' if ok else 'FAIL'}] {section}: {what}: expected {expected!r}, observed {observed!r}",
              flush=True)
        return ok

    def report(self, error: str | None) -> Path:
        failed = [r for r in self.results if not r["ok"]]
        lines = [f"# SmokeTest {self.out.name}", "",
                 f"Result: **{'PASS' if not failed and not error else 'FAIL'}** "
                 f"({len(self.results) - len(failed)}/{len(self.results)} checks passed)", "",
                 "Data: temporary SQLite DB with the labelled TESTSMA fixture; TESTAUTO test prices on a test clock. "
                 "Providers: Yahoo off, provider test double (mocked).", "",
                 "| Section | Check | Expected | Observed | Result |", "|---|---|---|---|---|"]
        for r in self.results:
            lines.append(f"| {r['section']} | {r['check']} | {r['expected']} | {r['observed']} | "
                         f"{'pass' if r['ok'] else '**FAIL**'} |")
        if error:
            lines += ["", "## Error", "", "```", error, "```"]
        path = self.out / "report.md"
        path.write_text("\n".join(lines) + "\n")
        (self.out / "report.json").write_text(json.dumps({"results": self.results, "error": error}, indent=2,
                                                         default=str))
        return path

    # ------------------------------------------------------------------ processes
    def start_services(self):
        for port in (8000, 5173):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
                raise SmokeFailure(f"port {port} is already in use; stop the running server first")
            except urllib.error.HTTPError:
                raise SmokeFailure(f"port {port} is already in use; stop the running server first")
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                pass
        env = {**os.environ, "MARKET_DB": str(self.db), "LIVE_FEED": "off", "TEST_FEED": "on",
               "DECISIONS_PROVIDER": "test", "PAPER_ACCOUNT": "paper", "PAPER_STARTING_CASH": "1000",
               "PAPER_TEST_ACCOUNT": "paper-test", "PAPER_TEST_STARTING_CASH": "1000", "PAPER_ORDER_SIZE": "1",
               # Stubbed providers: no credentials reach the backend even if a local .env exists.
               "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "", "DECISIONS_TEST_ANSWERS": APPROVE_BUY}
        backend_log = open(self.out / "backend.log", "w")
        self.procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", "8000"],
            cwd=PROJECT_ROOT, env=env, stdout=backend_log, stderr=subprocess.STDOUT, start_new_session=True))
        npx = shutil.which("npx")
        if not npx:
            raise SmokeFailure("npx (Node.js) not found")
        vite_log = open(self.out / "vite.log", "w")
        self.procs.append(subprocess.Popen([npx, "vite", "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
                                           cwd=PROJECT_ROOT / "frontend", stdout=vite_log, stderr=subprocess.STDOUT,
                                           start_new_session=True))
        deadline = time.time() + 60
        while time.time() < deadline:
            for p in self.procs:
                if p.poll() is not None:
                    raise SmokeFailure(f"a service exited during start-up (see {self.out}/backend.log, vite.log)")
            try:
                health = json.load(urllib.request.urlopen(f"{UI}/api/health", timeout=2))
                if health.get("status") == "ok" and health.get("database") == str(self.db):
                    print(f"  services ready: FastAPI + Vite, database {self.db}", flush=True)
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise SmokeFailure("FastAPI and Vite were not ready within 60 s")

    def stop_services(self):
        for p in self.procs:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for p in self.procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                p.wait(timeout=5)

    # ------------------------------------------------------------------ helpers
    def api(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(API + path, method=method, headers={"Content-Type": "application/json"},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            return json.load(urllib.request.urlopen(req, timeout=30))
        except urllib.error.HTTPError as exc:
            raise SmokeFailure(f"{method} {path} -> HTTP {exc.code}: {exc.read().decode()[:300]}") from None

    def sql(self, query: str, args=()):
        conn = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        try:
            return conn.execute(query, args).fetchall()
        finally:
            conn.close()

    def clock(self, when: datetime):
        self.api("POST", "/api/test/clock", {"time": when.strftime("%Y-%m-%dT%H:%M:%SZ")})

    def answers(self, spec: str | None):
        self.api("POST", "/api/test/provider", {"answers": spec})

    def price(self, symbol: str, price: float, when: datetime) -> dict:
        self.clock(when)
        return self.api("POST", "/api/test/observations", {"symbol": symbol, "price": price})

    def text(self, testid: str) -> str:
        return self.page.get_by_test_id(testid).inner_text().strip()

    def wait_text(self, testid: str, expected: str, timeout_s: float = 15) -> str:
        deadline, seen = time.time() + timeout_s, None
        while time.time() < deadline:
            loc = self.page.get_by_test_id(testid)
            if loc.count():
                seen = loc.first.inner_text().strip()
                if expected in seen:
                    return seen
            time.sleep(0.25)
        return seen

    # ------------------------------------------------------------------ check 1
    def check_replay_sma(self):
        s = "1 replay + SMA"
        page = self.page
        page.goto(f"{UI}/?symbol={SMA_SYMBOL}&interval=1m")
        page.get_by_test_id("mode-replay").click()
        page.wait_for_function("document.querySelector('[data-testid=replay-symbol]')?.value === 'TESTSMA'")
        page.get_by_test_id("replay-speed").fill("200")

        def replay_to_end():
            # At 200 steps/s the 8 recorded observations replay in well under a second; wait for the last step.
            page.get_by_test_id("replay-start-button").click()
            page.wait_for_function(
                "document.querySelector('[data-testid=replay-progress]')?.innerText.trim().match(/^(\\d+) \\/ \\1$/)")

        def add_sma(name: str, window: int):
            page.get_by_test_id("indicator-type").select_option("sma")
            page.get_by_test_id("indicator-name").fill(name)
            page.get_by_test_id("indicator-setting-window").fill(str(window))
            page.get_by_test_id("indicator-add").click()
            page.get_by_test_id(f"indicator-item-{name}").wait_for()

        def cell(ts: str, name: str) -> str:
            table = page.get_by_test_id("indicator-values")
            table.wait_for()
            headers = [h.strip() for h in table.locator("thead th").all_inner_texts()]
            row = page.get_by_test_id(f"values-{ts}")
            return row.locator("td").nth(headers.index(name)).inner_text().strip()

        def hand_sma(minute: int, window: int) -> str:            # independent of the app's code
            closes = [Decimal(c) for c in SMA_CLOSES[minute - window + 1:minute + 1]]
            return str((sum(closes) / window).normalize())

        replay_to_end()
        self.check(s, "replay progress reached the end", True,
                   bool(page.get_by_test_id("replay-progress").inner_text().strip()))
        add_sma("SMA3", 3)
        replay_to_end()
        self.check(s, "SMA3 at 15:04 (hand: (7+13+19)/3)", hand_sma(4, 3), cell("2026-11-09T15:04:00Z", "SMA3"))
        # change the window: the panel adds and removes indicators, so remove SMA3 and add the same SMA with window 2
        page.get_by_test_id("indicator-item-SMA3").get_by_role("button", name="Remove").click()
        page.get_by_test_id("indicator-item-SMA3").wait_for(state="detached")
        add_sma("SMA2", 2)
        replay_to_end()
        self.check(s, "SMA2 at 15:04 (hand: (13+19)/2)", hand_sma(4, 2), cell("2026-11-09T15:04:00Z", "SMA2"))
        self.check(s, "SMA2 at 15:06 (hand: (7+1)/2)", hand_sma(6, 2), cell("2026-11-09T15:06:00Z", "SMA2"))
        self.check(s, "stored indicator after the change", [("SMA2", "sma", {"window": 2})],
                   [(n, t, json.loads(st)) for n, t, st in self.sql("SELECT name, type, settings FROM indicator_configs")])

    # ------------------------------------------------------------------ check 2
    def check_ask_about_chart(self):
        s = "2 ask about chart"
        page = self.page
        page.get_by_test_id("mode-live").click()
        page.get_by_test_id("sma-short").fill("2")
        page.get_by_test_id("sma-long").fill("3")
        page.get_by_test_id("sma-apply").click()
        page.get_by_test_id("ask-cutoff").select_option("2026-11-09T15:04:00Z")
        fills_before = self.sql("SELECT count(*) FROM paper_fills")[0][0]
        self.answers(APPROVE_BUY)
        page.get_by_test_id("ask-button").click()
        page.get_by_test_id("ask-status").wait_for(timeout=20000)
        decision_id = int(self.text("ask-decision-id").lstrip("#"))
        self.check(s, "status on screen", "decided", self.text("ask-status"))
        self.check(s, "model call label", "MOCK (provider test double)", self.text("ask-call-kind"))
        for name, value in (("action", "buy"), ("crossover", "up"), ("evidence", "sufficient")):
            self.check(s, f"{name} answer on screen", value, self.text(f"ask-answer-{name}-value"))
        self.check(s, "crossover compared with Python", "Python calculated up: match",
                   self.text("ask-answer-crossover-check"))
        self.check(s, "reviewer verdict on screen", "accept", self.text("ask-review-verdict"))
        self.check(s, "eligibility on screen", "Eligible for automated execution", self.text("ask-eligibility"))
        # the model boundary: what the test double received
        resp, image_json, review_json = self.sql(
            "SELECT response_json, image_json, review_json FROM decisions WHERE id = ?", (decision_id,))[0]
        resp, image, review = json.loads(resp), json.loads(image_json), json.loads(review_json)
        got = resp["received_images"][0]
        self.check(s, "analyst received a decodable PNG", True, got["decodable"])
        self.check(s, "received PNG = stored capture (sha256)", image["sha256"], got["sha256"])
        self.check(s, "analyst payload symbol and candle time", {"symbol": SMA_SYMBOL,
                   "candle_time": "2026-11-09T15:04:00Z"},
                   {k: resp["received_snapshot"][k] for k in ("symbol", "candle_time")})
        self.check(s, "reviewer received the same PNG", image["sha256"],
                   review["response"]["received_images"][0]["sha256"])
        self.check(s, "reviewer payload symbol and candle time", {"symbol": SMA_SYMBOL,
                   "candle_time": "2026-11-09T15:04:00Z"},
                   {k: review["response"]["received_snapshot"][k] for k in ("symbol", "candle_time")})
        pixels = page.evaluate("""async (url) => {
            const img = new Image(); img.src = url; await img.decode();
            const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
            const g = c.getContext('2d'); g.drawImage(img, 0, 0);
            const d = g.getImageData(0, 0, c.width, c.height).data; const colours = new Set();
            for (let i = 0; i < d.length; i += 4 * 97) colours.add((d[i] << 16) | (d[i + 1] << 8) | d[i + 2]);
            return {w: c.width, h: c.height, colours: colours.size};
        }""", f"{UI}/api/decisions/{decision_id}/image")
        self.check(s, "stored PNG decodes in the browser with chart pixels (>= 5 colours)", True,
                   pixels["w"] > 0 and pixels["h"] > 0 and pixels["colours"] >= 5)
        self.check(s, "fill count unchanged", fills_before, self.sql("SELECT count(*) FROM paper_fills")[0][0])
        # provider failure
        self.answers("fail=provider_error")
        page.get_by_test_id("ask-button").click()
        status = self.wait_text("ask-status", "provider_error", 20)
        self.check(s, "failure status on screen", "provider_error", status)
        self.check(s, "failure shown as not eligible", True, self.text("ask-eligibility").startswith("Not eligible"))
        self.check(s, "failure reason in the on-screen explanation", True,
                   "No usable proposal (provider_error): test double: simulated provider failure"
                   in self.text("ask-explanation"))
        self.check(s, "fill count unchanged after the failure", fills_before,
                   self.sql("SELECT count(*) FROM paper_fills")[0][0])

    # ------------------------------------------------------------------ check 3
    def at(self, minute: int, second: int) -> datetime:
        return T0 + timedelta(minutes=minute, seconds=second)

    def candle(self, minute: int) -> str:
        return self.at(minute, 0).strftime("%Y-%m-%dT%H:%M:%SZ")

    def wait_run(self, minute: int, statuses: set[str], timeout_s: float = 25) -> dict:
        deadline, run = time.time() + timeout_s, None
        while time.time() < deadline:
            view = self.api("GET", f"/api/automation/status?symbol={AUTO}&timeframe=1m&short=2&long=3")
            run = next((r for r in view["runs"] if r["candle_ts"] == self.candle(minute)), None)
            if run and run["status"] in statuses:
                return run
            time.sleep(0.4)
        raise SmokeFailure(f"run for {self.candle(minute)} did not reach {statuses}; last seen {run}")

    def fills(self) -> int:
        return self.sql("SELECT count(*) FROM paper_fills WHERE account_id = 'paper-test'")[0][0]

    def set_running(self, order_size: int):
        page = self.page
        page.get_by_test_id("auto-order-size").fill(str(order_size))
        page.get_by_test_id("auto-start").click()
        self.check("3 automation", f"Running with order size {order_size}", "Running",
                   self.wait_text("auto-state", "Running"))

    def stop_running(self):
        self.page.get_by_test_id("auto-stop").click()
        self.check("3 automation", "Stopped", "Stopped", self.wait_text("auto-state", "Stopped"))

    def check_automation(self):
        s = "3 automation"
        page = self.page
        for minute, close in enumerate([13, 10, 7, 13, 19]):            # m4: SMA2 16 > SMA3 13 -> up
            self.price(AUTO, close, self.at(minute, 10))
        page.goto(f"{UI}/?symbol={AUTO}&interval=1m")
        page.get_by_test_id("sma-short").fill("2")
        page.get_by_test_id("sma-long").fill("3")
        page.get_by_test_id("sma-apply").click()
        self.clock(self.at(4, 20))
        page.get_by_test_id("auto-start").wait_for()
        self.set_running(4)
        self.check(s, "clean test account at start (account, cash, shares, fills)",
                   ("paper-test", "$1,000.00", "0", "0"),
                   tuple(self.text(t) for t in ("auto-account-id", "auto-cash", "auto-shares", "auto-fills")))

        # buy 4 on the up crossover at m4, filled at 100
        self.answers(APPROVE_BUY)
        self.clock(self.at(5, 5))
        buy = self.wait_run(4, {"order_pending"})
        self.price(AUTO, 100, self.at(5, 10))
        self.price(AUTO, 5, self.at(5, 50))                               # m5 close 5 -> down crossover
        self.check(s, "buy filled once", 1, self.fills())
        # duplicate events: the same decision applied again and the same candle claimed again
        dup = self.api("POST", "/api/automation/apply", {"decision_id": buy["decision_id"]})
        again = self.api("POST", "/api/automation/runs", {"symbol": AUTO, "timeframe": "1m", "short": 2, "long": 3,
                                                          "candle_time": self.candle(4)})
        self.check(s, "duplicate apply and claim recognised", (True, False), (dup["duplicate"], again["created"]))
        self.check(s, "duplicate added no fill", 1, self.fills())

        # partial sale: order size 1, sell on the down crossover at m5, filled at 110
        self.clock(self.at(5, 55))
        self.stop_running()
        self.set_running(1)
        self.answers("action=sell,crossover=down,evidence=sufficient,verdict=accept")
        self.clock(self.at(6, 5))
        self.wait_run(5, {"order_pending"})
        self.price(AUTO, 110, self.at(6, 10))
        self.price(AUTO, 40, self.at(6, 50))                              # m6 close 40 -> up crossover
        self.check(s, "sale filled", 2, self.fills())

        # veto on the up crossover at m6
        self.answers("action=buy,crossover=up,evidence=sufficient,verdict=reject_unsupported_signal")
        self.clock(self.at(7, 5))
        veto = self.wait_run(6, {"skipped"})
        self.price(AUTO, 10, self.at(7, 50))                              # m7 close 10 -> no crossover
        self.check(s, "veto skipped by the reviewer", "not eligible: reviewer accepted", veto["reason"])
        self.check(s, "veto added no fill", 2, self.fills())

        # hold at m7 (approved, no order), then close on the down crossover at m8, filled at 105
        self.answers("action=hold,crossover=none,evidence=sufficient,verdict=accept")
        self.clock(self.at(8, 5))
        self.wait_run(7, {"no_order"})
        self.price(AUTO, 1, self.at(8, 50))                               # m8 close 1 -> down crossover
        self.answers("action=close,crossover=down,evidence=sufficient,verdict=accept")
        self.clock(self.at(9, 5))
        close = self.wait_run(8, {"order_pending"})
        self.price(AUTO, 105, self.at(9, 10))
        self.check(s, "close filled", 3, self.fills())

        # on screen
        self.check(s, "cash on screen", "$1,025.00", self.wait_text("auto-cash", "$1,025.00", 10))
        self.check(s, "shares on screen", "0", self.text("auto-shares"))
        self.check(s, "fills on screen", "3", self.text("auto-fills"))
        page.get_by_test_id(f"auto-trace-{close['decision_id']}").click()
        self.check(s, "realized P&L on screen (trace)", "$25.00", self.wait_text("trace-pnl-realized", "$25.00", 10))
        self.check(s, "unrealized P&L on screen (trace)", "$0.00", self.text("trace-pnl-unrealized"))
        # in SQLite
        self.check(s, "SQLite cash and position", (1_025_000_000, 0), tuple(self.sql(
            "SELECT a.cash_micros, p.quantity FROM paper_accounts a JOIN paper_positions p ON p.account_id = a.id "
            "WHERE a.id = 'paper-test' AND p.symbol = ?", (AUTO,))[0]))
        self.check(s, "SQLite fills (side, qty, price)", [("buy", 4, 100.0), ("sell", 1, 110.0), ("close", 3, 105.0)],
                   [tuple(r) for r in self.sql("SELECT side, quantity, price FROM paper_fills "
                                               "WHERE account_id = 'paper-test' ORDER BY id")])
        self.check(s, "SQLite last P&L row (realized, unrealized)", (25_000_000, 0), tuple(self.sql(
            "SELECT realized_micros, unrealized_micros FROM pnl_history WHERE account_id = 'paper-test' "
            "ORDER BY id DESC LIMIT 1")[0]))
        self.check(s, "every fill linked to its decision", 3, self.sql(
            "SELECT count(*) FROM paper_fills f JOIN decisions d ON d.id = f.decision_id "
            "WHERE f.account_id = 'paper-test'")[0][0])
        self.check(s, "main paper account untouched", (1_000_000_000, 0), tuple(self.sql(
            "SELECT cash_micros, (SELECT count(*) FROM paper_fills WHERE account_id = 'paper') FROM paper_accounts "
            "WHERE id = 'paper'")[0]))

        # Stop, then another candle: no order
        orders_before = self.sql("SELECT count(*) FROM paper_orders")[0][0]
        self.stop_running()
        self.answers(APPROVE_BUY)
        self.price(AUTO, 99, self.at(10, 10))
        self.clock(self.at(11, 5))
        time.sleep(6)                                                     # three polling cycles of the panel
        self.check(s, "after Stop: no run for the new candles", [],
                   [r for r in self.api("GET", f"/api/automation/status?symbol={AUTO}&timeframe=1m&short=2&long=3")
                    ["runs"] if r["candle_ts"] >= self.candle(9)])
        self.check(s, "after Stop: no new order", orders_before, self.sql("SELECT count(*) FROM paper_orders")[0][0])

    # ------------------------------------------------------------------ run
    def run(self) -> int:
        error = None
        try:
            print(f"SmokeTest: artifacts in {self.out}", flush=True)
            write_sma_fixture(self.db)
            self.start_services()
            from playwright.sync_api import sync_playwright
            self.pw = sync_playwright().start()
            channel = os.environ.get("SMOKE_BROWSER_CHANNEL", "chrome") or None
            headless = os.environ.get("SMOKE_HEADED", "") != "1"
            self.browser = self.pw.chromium.launch(channel=channel, headless=headless)
            self.page = self.browser.new_page(viewport={"width": 1280, "height": 900})
            self.page.set_default_timeout(15000)
            console = open(self.out / "browser-console.log", "w")
            self.page.on("console", lambda m: console.write(f"{m.type}: {m.text}\n"))
            for step in (self.check_replay_sma, self.check_ask_about_chart, self.check_automation):
                print(f"- {step.__name__}", flush=True)
                step()
        except Exception as exc:
            error = "".join(traceback.format_exception(exc))
            print(f"ERROR: {exc}", flush=True)
        finally:
            failed = error or any(not r["ok"] for r in self.results)
            if failed and self.page is not None:
                try:
                    self.page.screenshot(path=str(self.out / "failure.png"), full_page=True)
                except Exception:
                    pass
            for closer in (lambda: self.browser and self.browser.close(), lambda: self.pw and self.pw.stop()):
                try:
                    closer()
                except Exception:
                    pass
            self.stop_services()
            if failed and self.db.exists():
                shutil.copy(self.db, self.out / "smoke.sqlite")
            shutil.rmtree(self.tmp, ignore_errors=True)
            path = self.report(error)
            passed = sum(r["ok"] for r in self.results)
            print(f"SmokeTest {'FAILED' if failed else 'PASSED'}: {passed}/{len(self.results)} checks; report {path}",
                  flush=True)
        return 1 if failed else 0


def main() -> None:
    sys.exit(Smoke().run())


if __name__ == "__main__":
    main()
