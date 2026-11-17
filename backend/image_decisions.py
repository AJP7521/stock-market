"""Image-only chart decision: long, short or wait (Prompt 16).

The model sees ONE thing: a PNG of the chart through the cutoff candle, captured in the browser from a frozen
snapshot (Prompt 10A). It does not receive the symbol, prices, SMA values, the Python crossover or the paper account.
It answers one named choice question, "stance": long, short or wait.

Steps (decide_image):
  1. Load the frozen snapshot. It must have a chart section and a cutoff candle.
  2. Check the PNG (chart_images.read_png) and that the capture describes this snapshot (decisions.check_capture).
     Either failure raises ImageRejected: nothing is stored and no provider call is made.
  3. Store the PNG beside the database and send it, with fixed instructions only, through the same provider client
     as the Decision API (Claude by default; DECISIONS_PROVIDER=test for the labelled test double).
  4. Validate the answer by name and store it in image_decisions. Statuses as in decisions.py:
     decided | malformed_answer | timeout | provider_error.

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

STANCES = {
    "long": "The chart suggests the price is more likely to rise after the last candle.",
    "short": "The chart suggests the price is more likely to fall after the last candle.",
    "wait": "The chart shows no clear signal at the last candle; stay out.",
}

INSTRUCTIONS = (
    "You look at one candlestick chart image from a PAPER trading simulation for students (no real money; no order "
    "is placed from your answer). Assess the image only: you are not given the symbol, prices or indicator values "
    "as data. Green candles closed higher, red candles closed lower; volume bars sit at the bottom of the price "
    "pane. The blue line is a shorter simple moving average and the orange line a longer one; arrows mark where "
    "they cross. Any other lines are indicators labelled on the right axis. The last candle on the right is the "
    "cutoff. Choose exactly one stance for the moment after the cutoff."
)

STANCE_QUESTION = {
    "type": "choice", "name": "stance",
    "instructions": "Based only on what is visible in the image, which stance fits the chart at the last candle?",
    "choices": [{"value": v, "description": d} for v, d in STANCES.items()],
}

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
    stance            TEXT CHECK (stance IS NULL OR stance IN ('long', 'short', 'wait')),
    python_crossover  TEXT,                   -- Python's crossover at the cutoff, for comparison only
    image_path        TEXT NOT NULL,          -- PNG relative to the database folder
    image_json        TEXT NOT NULL,          -- width, height, bytes, sha256
    request_json      TEXT NOT NULL,          -- what was sent, with the image as a stored reference
    response_json     TEXT,
    error             TEXT,
    latency_ms        INTEGER
);
CREATE INDEX IF NOT EXISTS image_decisions_by_chart ON image_decisions (symbol, interval, candle_ts);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def build_request(model: str, image_url: str) -> dict:
    """Provider-neutral request (same shape as decisions.build_request): the image and fixed text, no snapshot data."""
    return {"model": model,
            "input": [{"role": "developer", "content": [{"type": "input_text", "text": INSTRUCTIONS}]},
                      {"role": "user", "content": [
                          {"type": "input_text", "text": "Chart image (the last candle on the right is the cutoff):"},
                          {"type": "input_image", "image_url": image_url}]}],
            "questions": [STANCE_QUESTION]}


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

    body = build_request(provider.model, data_url(raw))
    started = time.monotonic()
    response = error = stance = None
    try:
        response = provider.send(body)
        answer = next((a for a in response.get("answers", []) if a.get("name") == "stance"), None)
        if answer is None:
            status, error = "malformed_answer", "no answer named 'stance' in the response"
        elif answer.get("choice") not in STANCES:
            status, error = "malformed_answer", f"choice {answer.get('choice')!r} is not one of {list(STANCES)}"
        else:
            status, stance = "decided", answer["choice"]
    except ProviderTimeout as exc:
        status, error = "timeout", str(exc)
    except ProviderError as exc:
        status, error = "provider_error", str(exc)
    except (AttributeError, TypeError) as exc:      # JSON, but not the documented shape
        status, error = "malformed_answer", f"unexpected response shape: {exc}"
    latency = round((time.monotonic() - started) * 1000)

    image_ref = f"stored:{image_path} sha256:{info['sha256']}"
    with conn:
        new_id = conn.execute(
            "INSERT INTO image_decisions (snapshot_id, created_ms, symbol, interval, candle_ts, provider, model, "
            "call_kind, status, stance, python_crossover, image_path, image_json, request_json, response_json, error, "
            "latency_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (snapshot_id, now_ms(), snapshot["symbol"], snapshot["interval"], snapshot["candle"]["timestamp"],
             provider.name, provider.model, provider.call_kind, status, stance,
             (snapshot.get("indicators") or {}).get("crossover_at_candle"), image_path, json.dumps(info),
             json.dumps(stored_request(body, image_ref)),
             json.dumps(response) if response is not None else None, error, latency)).lastrowid
    return get_image_decision(conn, new_id)


def _row(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    return {
        "image_decision_id": r["id"], "snapshot_id": r["snapshot_id"], "created_at": iso_ms(r["created_ms"]),
        "symbol": r["symbol"], "interval": r["interval"], "candle_time": r["candle_ts"],
        "provider": r["provider"], "model": r["model"], "call_kind": r["call_kind"], "status": r["status"],
        "stance": r["stance"], "error": r["error"], "latency_ms": r["latency_ms"],
        "python_crossover": r["python_crossover"],
        "image": {**json.loads(r["image_json"]), "path": r["image_path"],
                  "url": f"/api/image-decisions/{r['id']}/image"},
        "trade_placed": False, "eligible_for_order": False,
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
