"""FastAPI application for the Live Market Workstation.

Run from the project root:
    python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Query

from backend.candles import minute_candles
from backend.history import load_candles
from backend.recorder import connect, db_path
from backend.timeframes import aggregate

app = FastAPI(title="Live Market Workstation")

SAMPLE_SYMBOL = "SAMPLE"


@app.get("/api/health")
def health() -> dict:
    """Small status response used to check that the backend is reachable."""
    return {"status": "ok", "service": "backend"}


def _parse_as_of(as_of: str | None) -> int | None:
    if as_of is None:
        return None
    try:
        dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, f"as_of must be an ISO 8601 time, e.g. 2026-11-09T14:35:00Z (got {as_of!r})")
    if dt.tzinfo is None:
        raise HTTPException(422, "as_of must include a time zone, e.g. a trailing Z")
    return round(dt.timestamp() * 1000)


@app.get("/api/history")
def history(
    symbol: str = Query(SAMPLE_SYMBOL, description="SAMPLE = synthetic CSV; otherwise a recorded ticker"),
    timeframe: Literal["1m", "5m", "15m", "1h", "1d"] = "1m",
    as_of: str | None = Query(None, description="Only use minutes available at this UTC time"),
) -> dict:
    """Candles for one symbol at the requested timeframe, derived from one-minute data.

    `candles` holds exactly the six CSV fields; `status[i]` describes `candles[i]`
    (completion, coverage, whether volume is known).
    """
    as_of_ms = _parse_as_of(as_of)
    if symbol == SAMPLE_SYMBOL:
        minutes, source = load_candles(), "synthetic CSV (prompts/data/sample-candles.csv)"
    else:
        with connect(db_path()) as conn:
            live = minute_candles(conn, symbol, as_of_ms if as_of_ms is not None else _now_ms())
        minutes = [{**item["candle"], "complete": item["status"]["complete"]} for item in live]
        source = "recorded Yahoo observations (SQLite)"
    result = aggregate(minutes, timeframe, as_of_ms)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of": as_of,
        "source": source,
        "candles": [c["candle"] for c in result["candles"]],
        "status": [c["status"] for c in result["candles"]],
        "excluded_minutes": result["excluded_minutes"],
    }


def _now_ms() -> int:
    return round(datetime.now(timezone.utc).timestamp() * 1000)
