"""Image-only chart decision: long, short, hold, exit or wait (Prompts 16, 16A, 16C, 16D, 16E).

The model sees a PNG of the chart through the cutoff candle, captured in the browser from a frozen snapshot
(Prompt 10A), plus a short context: the capture time, the chart interval, whether the chart is stale, and the
position context (Prompt 16E). It does not receive the symbol, prices, SMA values, the Python crossover, cash or the
share count. It answers one named choice question, "chart_signal" (Prompt 16E, word for word), comparing the newer of
the last two completed closes with the preceding close:

    position explicitly empty:  higher -> long, lower -> short
    open long position:         higher -> hold, lower -> exit       (open short: lower -> hold, higher -> exit)
    equal closes                -> wait
    stale chart, fewer than two candles, unclear evidence, or missing/unknown position -> wait (takes priority)

Position context: application-defined fields (not Decision API fields), from the frozen snapshot's paper account at
the decision time: 0 shares -> "empty"; more than 0 -> "open", direction "long"; fewer than 0 -> "open", "short"
(never happens: the simulator is long-only); account missing -> "unknown". An explicitly empty position is not the
same as an unknown one, and the chart image alone establishes neither.

Probabilities (Prompt 16D): the request asks for one probability per choice. With Claude these are the MODEL'S OWN
ESTIMATES (the Messages API returns no measured probabilities); check_probabilities() requires one per choice, each
0-1, summing to 1 within PROBABILITY_SUM_TOLERANCE. The decision label is the choice with its estimate, e.g.
"wait 85%"; invalid or missing estimates are stored with the reason and never shown as a percentage.

Stale: the cutoff candle completed more than one interval before the capture time (a newer candle has completed
since). Capture time = the backend clock when the image arrives (recorder.now_ms, so the SmokeTest's test clock
applies). Python applies the same rule to the snapshot's last two completed candles (python_signal) and the result
shows whether the model's answer matches; a conflict is shown, never corrected.

Steps (decide_image):
  1. Load the frozen snapshot. It must have a chart section and a cutoff candle.
  2. Check the PNG (chart_images.read_png) and that the capture describes this snapshot (decisions.check_capture).
     Either failure raises ImageRejected: nothing is stored and no provider call is made.
  3. Store the PNG beside the database and send it, with fixed instructions and the context only, through the same
     provider client as the Decision API (Claude by default; DECISIONS_PROVIDER=test for the labelled test double).
  4. Validate the answer by name ("chart_signal") and store it in image_decisions (column "stance").
     Statuses as in decisions.py: decided | malformed_answer | timeout | provider_error.

The stance is a view of the chart, not an order. The simulator stays long-only, nothing here touches the paper
account, and an image-only decision is never eligible for execution. Results live in their own table, so
automation, the reviewer and voice never pick them up. The Python crossover at the cutoff is stored beside the answer
for comparison on screen only; it is never sent to the model.
"""

import json
import sqlite3
import time

from backend.candles import iso_ms
from backend.chart_images import ImageRejected, data_url, read_png, store_png
from backend.decisions import ProviderError, ProviderTimeout, check_capture, load_snapshot, stored_request
from backend.recorder import now_ms

INSTRUCTIONS = (
    "You look at one candlestick chart image from a PAPER trading simulation for students (no real money; no order "
    "is placed from your answer). Assess the image only: you are not given the symbol, prices or indicator values "
    "as data. Green candles closed above their open, red candles below; volume bars sit at the bottom of the price "
    "pane. The blue line is a shorter simple moving average and the orange line a longer one; arrows mark where "
    "they cross. Any other lines are indicators labelled on the right axis. The last candle on the right is the "
    "cutoff. Answer the question exactly as its instructions and choices describe."
)

# The question exactly as approved by the student (Prompt 16E: "use your recommended entries as written").
SIGNAL_QUESTION = {
    "type": "choice",
    "name": "chart_signal",
    "instructions": (
        "For this hypothetical chart-reading exercise, inspect the last two completed visible one-minute candles. "
        "Exclude a candle that is still forming. Use the chart and supplied capture time to identify completed "
        "intervals. Use the supplied position context to determine whether a position exists and its direction; "
        "the chart image alone does not establish the position. Choose wait if two completed candles cannot be "
        "identified, the evidence is unclear or insufficient, the supplied context marks it as stale, or the "
        "position state is missing or unknown; these conditions take priority over every other choice. Otherwise "
        "apply the choice descriptions. The chart is a limited snapshot."),
    "choices": [
        {"value": "long",
         "description": ("The position is explicitly empty, and the more recent of the two completed candles closes "
                         "above the preceding candle's close.")},
        {"value": "short",
         "description": ("The position is explicitly empty, and the more recent of the two completed candles closes "
                         "below the preceding candle's close.")},
        {"value": "hold",
         "description": ("A position is open, and the more recent completed candle's close continues in the "
                         "position's direction: above the preceding candle's close for a long position, or below it "
                         "for a short position.")},
        {"value": "exit",
         "description": ("A position is open, and the more recent completed candle's close moves against the "
                         "position's direction: below the preceding candle's close for a long position, or above it "
                         "for a short position.")},
        {"value": "wait",
         "description": ("No applicable change can be determined: the two closing prices are equal, or the evidence "
                         "is insufficient, unclear, or stale, or the position state is missing or unknown.")},
    ],
}
STANCES = [c["value"] for c in SIGNAL_QUESTION["choices"]]
PROBABILITY_SUM_TOLERANCE = 0.02
INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "1d": 86_400_000}

SCHEMA = """
CREATE TABLE IF NOT EXISTS image_decisions (
    id                INTEGER PRIMARY KEY,
    snapshot_id       INTEGER NOT NULL,       -- decision_snapshots.id the chart was rendered from
    created_ms        INTEGER NOT NULL,
    symbol            TEXT NOT NULL,          -- from the snapshot (for markers; not sent to the model)
    interval          TEXT NOT NULL,
    candle_ts         TEXT NOT NULL,          -- cutoff candle (the marker's position)
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    call_kind         TEXT NOT NULL,          -- 'live' provider call or 'mock' test double
    status            TEXT NOT NULL CHECK (status IN
                        ('decided', 'malformed_answer', 'timeout', 'provider_error')),
    stance            TEXT CHECK (stance IS NULL OR stance IN ('long', 'short', 'hold', 'exit', 'wait')),
    python_crossover  TEXT,                   -- Python's crossover at the cutoff, for comparison only
    image_path        TEXT NOT NULL,          -- PNG relative to the database folder
    image_json        TEXT NOT NULL,          -- width, height, bytes, sha256
    request_json      TEXT NOT NULL,          -- what was sent, with the image as a stored reference
    response_json     TEXT,
    error             TEXT,
    latency_ms        INTEGER,
    capture_ms        INTEGER,                -- capture time supplied to the model (Prompt 16A)
    stale             INTEGER,                -- 1 if stale at the capture time (Prompt 16A)
    python_signal     TEXT,                   -- Python's answer to the same rule (not sent)
    probabilities_json  TEXT,                 -- Prompt 16D: [{value, probability}] (Claude: model-estimated)
    probabilities_check TEXT,                 -- "valid", "missing" or "invalid: <reason>"
    position_state    TEXT,                   -- Prompt 16E: empty | open | unknown (sent as context)
    position_direction TEXT,                  -- long | short when open (sent as context)
    position_shares   INTEGER,                -- shares in the snapshot's paper account (not sent)
    position_source   TEXT                    -- Prompt 16F: "paper account" or "TEST override"
);
CREATE INDEX IF NOT EXISTS image_decisions_by_chart ON image_decisions (symbol, interval, candle_ts);
"""

# Columns added after Prompt 16 (added to an older table if missing, before any rebuild).
ADDED_COLUMNS = {
    "capture_ms": "INTEGER", "stale": "INTEGER", "python_signal": "TEXT",                 # Prompt 16A
    "probabilities_json": "TEXT", "probabilities_check": "TEXT",                         # Prompt 16D
    "position_state": "TEXT", "position_direction": "TEXT", "position_shares": "INTEGER",  # Prompt 16E
    "position_source": "TEXT",                                                           # Prompt 16F
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    have = [r[1] for r in conn.execute("PRAGMA table_info(image_decisions)")]
    for name, kind in ADDED_COLUMNS.items():
        if name not in have:
            conn.execute(f"ALTER TABLE image_decisions ADD COLUMN {name} {kind}")
            have.append(name)
    conn.commit()
    # Prompt 16E: SQLite cannot change a CHECK constraint in place. A table created before 16E only allows
    # long/short/wait, so rebuild it once with the current definition, copying every row (one transaction).
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'image_decisions'").fetchone()[0]
    if "'hold'" not in sql:
        cols = ", ".join(have)
        conn.executescript(
            "BEGIN;"
            "DROP INDEX IF EXISTS image_decisions_by_chart;"
            "ALTER TABLE image_decisions RENAME TO image_decisions_before_16e;"
            + SCHEMA +
            f"INSERT INTO image_decisions ({cols}) SELECT {cols} FROM image_decisions_before_16e;"
            "DROP TABLE image_decisions_before_16e;"
            "COMMIT;")


def is_stale(snapshot: dict, capture_ms: int) -> bool:
    """True if a newer candle has completed since the cutoff (decision_ms = when the cutoff candle completed)."""
    return capture_ms - snapshot["decision_ms"] > INTERVAL_MS[snapshot["interval"]]


# TEST CONTROL (Prompt 16F): set only through the TEST_FEED-gated POST /api/test/position, to compare scenarios the
# long-only paper account cannot produce (a short or an unknown position). None = use the paper account.
TEST_POSITION: dict | None = None


def position_of(snapshot: dict) -> dict:
    """Application-defined position context from the frozen snapshot's paper account (Prompt 16E), or the labelled
    test override when one is set (Prompt 16F)."""
    if TEST_POSITION is not None:
        return {**TEST_POSITION, "shares": None, "source": "TEST override"}
    account = snapshot.get("account")
    shares = account.get("position") if isinstance(account, dict) else None
    if not isinstance(shares, int) or isinstance(shares, bool):
        return {"state": "unknown", "direction": None, "shares": None, "source": "paper account"}
    if shares == 0:
        return {"state": "empty", "direction": None, "shares": 0, "source": "paper account"}
    return {"state": "open", "direction": "long" if shares > 0 else "short", "shares": shares,
            "source": "paper account"}


def python_signal(snapshot: dict, stale: bool, position: dict) -> str:
    """The question's rule applied to the snapshot's last two completed candles and the position (independent of the
    model): wait first for stale, fewer than two candles or an unknown position; equal closes -> wait; empty: higher
    long, lower short; open: in the position's direction hold, against it exit (Prompt 16E)."""
    last_two = (snapshot.get("chart") or {}).get("candles", [])[-2:]
    if stale or len(last_two) < 2 or position["state"] not in ("empty", "open"):
        return "wait"
    older, newer = last_two[0]["close"], last_two[1]["close"]
    if newer == older:
        return "wait"
    up = newer > older
    if position["state"] == "empty":
        return "long" if up else "short"
    if position["direction"] not in ("long", "short"):
        return "wait"
    return "hold" if up == (position["direction"] == "long") else "exit"


def check_probabilities(probabilities) -> str:
    """'valid', 'missing' or 'invalid: <reason>' for a [{value, probability}] list: exactly one per choice value
    (matched by value, in any order), each 0-1, adding up to 1."""
    if probabilities is None:
        return "missing"
    if not isinstance(probabilities, list) or not all(isinstance(p, dict) for p in probabilities):
        return "invalid: not a list of {value, probability}"
    values = [p.get("value") for p in probabilities]
    if sorted(values, key=str) != sorted(STANCES):
        return f"invalid: values {values} are not exactly {STANCES}"
    numbers = [p.get("probability") for p in probabilities]
    if not all(isinstance(n, (int, float)) and not isinstance(n, bool) and 0 <= n <= 1 for n in numbers):
        return f"invalid: each probability must be a number from 0 to 1 (got {numbers})"
    total = sum(numbers)
    if abs(total - 1) > PROBABILITY_SUM_TOLERANCE:
        return f"invalid: probabilities add up to {total:.3f}, not 1"
    return "valid"


POSITION_TEXT = {
    "empty": "Position context (application-defined): position exists = no; the position is explicitly empty.",
    "open": "Position context (application-defined): position exists = yes; direction = {direction}.",
    "unknown": "Position context (application-defined): position state is unknown (missing).",
}


def context_text(interval: str, capture_ms: int, stale: bool, position: dict) -> str:
    """The supplied context: capture time, interval, staleness and position (whether one exists and its direction).
    No symbol, prices, indicator values, cash or share count."""
    return (f"Capture time: {iso_ms(capture_ms)} (UTC). Chart interval: {interval}. "
            + ("Context: STALE. The last candle on the chart completed more than one interval before the capture "
               "time, so the chart is out of date." if stale else
               "Context: not stale. The last candle on the chart completed within one interval before the capture "
               "time.")
            + " " + POSITION_TEXT[position["state"]].format(direction=position["direction"]))


def build_request(model: str, image_url: str, context: str) -> dict:
    """Provider-neutral request (same shape as decisions.build_request): text context and the image, no candle data."""
    return {"model": model,
            "input": [{"role": "developer", "content": [{"type": "input_text", "text": INSTRUCTIONS}]},
                      {"role": "user", "content": [
                          {"type": "input_text", "text": context},
                          {"type": "input_text", "text": "Chart image (the last candle on the right is the cutoff):"},
                          {"type": "input_image", "image_url": image_url}]}],
            "questions": [SIGNAL_QUESTION],
            "estimate_probabilities": True}             # Claude: add its own estimate per choice (Prompt 16D)


def decide_image(conn: sqlite3.Connection, snapshot_id: int, provider, image: str, capture: dict | None) -> dict:
    """Ask the provider for a stance on one captured chart and store the result. Never touches the paper account."""
    ensure_schema(conn)
    snapshot = load_snapshot(conn, snapshot_id)
    if snapshot is None:
        raise ImageRejected(f"no snapshot {snapshot_id}", "Freeze a new snapshot and capture the chart again.")
    if not snapshot.get("candle"):
        raise ImageRejected(f"snapshot {snapshot_id} has no cutoff candle: " + "; ".join(snapshot["missing"]),
                            "Choose a completed candle and capture the chart again.")
    raw, info = read_png(image)               # raises ImageRejected: unreadable image
    check_capture(snapshot, capture)          # raises ImageRejected: capture of a different snapshot or candle
    image_path = store_png(conn, snapshot_id, raw, info["sha256"])

    capture_ms = now_ms()
    stale = is_stale(snapshot, capture_ms)
    position = position_of(snapshot)
    expected = python_signal(snapshot, stale, position)
    body = build_request(provider.model, data_url(raw),
                         context_text(snapshot["interval"], capture_ms, stale, position))
    started = time.monotonic()
    response = error = stance = probabilities = None
    try:
        response = provider.send(body)
        answer = next((a for a in response.get("answers", []) if a.get("name") == "chart_signal"), None)
        if answer is None:
            status, error = "malformed_answer", "no answer named 'chart_signal' in the response"
        elif answer.get("choice") not in STANCES:
            status, error = "malformed_answer", f"choice {answer.get('choice')!r} is not one of {STANCES}"
        else:
            status, stance = "decided", answer["choice"]
            probabilities = answer.get("probabilities")
    except ProviderTimeout as exc:
        status, error = "timeout", str(exc)
    except ProviderError as exc:
        status, error = "provider_error", str(exc)
    except (AttributeError, TypeError) as exc:      # JSON, but not the documented shape
        status, error = "malformed_answer", f"unexpected response shape: {exc}"
    latency = round((time.monotonic() - started) * 1000)

    image_ref = f"stored:{image_path} sha256:{info['sha256']}"
    prob_check = check_probabilities(probabilities) if status == "decided" else None
    with conn:
        new_id = conn.execute(
            "INSERT INTO image_decisions (snapshot_id, created_ms, symbol, interval, candle_ts, provider, model, "
            "call_kind, status, stance, python_crossover, image_path, image_json, request_json, response_json, error, "
            "latency_ms, capture_ms, stale, python_signal, probabilities_json, probabilities_check, position_state, "
            "position_direction, position_shares, position_source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (snapshot_id, now_ms(), snapshot["symbol"], snapshot["interval"], snapshot["candle"]["timestamp"],
             provider.name, provider.model, provider.call_kind, status, stance,
             (snapshot.get("indicators") or {}).get("crossover_at_candle"), image_path, json.dumps(info),
             json.dumps(stored_request(body, image_ref)),
             json.dumps(response) if response is not None else None, error, latency, capture_ms, int(stale),
             expected, json.dumps(probabilities) if probabilities is not None else None, prob_check,
             position["state"], position["direction"], position["shares"], position["source"])).lastrowid
    return get_image_decision(conn, new_id)


def _row(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    return {
        "image_decision_id": r["id"], "snapshot_id": r["snapshot_id"], "created_at": iso_ms(r["created_ms"]),
        "symbol": r["symbol"], "interval": r["interval"], "candle_time": r["candle_ts"],
        "provider": r["provider"], "model": r["model"], "call_kind": r["call_kind"], "status": r["status"],
        "stance": r["stance"], "error": r["error"], "latency_ms": r["latency_ms"],
        "python_crossover": r["python_crossover"],
        "question": "chart_signal" if r["capture_ms"] is not None else "stance",    # rows before Prompt 16A: stance
        "capture_time": iso_ms(r["capture_ms"]) if r["capture_ms"] is not None else None,
        "stale": bool(r["stale"]) if r["stale"] is not None else None,
        "python_signal": r["python_signal"],
        "position": ({"state": r["position_state"], "direction": r["position_direction"],
                      "shares": r["position_shares"], "source": r["position_source"] or "paper account"}
                     if r["position_state"] is not None else None),
        "signal_match": (r["stance"] == r["python_signal"]
                         if r["stance"] is not None and r["python_signal"] is not None else None),
        **_label(r),
        "image": {**json.loads(r["image_json"]), "path": r["image_path"],
                  "url": f"/api/image-decisions/{r['id']}/image"},
        "trade_placed": False, "eligible_for_order": False,
    }


def _label(r: sqlite3.Row) -> dict:
    """The decision label (Prompt 16D): the choice and, when the estimates are valid, its probability."""
    probabilities = json.loads(r["probabilities_json"]) if r["probabilities_json"] else None
    check = r["probabilities_check"]
    chosen = (next((p["probability"] for p in probabilities if p.get("value") == r["stance"]), None)
              if check == "valid" else None)
    return {
        "probabilities": probabilities,
        "probabilities_check": check,
        # Claude has no measured probabilities: what it returns is its own estimate; the test double's are fixed.
        "probabilities_source": (None if probabilities is None else "test double (fixed)" if r["call_kind"] == "mock"
                                 else "model-estimated" if r["provider"] == "anthropic" else "provider"),
        "chosen_probability": chosen,
        "label": None if r["stance"] is None else (
            f"{r['stance']} {round(chosen * 100)}%" if chosen is not None else r["stance"]),
    }


def get_image_decision(conn: sqlite3.Connection, image_decision_id: int) -> dict | None:
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM image_decisions WHERE id = ?", (image_decision_id,)).fetchone()
    if r is None:
        return None
    return {**_row(conn, r), "request_sent": json.loads(r["request_json"]),
            "response": json.loads(r["response_json"]) if r["response_json"] else None}


def list_image_decisions(conn: sqlite3.Connection, symbol: str, interval: str, limit: int = 200) -> list[dict]:
    """Decided stances for one chart, newest first, at most one per candle (the latest), for chart markers."""
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM image_decisions WHERE symbol = ? AND interval = ? AND status = 'decided' "
        "AND id IN (SELECT max(id) FROM image_decisions WHERE symbol = ? AND interval = ? AND status = 'decided' "
        "GROUP BY candle_ts) ORDER BY candle_ts DESC LIMIT ?", (symbol, interval, symbol, interval, limit)).fetchall()
    return [_row(conn, r) for r in rows]
