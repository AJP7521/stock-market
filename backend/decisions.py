"""Decision requests about one completed candle (Prompt 10).

Two separate steps:

1. create_snapshot() freezes, for one symbol and interval, as of the decision time:
   the last completed candle (timestamp, close, ...), the short and long SMAs and the
   crossover at that candle, and the paper account's cash and position. The snapshot gets
   an ID and is stored before any model call, so a chart can be drawn from exactly the
   same records first. In replay, the as-of time limits it to observations received by then.
   A snapshot missing any part (no completed candle, SMA warm-up, no account) is stored as
   incomplete and is never sent to the model or eligible for a paper order.
   create_test_snapshot() stores a clearly labelled kind='test' snapshot for isolated tests.

2. decide() sends a complete snapshot to a provider (by default the Claude Messages API with a
   structured output limited to the choices buy, sell, hold, wait, close; or, with
   DECISIONS_PROVIDER=openai, OpenAI POST /v1/decisions with a named choice question "action"), checks the answer against the
   snapshot and the long-only account rules, builds a short factual explanation from the
   recorded evidence, and stores everything under one decision ID. Statuses:
     decided           a valid choice was returned (validation may still reject it)
     malformed_answer  no usable "action" answer, or a value outside the five choices
     timeout           the provider did not answer within the timeout
     provider_error    HTTP or connection error, or a missing API key
     missing_input     unknown or incomplete snapshot (no provider call)
   A valid hold or wait is a decision. Nothing here touches the paper account.
"""

import json
import os
import socket
import sqlite3
import time
import urllib.error
import urllib.request
from decimal import Decimal

from backend.candles import iso_ms
from backend.indicators import sma_crossover
from backend.paper import ensure_schema as ensure_paper_schema
from backend.recorder import now_ms
from backend.series import candle_series

ACTIONS = {
    "buy": "Buy the configured order size of whole shares (adds to the position). Needs enough cash.",
    "sell": "Sell the configured order size from an open position (reduces it). Needs at least that many shares.",
    "hold": "Keep an existing open position unchanged. Only for an open position.",
    "wait": "Stay flat (no position) and do nothing. Only when no shares are held.",
    "close": "Sell the entire open position. Only for an open position.",
}

STRATEGY = (
    "You evaluate one decision for a long-only PAPER trading simulation (no real money). "
    "Strategy rules: an upward SMA crossover (short SMA moves above the long SMA) at this candle is a buy "
    "signal when the account can afford the order size; a downward crossover is an exit signal for an open "
    "position (close it, or sell the order size). Without a crossover, an open position is held and a flat "
    "account waits. Use only the snapshot data provided."
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_snapshots (
    id             INTEGER PRIMARY KEY,
    kind           TEXT NOT NULL CHECK (kind IN ('application', 'test')),
    symbol         TEXT NOT NULL,
    interval       TEXT NOT NULL,
    decision_ms    INTEGER NOT NULL,
    candle_ts      TEXT,
    complete       INTEGER NOT NULL,
    snapshot_json  TEXT NOT NULL,
    created_ms     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id                  INTEGER PRIMARY KEY,
    snapshot_id         INTEGER,
    created_ms          INTEGER NOT NULL,
    provider            TEXT NOT NULL,
    model               TEXT NOT NULL,
    call_kind           TEXT NOT NULL,          -- 'live' provider call or 'mock' test double
    status              TEXT NOT NULL CHECK (status IN
                          ('decided', 'malformed_answer', 'timeout', 'provider_error', 'missing_input')),
    proposal            TEXT,                   -- the model's choice (only if status = decided)
    probabilities_json  TEXT,
    confidence          REAL,
    validation_status   TEXT NOT NULL CHECK (validation_status IN ('valid', 'invalid', 'not_checked')),
    validation_json     TEXT NOT NULL,          -- list of rule checks with pass/fail
    eligible_for_order  INTEGER NOT NULL,
    explanation         TEXT NOT NULL,
    facts_json          TEXT NOT NULL,          -- what the backend supplied (from the snapshot)
    request_json        TEXT,
    response_json       TEXT,
    error               TEXT,
    latency_ms          INTEGER
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# --------------------------------------------------------------------------- snapshots

def _account_as_of(conn, account_id: str, as_of_ms: int) -> dict | None:
    """Cash and position from the paper ledger, counting only fills executed by `as_of_ms`."""
    ensure_paper_schema(conn)
    acct = conn.execute("SELECT starting_cash_micros FROM paper_accounts WHERE id = ?", (account_id,)).fetchone()
    if acct is None:
        return None
    cash = acct[0]
    positions: dict[str, int] = {}
    for side, symbol, qty, price in conn.execute(
            "SELECT side, symbol, quantity, price_micros FROM paper_fills WHERE account_id = ? AND executed_ms <= ? "
            "ORDER BY id", (account_id, as_of_ms)):
        sign = 1 if side == "buy" else -1
        cash -= sign * qty * price
        positions[symbol] = positions.get(symbol, 0) + sign * qty
    return {"cash_micros": cash, "positions": positions}


def _store_snapshot(conn, snap: dict) -> dict:
    ensure_schema(conn)
    with conn:
        sid = conn.execute(
            "INSERT INTO decision_snapshots (kind, symbol, interval, decision_ms, candle_ts, complete, snapshot_json, "
            "created_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (snap["kind"], snap["symbol"], snap["interval"], snap["decision_ms"],
             (snap.get("candle") or {}).get("timestamp"), int(snap["complete"]), "{}", now_ms())).lastrowid
        snap = {"snapshot_id": sid, **snap}
        conn.execute("UPDATE decision_snapshots SET snapshot_json = ? WHERE id = ?",
                     (json.dumps(snap, sort_keys=True), sid))
    return snap


def create_snapshot(conn: sqlite3.Connection, symbol: str, interval: str, short: int, long: int,
                    account_id: str, order_size: int, as_of_ms: int | None = None) -> dict:
    """Freeze the application data for one decision and store it. Never calls a model."""
    as_of_ms = as_of_ms if as_of_ms is not None else now_ms()
    series = candle_series(symbol, interval, as_of_ms, conn=conn)
    rows = [{**c["candle"], "complete": c["status"]["complete"]} for c in series["candles"]]
    completed = [r for r in rows if r["complete"]]
    missing = []
    candle = indicators = None
    if not completed:
        missing.append("no completed candle at the decision time")
    else:
        last = completed[-1]
        candle = {k: last[k] for k in ("timestamp", "open", "high", "low", "close", "volume")}
        point = next(p for p in sma_crossover(rows, short, long)["points"] if p["timestamp"] == last["timestamp"])
        indicators = {"sma_short_window": short, "sma_long_window": long, "sma_short": point["short"],
                      "sma_long": point["long"], "short_vs_long": point["side"],
                      "crossover_at_candle": point["signal"] or "none"}
        if point["short"] is None or point["long"] is None:
            missing.append(f"SMA warm-up: fewer than {long} completed candles")
    acct = _account_as_of(conn, account_id, as_of_ms)
    account = None
    if acct is None:
        missing.append(f"no paper account {account_id!r}")
    else:
        account = {"account_id": account_id, "cash": acct["cash_micros"] / 1_000_000,
                   "position": acct["positions"].get(symbol, 0), "order_size": order_size}
    snap = {
        "kind": "application", "symbol": symbol, "interval": interval,
        "decision_ms": as_of_ms, "decision_time": iso_ms(as_of_ms),
        "market_data_source": series["source"], "market_data_labels": series["sources"],
        "candle": candle, "indicators": indicators, "account": account,
        "complete": not missing, "missing": missing,
    }
    return _store_snapshot(conn, snap)


def create_test_snapshot(conn: sqlite3.Connection, *, symbol: str, interval: str, decision_time_ms: int,
                         candle: dict, indicators: dict, account: dict) -> dict:
    """Store a clearly labelled TEST snapshot (kind='test') built from given values."""
    missing = [name for name, part in (("candle", candle), ("indicators", indicators), ("account", account))
               if not part]
    snap = {"kind": "test", "symbol": symbol, "interval": interval, "decision_ms": decision_time_ms,
            "decision_time": iso_ms(decision_time_ms), "market_data_source": "TEST snapshot (constructed values)",
            "market_data_labels": ["test"], "candle": candle, "indicators": indicators, "account": account,
            "complete": not missing, "missing": missing}
    return _store_snapshot(conn, snap)


def load_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> dict | None:
    ensure_schema(conn)
    row = conn.execute("SELECT snapshot_json FROM decision_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    return json.loads(row[0]) if row else None


# --------------------------------------------------------------------------- provider

class ProviderTimeout(Exception):
    pass


class ProviderError(Exception):
    pass


def model_input(snapshot: dict) -> dict:
    """The part of the snapshot the model sees (no database ids beyond the snapshot id)."""
    return {k: snapshot[k] for k in ("snapshot_id", "kind", "symbol", "interval", "decision_time", "candle",
                                     "indicators", "account")}


def build_request(snapshot: dict, model: str) -> dict:
    return {
        "model": model,
        "input": [
            {"role": "developer", "content": [{"type": "input_text", "text": STRATEGY}]},
            {"role": "user", "content": [
                {"type": "input_text", "text": "Decision snapshot for one completed candle (paper account):"},
                {"type": "input_json", "data": model_input(snapshot)},
            ]},
        ],
        "questions": [{
            "type": "choice",
            "name": "action",
            "instructions": "Which action fits this paper position and the strategy rules at this candle?",
            "choices": [{"value": v, "description": d} for v, d in ACTIONS.items()],
        }],
    }


class OpenAIDecisions:
    """POST /v1/decisions with Python's standard library. The key comes from the backend environment."""

    name = "openai"
    call_kind = "live"

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None,
                 timeout_s: float | None = None):
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("DECISIONS_MODEL", "gpt-6-luna")
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.timeout_s = timeout_s if timeout_s is not None else float(os.environ.get("DECISIONS_TIMEOUT_S", "20"))

    def send(self, body: dict) -> dict:
        if not self.api_key:
            raise ProviderError("OPENAI_API_KEY is not set in the backend environment")
        req = urllib.request.Request(
            f"{self.base_url}/decisions", data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise ProviderError(f"HTTP {exc.code}: {detail}") from None
        except (socket.timeout, TimeoutError) as exc:
            raise ProviderTimeout(f"no response within {self.timeout_s:g} s") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise ProviderTimeout(f"no response within {self.timeout_s:g} s") from exc
            raise ProviderError(f"connection error: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError(f"response was not JSON: {exc}") from exc


CLAUDE_DEFAULT_MODEL = "claude-opus-5-5"


def claude_params(body: dict) -> dict:
    """Translate the provider-neutral request (built by build_request) into a Claude Messages API request.

    The named choice question becomes a structured-output schema: the reply must be {"action": <one of ACTIONS>}.
    """
    question = body["questions"][0]
    choices = [c["value"] for c in question["choices"]]
    guide = "\n".join(f"- {c['value']}: {c['description']}" for c in question["choices"])
    user = body["input"][1]["content"]
    return {
        "model": body["model"],
        "max_tokens": 4000,
        "system": body["input"][0]["content"][0]["text"],
        "messages": [{"role": "user", "content": (
            f"{user[0]['text']}\n{json.dumps(user[1]['data'], indent=2)}\n\n"
            f"Question '{question['name']}': {question['instructions']}\nAllowed answers:\n{guide}")}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": {
            "type": "object", "properties": {question["name"]: {"type": "string", "enum": choices}},
            "required": [question["name"]], "additionalProperties": False}}},
        # If the model declines, the API re-runs the request on a fallback model; response.model shows which ran.
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    }


class ClaudeDecisions:
    """Claude Messages API (official `anthropic` SDK) with a structured-output choice. Key: ANTHROPIC_API_KEY."""

    name = "anthropic"
    call_kind = "live"

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None,
                 timeout_s: float | None = None, max_retries: int = 1):
        self.api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or os.environ.get("DECISIONS_MODEL") or CLAUDE_DEFAULT_MODEL
        self.base_url = base_url
        self.timeout_s = timeout_s if timeout_s is not None else float(os.environ.get("DECISIONS_TIMEOUT_S", "20"))
        self.max_retries = max_retries

    def send(self, body: dict) -> dict:
        import anthropic                      # imported here so the OpenAI path works without the package
        if not self.api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set in the backend environment")
        client = anthropic.Anthropic(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout_s,
                                     max_retries=self.max_retries)
        params = claude_params(body)
        try:
            message = client.beta.messages.create(**params)
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeout(f"no response within {self.timeout_s:g} s") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"HTTP {exc.status_code}: {exc.message}"[:500]) from None
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"connection error: {exc}") from exc
        raw = message.to_dict()
        if message.stop_reason == "refusal":
            category = message.stop_details.category if message.stop_details else None
            raise ProviderError(f"model declined the request (refusal, category {category})")
        return {"model": message.model, "request_id": message._request_id,
                "answers": claude_answers(message, params), "raw_response": raw}


def claude_answers(message, params: dict) -> list:
    """The structured reply as the provider-neutral answers list; an unparsable reply gives no answers."""
    text = "".join(b.text for b in message.content if b.type == "text")
    if message.stop_reason != "end_turn":
        return []
    try:
        reply = json.loads(text)
    except json.JSONDecodeError:
        return []
    name = next(iter(params["output_config"]["format"]["schema"]["required"]))
    if not isinstance(reply, dict) or name not in reply:
        return []
    return [{"type": "choice", "name": name, "choice": reply[name], "probabilities": None, "confidence": None}]


def make_provider():
    """Claude (Anthropic) by default; DECISIONS_PROVIDER=openai selects OpenAI."""
    choice = os.environ.get("DECISIONS_PROVIDER", "claude").strip().lower()
    return OpenAIDecisions() if choice == "openai" else ClaudeDecisions()


# --------------------------------------------------------------------------- validation and explanation

def _money(v) -> str:
    return f"${Decimal(str(v)):,.2f}"


def check_rules(action: str, snapshot: dict) -> list[dict]:
    """Check a proposed action against the snapshot and the long-only account rules."""
    acct, close = snapshot["account"], Decimal(str(snapshot["candle"]["close"]))
    position, cash, size = acct["position"], Decimal(str(acct["cash"])), acct["order_size"]
    if action == "buy":
        cost = close * size
        return [{"rule": f"cash covers {size} share(s) at the candle close ({_money(cost)})",
                 "passed": cash >= cost, "observed": f"cash {_money(cash)}"}]
    if action == "sell":
        return [{"rule": f"position holds at least the order size ({size})", "passed": position >= size,
                 "observed": f"position {position}"}]
    if action in ("close", "hold"):
        return [{"rule": "an open position exists", "passed": position > 0, "observed": f"position {position}"}]
    return [{"rule": "no position is held (wait stays flat)", "passed": position == 0,
             "observed": f"position {position}"}]


def facts(snapshot: dict) -> dict:
    """What the backend supplied, independent of the model."""
    c, i, a = snapshot["candle"], snapshot["indicators"], snapshot["account"]
    return {"symbol": snapshot["symbol"], "interval": snapshot["interval"], "candle_time": c["timestamp"],
            "close": c["close"], "sma_short": i["sma_short"], "sma_long": i["sma_long"],
            "sma_windows": [i["sma_short_window"], i["sma_long_window"]], "short_vs_long": i["short_vs_long"],
            "crossover_at_candle": i["crossover_at_candle"], "cash": a["cash"], "position": a["position"],
            "order_size": a["order_size"], "market_data": snapshot["market_data_source"],
            "snapshot_kind": snapshot["kind"]}


def explain(snapshot: dict, status: str, proposal: str | None, checks: list[dict], model: str,
            error: str | None) -> str:
    f = facts(snapshot) if snapshot.get("complete") else None
    parts = []
    if f:
        parts.append(f"Backend facts: {f['symbol']} {f['interval']} candle {f['candle_time']} closed at "
                     f"{f['close']}; SMA{f['sma_windows'][0]} {f['sma_short']:.4f} vs SMA{f['sma_windows'][1]} "
                     f"{f['sma_long']:.4f} ({f['short_vs_long']}); crossover at this candle: "
                     f"{f['crossover_at_candle']}; paper cash {_money(f['cash'])}, position {f['position']} "
                     f"share(s), order size {f['order_size']}. Market data: {f['market_data']}.")
    if status == "decided":
        verdict = "passes" if all(c["passed"] for c in checks) else "fails"
        failed = "; ".join(f"{c['rule']} ({c['observed']})" for c in checks if not c["passed"])
        parts.append(f"Model proposal ({model}): {proposal}. Rule check {verdict}"
                     + (f": {failed}." if failed else "."))
    elif status == "missing_input":
        parts.append(f"No model call: {error}.")
    else:
        parts.append(f"No usable proposal ({status}): {error}.")
    return " ".join(parts)


# --------------------------------------------------------------------------- decide

def _store_decision(conn, **row) -> dict:
    ensure_schema(conn)
    with conn:
        did = conn.execute(f"INSERT INTO decisions ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                           tuple(row.values())).lastrowid
    return get_decision(conn, did)


def decide(conn: sqlite3.Connection, snapshot_id: int, provider) -> dict:
    """Ask the provider about one stored snapshot, validate the answer and store the decision."""
    ensure_schema(conn)
    snapshot = load_snapshot(conn, snapshot_id)
    base = {"snapshot_id": snapshot_id, "created_ms": now_ms(), "provider": provider.name,
            "model": provider.model, "call_kind": provider.call_kind}
    if snapshot is None or not snapshot["complete"]:
        error = (f"no snapshot {snapshot_id}" if snapshot is None
                 else "incomplete snapshot: " + "; ".join(snapshot["missing"]))
        return _store_decision(conn, **base, status="missing_input", proposal=None, probabilities_json=None,
                               confidence=None, validation_status="not_checked", validation_json="[]",
                               eligible_for_order=0,
                               explanation=explain(snapshot or {}, "missing_input", None, [], provider.model, error),
                               facts_json=json.dumps(facts(snapshot) if snapshot and snapshot["complete"] else {}),
                               request_json=None, response_json=None, error=error, latency_ms=None)

    body = build_request(snapshot, provider.model)
    started = time.monotonic()
    response = error = proposal = probabilities = confidence = None
    try:
        response = provider.send(body)
        answer = next((a for a in response.get("answers", []) if a.get("name") == "action"), None)
        if answer is None:
            status, error = "malformed_answer", "no answer named 'action' in the response"
        elif answer.get("choice") not in ACTIONS:
            status, error = "malformed_answer", f"choice {answer.get('choice')!r} is not one of {list(ACTIONS)}"
        else:
            status, proposal = "decided", answer["choice"]
            probabilities, confidence = answer.get("probabilities"), answer.get("confidence")
    except ProviderTimeout as exc:
        status, error = "timeout", str(exc)
    except ProviderError as exc:
        status, error = "provider_error", str(exc)
    except (AttributeError, TypeError) as exc:      # response was JSON but not the documented shape
        status, error = "malformed_answer", f"unexpected response shape: {exc}"
    latency = round((time.monotonic() - started) * 1000)

    checks = check_rules(proposal, snapshot) if status == "decided" else []
    valid = status == "decided" and all(c["passed"] for c in checks)
    eligible = valid and snapshot["kind"] == "application" and snapshot["complete"]
    return _store_decision(
        conn, **base, status=status, proposal=proposal,
        probabilities_json=json.dumps(probabilities) if probabilities is not None else None,
        confidence=confidence, validation_status=("valid" if valid else "invalid") if status == "decided"
        else "not_checked", validation_json=json.dumps(checks), eligible_for_order=int(eligible),
        explanation=explain(snapshot, status, proposal, checks, provider.model, error),
        facts_json=json.dumps(facts(snapshot)), request_json=json.dumps(body),
        response_json=json.dumps(response) if response is not None else None, error=error, latency_ms=latency)


def get_decision(conn: sqlite3.Connection, decision_id: int) -> dict | None:
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    if r is None:
        return None
    return {
        "decision_id": r["id"], "snapshot_id": r["snapshot_id"], "created_at": iso_ms(r["created_ms"]),
        "provider": r["provider"], "model": r["model"], "call_kind": r["call_kind"], "status": r["status"],
        "model_proposal": {"action": r["proposal"], "probabilities": json.loads(r["probabilities_json"] or "null"),
                           "confidence": r["confidence"]},
        "backend_facts": json.loads(r["facts_json"]),
        "validation": {"status": r["validation_status"], "checks": json.loads(r["validation_json"])},
        "eligible_for_order": bool(r["eligible_for_order"]), "explanation": r["explanation"],
        "error": r["error"], "latency_ms": r["latency_ms"],
        "snapshot": load_snapshot(conn, r["snapshot_id"]) if r["snapshot_id"] is not None else None,
    }
