"""Trading assistant chatbot for the PAPER account (Prompt 18). SIMULATED: nothing here reaches a broker.

The student can ask anything: how the app works, what an indicator means, general market or trading questions, or
"should I buy or sell now?". For questions about the symbol on screen, Claude gathers evidence through READ-ONLY
tools that call the app's own Python (it never calculates prices or P&L itself). When the question is about what to
do, it returns a structured recommendation for the paper account: buy, sell, close, hold or wait, with a quantity,
its own confidence estimate, reasons with evidence, the case against and what would change it.

  Tools (each one calls existing Python; results are stored with the turn)
    get_market_state      completed candles, the forming candle, SMAs and crossovers, configured indicators, data
                          source label, age of the latest observation, live feed status
    get_account           paper cash, position, average cost, realized / unrealized P&L, recent orders
    get_strategy_signal   what the fixed SMA crossover rule would do now (automation.rule_action, no snapshot stored)
    run_backtest          backtest.run_backtest (stores a simulated run only)
    get_recent_ai_decisions  recent analyst / reviewer decisions and chart-only answers for the symbol
    give_answer           the final answer (reply + optional recommendation); ends the turn

  Python check (check_recommendation), against the account at the time of the check:
    known action; whole quantity >= 1 for buy / sell; buy needs cash for quantity x last close; sell / close need
    shares (long only); the latest observation must be younger than ASSISTANT_STALE_S (default 300 s) and a
    completed candle must exist. Hold and wait are valid but place no order. Test data is flagged, not blocked.

  Nothing trades from the chat. place_order() runs only when the student clicks "Place this paper order": it
  repeats the Python check at that moment and calls paper.execute(origin="assistant") with request_id
  "assistant-turn-<id>", so a second click returns the same order.

DECISIONS_PROVIDER=test uses a deterministic, labelled test double (call_kind 'mock') instead of Claude.
"""

import json
import os
import sqlite3
import time

from backend import backtest
from backend.automation import rule_action
from backend.candles import iso_ms
from backend.decisions import CLAUDE_DEFAULT_MODEL
from backend.indicator_store import list_configs
from backend.indicators import configured_values, sma_crossover, validate_windows
from backend.paper import account_state, execute
from backend.pnl import history as pnl_history
from backend.recorder import now_ms
from backend.series import candle_series, data_sources

STALE_S = float(os.environ.get("ASSISTANT_STALE_S", "300"))
MAX_TOOL_ROUNDS = 6
ACTIONS = ("buy", "sell", "close", "hold", "wait")

SCHEMA = """
CREATE TABLE IF NOT EXISTS assistant_sessions (
    id          INTEGER PRIMARY KEY,
    symbol      TEXT NOT NULL,
    timeframe   TEXT NOT NULL,
    short       INTEGER NOT NULL,
    long        INTEGER NOT NULL,
    account_id  TEXT NOT NULL,
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    call_kind   TEXT NOT NULL,                 -- 'live' Claude or 'mock' test double
    created_ms  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS assistant_turns (
    id                   INTEGER PRIMARY KEY,
    session_id           INTEGER NOT NULL REFERENCES assistant_sessions(id),
    role                 TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text                 TEXT NOT NULL,
    created_ms           INTEGER NOT NULL,
    model                TEXT,
    latency_ms           INTEGER,
    status               TEXT,                 -- assistant: ok | tool_limit | timeout | provider_error | refusal
    tool_calls_json      TEXT,                 -- [{name, input, result}] in call order
    recommendation_json  TEXT,                 -- the model's recommendation, or NULL
    python_check_json    TEXT,                 -- check_recommendation() at answer time
    order_id             INTEGER               -- paper order placed from this turn (student's click)
);
"""

INSTRUCTIONS = """You are the trading assistant inside a student's market workstation. It is a course project with a
SIMULATED paper trading account: no real money and no real orders. The screen shows {symbol} on {timeframe} candles
with SMA({short}) and SMA({long}).

Answer any question the student asks, helpfully and plainly: how the app works, trading concepts, indicators,
general market knowledge, or anything else. When a question concerns the symbol, the chart or the account, use the
tools to get the facts; never invent or calculate prices, averages or P&L yourself; quote the numbers from tool
results with their times. Label general knowledge as general knowledge, not as data from the app.

When the student asks what to do (buy, sell, hold, enter, exit, the best action...), recommend the single best action
for THIS paper account from the evidence: buy, sell, close, hold or wait, with a whole-number quantity for buy or sell.
Weigh the SMA trend and crossover, what the fixed crossover rule says, how a backtest of that rule did, the account's
cash and position, and recent AI decisions when useful. Give your confidence as your own estimate, the reasons with
evidence, the strongest case against, and what would change your mind. Never claim certainty or predict prices. Say
so first when the data is labelled TEST or synthetic, or is stale. The account is long only: it cannot sell short.
If the student asks about real money, say this is a simulation and not personal financial advice.

Always finish by calling give_answer exactly once. Include a recommendation only when the student asked what to do."""

TOOLS = [
    {"name": "get_market_state",
     "description": "Latest completed candles, the forming candle (if any), SMA values and recent crossovers, configured "
                    "indicator values, the data source label (live Yahoo or TEST), the age of the latest observation "
                    "and the live feed status, for a symbol and timeframe.",
     "input_schema": {"type": "object", "properties": {
         "symbol": {"type": "string"}, "timeframe": {"type": "string", "enum": ["1m", "5m", "15m", "1h", "1d"]},
         "candles": {"type": "integer", "description": "How many completed candles to return (1-60, default 20)"}},
         "required": ["symbol", "timeframe"]}},
    {"name": "get_account",
     "description": "The paper account: cash, positions, average cost, realized and unrealized P&L for a symbol, and "
                    "recent orders.",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}},
    {"name": "get_strategy_signal",
     "description": "What the fixed SMA crossover rule would do now for the paper account: crossover at the latest "
                    "completed candle and the rule's action (buy, close, hold or wait).",
     "input_schema": {"type": "object", "properties": {
         "symbol": {"type": "string"}, "timeframe": {"type": "string"},
         "short": {"type": "integer"}, "long": {"type": "integer"}},
         "required": ["symbol", "timeframe", "short", "long"]}},
    {"name": "run_backtest",
     "description": "Backtest the SMA crossover rule on the stored candles (no look-ahead, fills at the next open, "
                    "zero fees). Returns the summary, buy-and-hold comparison and what the result leaves uncertain.",
     "input_schema": {"type": "object", "properties": {
         "symbol": {"type": "string"}, "timeframe": {"type": "string"},
         "short": {"type": "integer"}, "long": {"type": "integer"}},
         "required": ["symbol", "timeframe", "short", "long"]}},
    {"name": "get_recent_ai_decisions",
     "description": "Recent analyst/reviewer decisions and chart-only answers for a symbol.",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}, "timeframe": {"type": "string"}},
                      "required": ["symbol", "timeframe"]}},
    {"name": "give_answer",
     "description": "Your final answer to the student. Call exactly once, at the end. Include recommendation only "
                    "when the student asked what to do.",
     "strict": True,
     "input_schema": {
         "type": "object", "additionalProperties": False, "required": ["reply", "recommendation"],
         "properties": {
             "reply": {"type": "string", "description": "The answer in plain language."},
             "recommendation": {"anyOf": [{"type": "null"}, {
                 "type": "object", "additionalProperties": False,
                 "required": ["action", "quantity", "confidence", "reasons", "case_against", "would_change"],
                 "properties": {
                     "action": {"type": "string", "enum": list(ACTIONS)},
                     "quantity": {"type": "integer", "description": "Shares for buy or sell; 0 for close/hold/wait"},
                     "confidence": {"type": "number", "description": "Your own estimate, 0 to 1"},
                     "reasons": {"type": "array", "items": {
                         "type": "object", "additionalProperties": False, "required": ["point", "evidence"],
                         "properties": {"point": {"type": "string"},
                                        "evidence": {"type": "string", "description": "Numbers and times from tools"}}}},
                     "case_against": {"type": "array", "items": {"type": "string"}},
                     "would_change": {"type": "array", "items": {"type": "string"}},
                 }}]},
         }}},
]


class AssistantError(ValueError):
    pass


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# --------------------------------------------------------------------------- tools (read-only, except run_backtest)

def _latest_observation(conn, symbol: str) -> dict:
    row = conn.execute("SELECT MAX(first_received_ms), MAX(source_time_ms) FROM observations WHERE ticker = ? AND "
                       "first_received_ms <= ?", (symbol, now_ms())).fetchone()   # only what was known by now
    received = row[0] if row else None
    return {"latest_observation_received": iso_ms(received),
            "age_s": None if received is None else round((now_ms() - received) / 1000, 1)}


def market_state(conn, symbol: str, timeframe: str, short: int, long: int, feed_status=None, candles: int = 20) -> dict:
    candles = max(1, min(int(candles or 20), 60))
    series = candle_series(symbol, timeframe, now_ms(), conn=conn)
    rows = [{**c["candle"], "complete": c["status"]["complete"]} for c in series["candles"]]
    completed = [r for r in rows if r["complete"]]
    forming = next((r for r in reversed(rows) if not r["complete"]), None)
    sma = sma_crossover(rows, short, long)
    points = {p["timestamp"]: p for p in sma["points"]}
    last = completed[-1] if completed else None
    indicators = []
    try:
        for ind in configured_values(list_configs(conn, symbol), rows):
            value = next((p["value"] for p in reversed(ind["points"]) if p["value"] is not None), None)
            indicators.append({"name": ind["name"], "type": ind["type"], "latest": value, "units": ind["units"]})
    except sqlite3.OperationalError:
        pass
    return {
        "symbol": symbol, "timeframe": timeframe, "server_time": iso_ms(now_ms()),
        "data_source": series["source"], "data_labels": series["sources"],
        **_latest_observation(conn, symbol),
        "stale_after_s": STALE_S,
        "feed": feed_status(symbol) if feed_status else None,
        "completed_candles": [{k: c[k] for k in ("timestamp", "open", "high", "low", "close", "volume")}
                              for c in completed[-candles:]],
        "forming_candle": forming and {k: forming[k] for k in ("timestamp", "open", "high", "low", "close")},
        "sma": {"short_window": short, "long_window": long,
                "latest": last and {k: points[last["timestamp"]][k] for k in ("timestamp", "short", "long", "side",
                                                                               "signal")},
                "recent_crossovers": sma["crossovers"][-5:]},
        "indicators": indicators,
    }


def account(conn, account_id: str, symbol: str) -> dict:
    a = account_state(conn, account_id)
    rows = pnl_history(conn, account_id, symbol)
    last = rows[-1] if rows else None
    return {"account": account_id, "simulated": True, "cash": a["cash"], "starting_cash": a["starting_cash"],
            "positions": a["positions"], "shares_of_symbol": a["positions"].get(symbol, 0),
            "average_cost": last and last["average_cost"], "realized": last and last["realized"],
            "unrealized": last and last["unrealized"], "pnl_as_of": last and last["event_time"],
            "recent_orders": [{k: o.get(k) for k in ("id", "symbol", "side", "quantity", "status", "submitted_at",
                                                     "origin")} | {"fill_price": (o.get("fill") or {}).get("price")}
                              for o in a["orders"][:10]]}


def strategy_signal(conn, account_id: str, symbol: str, timeframe: str, short: int, long: int) -> dict:
    validate_windows(short, long)
    series = candle_series(symbol, timeframe, now_ms(), conn=conn)
    rows = [{**c["candle"], "complete": c["status"]["complete"]} for c in series["candles"]]
    completed = [r for r in rows if r["complete"]]
    if not completed:
        return {"available": False, "reason": "no completed candle"}
    point = next(p for p in sma_crossover(rows, short, long)["points"] if p["timestamp"] == completed[-1]["timestamp"])
    held = account_state(conn, account_id)["positions"].get(symbol, 0)
    snap = {"indicators": {"crossover_at_candle": point["signal"] or "none", "sma_short_window": short,
                           "sma_long_window": long}, "account": {"position": held, "order_size": 1}}
    action, reason = rule_action(snap)
    return {"available": True, "candle": completed[-1]["timestamp"], "sma_short": point["short"],
            "sma_long": point["long"], "side": point["side"], "crossover_at_candle": point["signal"] or "none",
            "shares_held": held, "rule_action": action, "rule_reason": reason}


def backtest_tool(conn, symbol: str, timeframe: str, short: int, long: int) -> dict:
    run = backtest.run_backtest(conn, symbol, timeframe, short, long, 1000)
    return {"run_id": run["id"], "candles": run["candle_count"], "first": run["first_candle"],
            "last": run["last_candle"], "data_source": run["data_source"], "summary": run["summary"],
            "uncertain": run["interpretation"]["uncertain"]}


def recent_ai(conn, symbol: str, timeframe: str) -> dict:
    out = {"decisions": [], "chart_only": []}
    try:
        from backend.voice import recent_decisions
        out["decisions"] = [d for d in recent_decisions(conn, 30) if d["symbol"] == symbol][:5]
    except sqlite3.OperationalError:
        pass
    try:
        from backend.image_decisions import list_image_decisions
        out["chart_only"] = [{k: d.get(k) for k in ("candle_ts", "stance", "python_signal", "call_kind")}
                             for d in list_image_decisions(conn, symbol, timeframe, limit=5)]
    except sqlite3.OperationalError:
        pass
    return out


def run_tool(conn, session: dict, name: str, args: dict, feed_status=None) -> dict:
    """Execute one tool call; errors come back as {"error": ...} so the model can continue."""
    sym = (args.get("symbol") or session["symbol"]).upper()
    tf = args.get("timeframe") or session["timeframe"]
    short, long = args.get("short") or session["short"], args.get("long") or session["long"]
    try:
        if name == "get_market_state":
            return market_state(conn, sym, tf, session["short"], session["long"], feed_status, args.get("candles", 20))
        if name == "get_account":
            return account(conn, session["account_id"], sym)
        if name == "get_strategy_signal":
            return strategy_signal(conn, session["account_id"], sym, tf, short, long)
        if name == "run_backtest":
            return backtest_tool(conn, sym, tf, short, long)
        if name == "get_recent_ai_decisions":
            return recent_ai(conn, sym, tf)
        return {"error": f"unknown tool {name}"}
    except (ValueError, KeyError) as exc:
        return {"error": str(exc)}


# --------------------------------------------------------------------------- Python check of a recommendation

def check_recommendation(conn, account_id: str, symbol: str, timeframe: str, rec: dict | None) -> dict | None:
    """Can this recommendation be placed in the paper account right now? Gates with pass/fail and details."""
    if not rec:
        return None
    action, qty = rec.get("action"), rec.get("quantity")
    a = account_state(conn, account_id)
    held, cash = a["positions"].get(symbol, 0), a["cash"]
    series = candle_series(symbol, timeframe, now_ms(), conn=conn)
    completed = [c["candle"] for c in series["candles"] if c["status"]["complete"]]
    last_close = completed[-1]["close"] if completed else None
    latest = _latest_observation(conn, symbol)
    gates = [{"gate": "known action", "passed": action in ACTIONS, "detail": str(action)}]
    trades = action in ("buy", "sell", "close")
    if action in ("buy", "sell"):
        ok = isinstance(qty, int) and not isinstance(qty, bool) and qty >= 1
        gates.append({"gate": "whole quantity of at least 1", "passed": ok, "detail": str(qty)})
    if action == "buy":
        need = (qty or 0) * last_close if last_close is not None else None
        gates.append({"gate": "cash for quantity x last close", "passed": need is not None and need <= cash,
                      "detail": f"need about {need:.2f}, cash {cash:.2f}" if need is not None else "no last close"})
    if action in ("sell", "close"):
        want = held if action == "close" else (qty or 0)
        gates.append({"gate": "shares held (long only)", "passed": held > 0 and want <= held,
                      "detail": f"{held} held, {want} to sell"})
    if trades:
        gates.append({"gate": "completed candle exists", "passed": bool(completed),
                      "detail": completed[-1]["timestamp"] if completed else "none"})
        fresh = latest["age_s"] is not None and latest["age_s"] <= STALE_S
        gates.append({"gate": f"latest observation younger than {STALE_S:.0f} s", "passed": fresh,
                      "detail": f"age {latest['age_s']} s" if latest["age_s"] is not None else "no observations"})
    labels = sorted(data_sources(conn, symbol)) if conn else []
    return {"actionable": trades and all(g["passed"] for g in gates), "places_order": trades,
            "gates": gates, "data_labels": labels, "test_data": "test" in labels,
            "order": ({"action": action, "quantity": (held if action == "close" else qty)} if trades else None),
            "checked_at": iso_ms(now_ms())}


# --------------------------------------------------------------------------- providers

class ClaudeAssistant:
    name, call_kind = "anthropic", "live"

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout_s: float | None = None):
        self.api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or os.environ.get("ASSISTANT_MODEL") or CLAUDE_DEFAULT_MODEL
        self.timeout_s = timeout_s if timeout_s is not None else float(os.environ.get("ASSISTANT_TIMEOUT_S", "90"))

    def respond(self, system: str, history: list[dict], call_tool) -> dict:
        """Manual tool-use loop: read-only tools until give_answer (or text-only end_turn)."""
        import anthropic
        if not self.api_key:
            raise AssistantError("ANTHROPIC_API_KEY is not set in the backend environment")
        client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout_s, max_retries=1)
        messages = list(history)
        model = self.model
        for _ in range(MAX_TOOL_ROUNDS + 1):
            try:
                response = client.beta.messages.create(
                    model=self.model, max_tokens=16000, system=system, messages=messages, tools=TOOLS,
                    tool_choice={"type": "auto"}, thinking={"type": "adaptive"}, output_config={"effort": "medium"},
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            except anthropic.APITimeoutError:
                return {"status": "timeout", "model": model}
            except anthropic.APIStatusError as exc:
                return {"status": "provider_error", "model": model, "error": f"HTTP {exc.status_code}: {exc.message}"[:300]}
            except anthropic.APIConnectionError as exc:
                return {"status": "provider_error", "model": model, "error": f"connection error: {exc}"[:300]}
            model = response.model
            if response.stop_reason == "refusal":
                return {"status": "refusal", "model": model}
            answer = next((b for b in response.content if b.type == "tool_use" and b.name == "give_answer"), None)
            if answer is not None:
                return {"status": "ok", "model": model, "answer": answer.input}
            if response.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue
            uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not uses:
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                return {"status": "ok", "model": model, "answer": {"reply": text, "recommendation": None}}
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(call_tool(b.name, b.input),
                                                                                    default=str)} for b in uses]})
        return {"status": "tool_limit", "model": model,
                "answer": {"reply": "I could not finish within the tool-call limit; please ask again more narrowly.",
                           "recommendation": None}}


class TestDoubleAssistant:
    """ASSISTANT TEST DOUBLE (DECISIONS_PROVIDER=test): no network. Calls the real tools in a fixed order; for a
    question about what to do, recommends the crossover rule's action (buy 1 / close / hold / wait)."""

    name, call_kind, model = "test-double", "mock", "test-double"

    def respond(self, system: str, history: list[dict], call_tool) -> dict:
        question = history[-1]["content"]
        market = call_tool("get_market_state", {})
        acct = call_tool("get_account", {})
        asks = any(w in question.lower() for w in ("buy", "sell", "should", "action", "close", "hold"))
        if not asks:
            return {"status": "ok", "model": self.model, "answer": {
                "reply": f"Test double reply to: {question} | data: {market.get('data_source')} | cash "
                         f"{acct.get('cash')}", "recommendation": None}}
        signal = call_tool("get_strategy_signal", {})
        action = signal.get("rule_action", "wait")
        qty = 1 if action == "buy" else 0
        return {"status": "ok", "model": self.model, "answer": {
            "reply": f"Test double: the SMA rule says {action} (crossover {signal.get('crossover_at_candle')}).",
            "recommendation": {
                "action": action, "quantity": qty, "confidence": 0.5,
                "reasons": [{"point": "fixed SMA crossover rule",
                             "evidence": signal.get("rule_reason", "no signal")}],
                "case_against": ["test double: no real analysis"], "would_change": ["a crossover the other way"]}}}


def make_assistant_provider():
    if os.environ.get("DECISIONS_PROVIDER", "claude").strip().lower() == "test":
        return TestDoubleAssistant()
    return ClaudeAssistant()


# --------------------------------------------------------------------------- sessions and turns

def start_session(conn, symbol: str, timeframe: str, short: int, long: int, account_id: str, provider) -> dict:
    validate_windows(short, long)
    ensure_schema(conn)
    with conn:
        sid = conn.execute(
            "INSERT INTO assistant_sessions (symbol, timeframe, short, long, account_id, provider, model, call_kind, "
            "created_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, timeframe, short, long, account_id, provider.name, getattr(provider, "model", "?"),
             provider.call_kind, now_ms())).lastrowid
    return get_session(conn, sid)


def _turn(r) -> dict:
    load = lambda v: json.loads(v) if v else None
    return {"id": r["id"], "role": r["role"], "text": r["text"], "created": iso_ms(r["created_ms"]),
            "model": r["model"], "latency_ms": r["latency_ms"], "status": r["status"],
            "tool_calls": load(r["tool_calls_json"]), "recommendation": load(r["recommendation_json"]),
            "python_check": load(r["python_check_json"]), "order_id": r["order_id"]}


def get_session(conn, session_id: int) -> dict | None:
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    s = conn.execute("SELECT * FROM assistant_sessions WHERE id = ?", (session_id,)).fetchone()
    if s is None:
        return None
    turns = [_turn(r) for r in conn.execute("SELECT * FROM assistant_turns WHERE session_id = ? ORDER BY id",
                                            (session_id,))]
    return {**{k: s[k] for k in s.keys()}, "created": iso_ms(s["created_ms"]), "turns": turns}


def ask(conn, session_id: int, text: str, provider, feed_status=None) -> dict:
    """One question -> tools as needed -> one stored answer with its recommendation and Python check."""
    session = get_session(conn, session_id)
    if session is None:
        raise AssistantError(f"no assistant session {session_id}")
    text = (text or "").strip()
    if not text:
        raise AssistantError("the question is empty")
    with conn:
        conn.execute("INSERT INTO assistant_turns (session_id, role, text, created_ms) VALUES (?, 'user', ?, ?)",
                     (session_id, text[:4000], now_ms()))
    history = [{"role": t["role"], "content": t["text"]} for t in session["turns"] if t["text"]]
    history.append({"role": "user", "content": text})
    calls = []

    def call_tool(name, args):
        result = run_tool(conn, session, name, dict(args or {}), feed_status)
        calls.append({"name": name, "input": args, "result": result})
        return result

    system = INSTRUCTIONS.format(**{k: session[k] for k in ("symbol", "timeframe", "short", "long")})
    started = time.monotonic()
    out = provider.respond(system, history, call_tool)
    latency = round((time.monotonic() - started) * 1000)
    answer = out.get("answer") or {}
    rec = answer.get("recommendation")
    reply = answer.get("reply") or {"timeout": "The model did not answer in time.",
                                    "refusal": "The model declined to answer this question.",
                                    "provider_error": f"The model could not be reached ({out.get('error', '')})."
                                    }.get(out["status"], "")
    check = check_recommendation(conn, session["account_id"], session["symbol"], session["timeframe"], rec)
    with conn:
        tid = conn.execute(
            "INSERT INTO assistant_turns (session_id, role, text, created_ms, model, latency_ms, status, "
            "tool_calls_json, recommendation_json, python_check_json) VALUES (?, 'assistant', ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, reply, now_ms(), out.get("model"), latency, out["status"],
             json.dumps(calls, default=str), json.dumps(rec) if rec else None,
             json.dumps(check) if check else None)).lastrowid
    return {"session": get_session(conn, session_id), "turn_id": tid}


def place_order(conn, turn_id: int) -> dict:
    """The student's click: re-check the turn's recommendation now and place it in the paper account (idempotent)."""
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT t.*, s.symbol, s.timeframe, s.account_id FROM assistant_turns t JOIN assistant_sessions s "
                     "ON s.id = t.session_id WHERE t.id = ?", (turn_id,)).fetchone()
    if r is None or r["role"] != "assistant":
        raise AssistantError(f"no assistant answer {turn_id}")
    if r["order_id"] is not None:
        from backend.paper import _order
        return {"order": _order(conn, r["order_id"]), "duplicate": True, "check": json.loads(r["python_check_json"])}
    rec = json.loads(r["recommendation_json"]) if r["recommendation_json"] else None
    if not rec:
        raise AssistantError("this answer has no recommendation to place")
    check = check_recommendation(conn, r["account_id"], r["symbol"], r["timeframe"], rec)
    if not check["places_order"]:
        raise AssistantError(f"'{rec['action']}' places no order")
    if not check["actionable"]:
        failed = "; ".join(g["gate"] for g in check["gates"] if not g["passed"])
        raise AssistantError(f"the Python check failed now: {failed}")
    order = check["order"]
    result = execute(conn, r["account_id"], r["symbol"], order["action"],
                     order["quantity"] if order["action"] in ("buy", "sell") else None,
                     f"assistant-turn-{turn_id}", origin="assistant")
    with conn:
        conn.execute("UPDATE assistant_turns SET order_id = ? WHERE id = ?", (result["order"]["id"], turn_id))
    return {"order": result["order"], "duplicate": result["duplicate"], "note": result["note"], "check": check}
