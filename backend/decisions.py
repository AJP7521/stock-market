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

3. Chart questions (Prompt 10A). create_snapshot(candle_time=...) freezes the snapshot as of the moment the
   selected candle completed and adds a "chart" section (the completed candles, SMA lines and configured indicator
   lines through that cutoff) so the browser renders and captures exactly those records. decide(image=...) checks
   the PNG (chart_images.read_png) and that the capture matches the snapshot, stores the PNG beside the database,
   sends it as input_image with the snapshot as input_json in the same user message, and asks three named
   questions: action, crossover and evidence. Each answer is validated by name. An unreadable chart, a missing
   answer, a visual crossover that conflicts with the Python crossover, or insufficient evidence makes the decision
   ineligible for automated execution.
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
from backend.chart_images import ImageRejected, data_url, read_png, store_png
from backend.indicator_store import list_configs
from backend.indicators import configured_values, sma_crossover
from backend.paper import ensure_schema as ensure_paper_schema
from backend.recorder import now_ms
from backend.series import candle_series
from backend.timeframes import MINUTE_MS, to_ms

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

ACTION_QUESTION = {
    "type": "choice", "name": "action",
    "instructions": "Which action fits this paper position and the strategy rules at this candle?",
    "choices": [{"value": v, "description": d} for v, d in ACTIONS.items()],
}
CHART_QUESTIONS = [
    {"type": "choice", "name": "action",
     "instructions": ("Which action fits this paper position and the strategy rules? Buy adds shares, sell reduces "
                      "the position, hold keeps an open position, wait stays flat, and close exits the entire "
                      "position."),
     "choices": [{"value": v, "description": d} for v, d in ACTIONS.items()]},
    {"type": "choice", "name": "crossover",
     "instructions": ("At the selected candle (the last candle on the chart, at the cutoff), is an upward "
                      "crossover, a downward crossover, or no crossover visible in the image?"),
     "choices": [{"value": "up", "description": "The short SMA line crosses above the long SMA line at that candle."},
                 {"value": "down", "description": "The short SMA line crosses below the long SMA line at that candle."},
                 {"value": "none", "description": "No crossover is visible at that candle."},
                 {"value": "unreadable", "description": "The image cannot be read well enough to tell."}]},
    {"type": "choice", "name": "evidence",
     "instructions": "Is the visible chart sufficient to assess the signal at the cutoff?",
     "choices": [{"value": "sufficient", "description": "The chart shows enough to assess the signal at the cutoff."},
                 {"value": "insufficient", "description": "The chart does not show enough to assess it."}]},
]
CHART_CANDLES_DEFAULT = 60

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


# Columns added after Prompt 10 (added to an existing table if missing).
ADDED_COLUMNS = {
    "questions_json": "TEXT",          # the named questions asked
    "answers_json": "TEXT",            # every answer by name, as returned
    "chart_checks_json": "TEXT",       # chart-answer checks (Prompt 10A)
    "image_path": "TEXT",              # capture PNG, relative to the database folder
    "image_json": "TEXT",              # width, height, bytes, sha256 of the capture
    "reviewer_status": "TEXT",         # Prompt 11: reviewed | malformed_answer | timeout | provider_error | not_run
    "verdict": "TEXT",                 # the reviewer's verdict
    "reviewer_model": "TEXT",
    "review_json": "TEXT",             # reviewer request (image as a reference), response, latency, evidence
    "strategy_json": "TEXT",           # strategy-rule checks of the proposal
    "final_check_json": "TEXT",        # every gate with pass/fail, and the final eligibility
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
    for name, kind in ADDED_COLUMNS.items():
        if name not in have:
            conn.execute(f"ALTER TABLE decisions ADD COLUMN {name} {kind}")
    conn.commit()


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


def _candle_end_ms(item: dict, interval: str) -> int:
    if interval == "1m":
        return to_ms(item["candle"]["timestamp"]) + MINUTE_MS
    return to_ms(item["status"]["interval_end"])


def _chart_section(conn, symbol: str, rows: list[dict], short: int, long: int, chart_candles: int) -> dict:
    """The completed candles (last `chart_candles`) through the cutoff, with SMA and configured indicator lines."""
    completed = [r for r in rows if r["complete"]]
    sma = sma_crossover(completed, short, long)
    shown = completed[-chart_candles:]
    keep = {r["timestamp"] for r in shown}
    indicators = configured_values(list_configs(conn, symbol), completed)
    return {
        "range": {"first": shown[0]["timestamp"] if shown else None, "last": shown[-1]["timestamp"] if shown else None,
                  "candles": len(shown), "requested": chart_candles},
        "candles": [{k: r[k] for k in ("timestamp", "open", "high", "low", "close", "volume")} for r in shown],
        "sma": {**sma, "points": [p for p in sma["points"] if p["timestamp"] in keep],
                "crossovers": [c for c in sma["crossovers"] if c["timestamp"] in keep]},
        "indicators": [{**ind, "points": [p for p in ind["points"] if p["timestamp"] in keep]} for ind in indicators],
    }


def create_snapshot(conn: sqlite3.Connection, symbol: str, interval: str, short: int, long: int,
                    account_id: str, order_size: int, as_of_ms: int | None = None,
                    candle_time: str | None = None, chart_candles: int | None = None) -> dict:
    """Freeze the application data for one decision and store it. Never calls a model.

    With `candle_time`, the decision time becomes the moment that candle completed (no later than `as_of_ms`),
    and a chart section is added for rendering and capture. Without it, the last completed candle as of
    `as_of_ms` is used (Prompt 10).
    """
    as_of_ms = as_of_ms if as_of_ms is not None else now_ms()
    missing = []
    if candle_time is not None:
        found = next((c for c in candle_series(symbol, interval, as_of_ms, conn=conn)["candles"]
                      if c["candle"]["timestamp"] == candle_time), None)
        if found is None:
            missing.append(f"no {interval} candle at {candle_time} for {symbol}")
        elif not found["status"]["complete"]:
            missing.append(f"the {interval} candle at {candle_time} has not completed")
        else:
            as_of_ms = min(as_of_ms, _candle_end_ms(found, interval))
    series = candle_series(symbol, interval, as_of_ms, conn=conn)
    rows = [{**c["candle"], "complete": c["status"]["complete"]} for c in series["candles"]]
    completed = [r for r in rows if r["complete"]]
    candle = indicators = None
    if missing:
        pass
    elif not completed:
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
        if candle_time is not None and last["timestamp"] != candle_time:
            missing.append(f"the last completed candle at the decision time is {last['timestamp']}, not {candle_time}")
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
    if candle_time is not None:
        snap["chart"] = _chart_section(conn, symbol, rows, short, long, chart_candles or CHART_CANDLES_DEFAULT)
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


def build_request(snapshot: dict, model: str, image_url: str | None = None) -> dict:
    """Provider-neutral request. With a chart image: input_image + input_json in one user message, three questions."""
    if image_url is None:
        content = [{"type": "input_text", "text": "Decision snapshot for one completed candle (paper account):"},
                   {"type": "input_json", "data": model_input(snapshot)}]
        questions = [ACTION_QUESTION]
    else:
        ind = snapshot["indicators"]
        content = [{"type": "input_text", "text": (
                       "Chart image of the decision snapshot: candles, volume, SMA lines and indicator lines through "
                       f"the cutoff candle (the last candle shown). Blue line: short SMA({ind['sma_short_window']}); "
                       f"orange line: long SMA({ind['sma_long_window']}); arrows mark crossovers; volume bars at the "
                       "bottom of the price pane; other lines are configured indicators, labelled on the right "
                       "axis. Then the snapshot data (paper account):")},
                   {"type": "input_image", "image_url": image_url},
                   {"type": "input_json", "data": model_input(snapshot)}]
        questions = CHART_QUESTIONS
    return {"model": model,
            "input": [{"role": "developer", "content": [{"type": "input_text", "text": STRATEGY}]},
                      {"role": "user", "content": content}],
            "questions": questions}


def stored_request(body: dict, image_ref: str | None) -> dict:
    """The request as saved in SQLite: the image bytes are replaced by a reference to the stored PNG."""
    if image_ref is None:
        return body
    user = body["input"][1]
    content = [{"type": "input_image", "image_url": image_ref} if part["type"] == "input_image" else part
               for part in user["content"]]
    return {**body, "input": [body["input"][0], {**user, "content": content}]}


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

    Each named choice question becomes one field of a structured-output schema, limited to its choices; for
    example {"action": <one of ACTIONS>}. A chart image (input_image data URL) becomes a base64 image block in
    the same user message, before the snapshot JSON.
    """
    questions = body["questions"]
    blocks = []
    for part in body["input"][1]["content"]:
        if part["type"] == "input_text":
            blocks.append({"type": "text", "text": part["text"]})
        elif part["type"] == "input_image":
            header, _, payload = part["image_url"].partition(",")
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": header[5:].split(";")[0],
                                                       "data": payload}})
        elif part["type"] == "input_json":
            blocks.append({"type": "text", "text": json.dumps(part["data"], indent=2)})
    asked = "\n\n".join(
        f"Question '{q['name']}': {q['instructions']}\nAllowed answers:\n"
        + "\n".join(f"- {c['value']}: {c['description']}" for c in q["choices"]) for q in questions)
    blocks.append({"type": "text", "text": asked})
    return {
        "model": body["model"],
        "max_tokens": 4000,
        "system": body["input"][0]["content"][0]["text"],
        "messages": [{"role": "user", "content": blocks}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": {
            "type": "object",
            "properties": {q["name"]: {"type": "string", "enum": [c["value"] for c in q["choices"]]} for q in questions},
            "required": [q["name"] for q in questions], "additionalProperties": False}}},
        # If the model declines, the API re-runs the request on a fallback model; response.model shows which ran.
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    }


class ClaudeDecisions:
    """Claude Messages API (official `anthropic` SDK) with a structured-output choice. Key: ANTHROPIC_API_KEY."""

    name = "anthropic"
    call_kind = "live"

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None,
                 timeout_s: float | None = None, max_retries: int = 0):   # one HTTP call per role
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
    if not isinstance(reply, dict):
        return []
    return [{"type": "choice", "name": name, "choice": reply[name], "probabilities": None, "confidence": None}
            for name in params["output_config"]["format"]["schema"]["required"] if name in reply]


class TestDoubleDecisions:
    """PROVIDER TEST DOUBLE (DECISIONS_PROVIDER=test): no network call; answers are fixed and labelled 'mock'.

    It decodes any input_image it receives and reports the PNG's sha256 and size in its response, so a check can
    prove the provider received a decodable PNG identical to the stored capture. Answers come from
    DECISIONS_TEST_ANSWERS, e.g. "action=buy,crossover=up,evidence=sufficient" (default).
    """

    name = "test-double"
    call_kind = "mock"
    model = "test-double"

    def __init__(self, answers: str | None = None):
        spec = answers if answers is not None else os.environ.get(
            "DECISIONS_TEST_ANSWERS", "action=buy,crossover=up,evidence=sufficient")
        self.answers = dict(item.split("=", 1) for item in spec.split(",") if "=" in item)
        self.received = []

    def send(self, body: dict) -> dict:
        self.received.append(body)
        images = []
        for part in body["input"][1]["content"]:
            if part["type"] == "input_image":
                try:
                    _raw, info = read_png(part["image_url"])
                    images.append({"decodable": True, **info})
                except ImageRejected as exc:
                    images.append({"decodable": False, "problem": exc.problem})
        asked = [q["name"] for q in body["questions"]]
        return {"model": self.model, "test_double": True, "received_images": images,
                "answers": [{"type": "choice", "name": n, "choice": self.answers[n], "probabilities": None,
                             "confidence": None} for n in asked if n in self.answers]}


def make_provider():
    """Claude (Anthropic) by default; DECISIONS_PROVIDER=openai selects OpenAI, =test the provider test double."""
    choice = os.environ.get("DECISIONS_PROVIDER", "claude").strip().lower()
    if choice == "openai":
        return OpenAIDecisions()
    if choice == "test":
        return TestDoubleDecisions()
    return ClaudeDecisions()


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
            error: str | None, chart_answers: dict | None = None, visual: list[dict] | None = None) -> str:
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
    if chart_answers is not None and status == "decided":
        parts.append(f"Chart answers: crossover {chart_answers.get('crossover', 'missing')} (Python calculated "
                     f"{f['crossover_at_candle']}), evidence {chart_answers.get('evidence', 'missing')}.")
        failed = [c["rule"] for c in visual or [] if not c["passed"]]
        if failed:
            parts.append("Not eligible for automated execution: " + "; ".join(failed) + " (failed).")
    return " ".join(parts)


# --------------------------------------------------------------------------- decide

def _store_decision(conn, **row) -> dict:
    ensure_schema(conn)
    with conn:
        did = conn.execute(f"INSERT INTO decisions ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                           tuple(row.values())).lastrowid
    return get_decision(conn, did)


def check_capture(snapshot: dict, capture: dict | None) -> None:
    """The capture must describe the same snapshot (symbol, interval, cutoff candle), else it must be recaptured."""
    if "chart" not in snapshot:
        raise ImageRejected(f"snapshot {snapshot['snapshot_id']} has no chart section",
                            "Freeze a new snapshot with Ask about chart; this snapshot was not made for a chart.")
    capture = capture or {}
    expected = {"snapshot_id": snapshot["snapshot_id"], "symbol": snapshot["symbol"],
                "interval": snapshot["interval"], "cutoff": snapshot["candle"]["timestamp"]}
    wrong = [f"{k} {capture.get(k)!r} (snapshot: {v!r})" for k, v in expected.items() if capture.get(k) != v]
    if wrong:
        raise ImageRejected("the capture does not match the snapshot: " + "; ".join(wrong),
                            f"Recapture {snapshot['symbol']} {snapshot['interval']} with the cutoff at "
                            f"{snapshot['candle']['timestamp']} (snapshot {snapshot['snapshot_id']}).")


def chart_checks(answers: dict, snapshot: dict) -> list[dict]:
    """Checks on the crossover and evidence answers; any failure makes the decision ineligible."""
    python = snapshot["indicators"]["crossover_at_candle"]
    crossover, evidence = answers.get("crossover"), answers.get("evidence")
    allowed = {q["name"]: [c["value"] for c in q["choices"]] for q in CHART_QUESTIONS}
    checks = [
        {"rule": "a crossover answer is present", "passed": crossover in allowed["crossover"],
         "observed": "missing" if crossover is None else repr(crossover)},
        {"rule": "an evidence answer is present", "passed": evidence in allowed["evidence"],
         "observed": "missing" if evidence is None else repr(evidence)},
    ]
    if crossover in allowed["crossover"]:
        checks.append({"rule": "the chart image was readable", "passed": crossover != "unreadable",
                       "observed": f"crossover answer {crossover}"})
        if crossover != "unreadable":
            checks.append({"rule": f"the visual crossover matches the Python crossover ({python})",
                           "passed": crossover == python, "observed": f"model saw {crossover}"})
    if evidence in allowed["evidence"]:
        checks.append({"rule": "the chart is sufficient to assess the signal", "passed": evidence == "sufficient",
                       "observed": f"evidence answer {evidence}"})
    return checks


def decide(conn: sqlite3.Connection, snapshot_id: int, provider, image: str | None = None,
           capture: dict | None = None, review: bool = False, reviewer=None) -> dict:
    """Ask the provider about one stored snapshot, validate the answers and store the decision.

    With `image` (a PNG data URL captured from the snapshot's chart) the request carries the image and three
    questions. An unreadable image or a capture of a different snapshot raises ImageRejected before any provider
    call, and nothing is stored.

    With `review`, a second call (the reviewer, Prompt 11; `reviewer` defaults to the same provider client) judges
    the analyst's proposal, and the decision is eligible only if every gate in review.final_check passes.
    """
    from backend import review as rv
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

    image_path = image_info = None
    if image is not None:
        raw, image_info = read_png(image)           # raises ImageRejected: unreadable image
        check_capture(snapshot, capture)            # raises ImageRejected: different snapshot
        image_path = store_png(conn, snapshot_id, raw, image_info["sha256"])
        image = data_url(raw)

    body = build_request(snapshot, provider.model, image)
    started = time.monotonic()
    response = error = proposal = probabilities = confidence = None
    answers: dict = {}
    try:
        response = provider.send(body)
        listed = response.get("answers", [])
        answers = {a.get("name"): a.get("choice") for a in listed if isinstance(a, dict)}
        answer = next((a for a in listed if a.get("name") == "action"), None)
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
    visual = chart_checks(answers, snapshot) if image is not None and status == "decided" else []
    eligible = (valid and snapshot["kind"] == "application" and snapshot["complete"]
                and all(c["passed"] for c in visual))
    image_ref = (f"stored:{image_path} sha256:{image_info['sha256']}" if image_path else None)

    review_row, strategy, final = {}, [], None
    if review:
        reviewer = reviewer or provider
        strategy = rv.strategy_checks(proposal, snapshot) if status == "decided" else []
        if status == "decided":
            rbody = rv.build_review_request(snapshot, reviewer.model, model_input(snapshot), facts(snapshot),
                                            proposal, answers, provider.model, image)
            outcome = rv.run_review(reviewer, rbody, (ProviderTimeout, ProviderError))
            outcome["request"] = stored_request(rbody, image_ref)
        else:
            outcome = {"status": "not_run", "verdict": None, "error": f"no proposal to review ({status})",
                       "response": None, "latency_ms": None, "request": None, "provider": reviewer.name,
                       "model": reviewer.model, "call_kind": reviewer.call_kind}
        outcome["evidence"] = rv.verdict_evidence(outcome["verdict"], proposal, snapshot)
        final = rv.final_check(analyst_status=status, account=checks, strategy=strategy, chart=visual,
                               kind=snapshot["kind"], review=outcome)
        eligible = eligible and final["eligible"]
        review_row = {"reviewer_status": outcome["status"], "verdict": outcome["verdict"],
                      "reviewer_model": outcome["model"], "review_json": json.dumps(outcome),
                      "strategy_json": json.dumps(strategy), "final_check_json": json.dumps(final)}
    explanation = explain(snapshot, status, proposal, checks, provider.model, error, answers if image else None,
                          visual)
    if final is not None:
        explanation += (f" Reviewer ({review_row['reviewer_model']}): "
                        + (review_row["verdict"] or f"no verdict ({review_row['reviewer_status']})") + ". "
                        + ("Final check: eligible." if final["eligible"] else "Final check: not eligible: "
                           + "; ".join(g["gate"] for g in final["gates"] if not g["passed"]) + "."))
    return _store_decision(
        conn, **base, status=status, proposal=proposal,
        probabilities_json=json.dumps(probabilities) if probabilities is not None else None,
        confidence=confidence, validation_status=("valid" if valid else "invalid") if status == "decided"
        else "not_checked", validation_json=json.dumps(checks), eligible_for_order=int(eligible),
        explanation=explanation,
        facts_json=json.dumps(facts(snapshot)), request_json=json.dumps(stored_request(body, image_ref)),
        response_json=json.dumps(response) if response is not None else None, error=error, latency_ms=latency,
        questions_json=json.dumps(body["questions"]), answers_json=json.dumps(answers),
        chart_checks_json=json.dumps(visual), image_path=image_path,
        image_json=json.dumps(image_info) if image_info else None, **review_row)


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
        "questions": json.loads(r["questions_json"] or "[]"),
        "answers": json.loads(r["answers_json"] or "{}"),
        "chart_checks": json.loads(r["chart_checks_json"] or "[]"),
        "image": ({**json.loads(r["image_json"]), "path": r["image_path"], "url": f"/api/decisions/{r['id']}/image"}
                  if r["image_path"] else None),
        "request_sent": json.loads(r["request_json"]) if r["request_json"] else None,
        "review": ({**json.loads(r["review_json"]), "strategy_checks": json.loads(r["strategy_json"] or "[]")}
                   if r["review_json"] else None),
        "final_check": json.loads(r["final_check_json"]) if r["final_check_json"] else None,
        "snapshot": load_snapshot(conn, r["snapshot_id"]) if r["snapshot_id"] is not None else None,
    }
