"""FastAPI application for the Live Market Workstation.

Run from the project root:
    python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect

from backend.candles import minute_candles
from backend.history import load_candles
from backend.live import LiveHub
from backend.recorder import connect, db_path, now_ms
from backend.timeframes import MINUTE_MS, aggregate, to_ms

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

SAMPLE_SYMBOL = "SAMPLE"
Timeframe = Literal["1m", "5m", "15m", "1h", "1d"]
hub = LiveHub()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await hub.stop_all()   # close upstream Yahoo connections on shutdown


app = FastAPI(title="Live Market Workstation", lifespan=lifespan)


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


def candle_series(symbol: str, timeframe: str, as_of_ms: int | None) -> dict:
    """One-minute data for the symbol, aggregated to the timeframe (shared by HTTP and WebSocket)."""
    if symbol == SAMPLE_SYMBOL:
        minutes, source = load_candles(), "synthetic CSV (prompts/data/sample-candles.csv)"
    else:
        if as_of_ms is None:          # live symbols: "now", so the latest candle can show as still forming
            as_of_ms = now_ms()
        with connect(db_path()) as conn:
            live = minute_candles(conn, symbol, as_of_ms)
        minutes = [{**item["candle"], "complete": item["status"]["complete"]} for item in live]
        source = "Yahoo Finance (recorded in SQLite)"
    result = aggregate(minutes, timeframe, as_of_ms)
    return {"source": source, **result}


@app.get("/api/history")
def history(
    symbol: str = Query(SAMPLE_SYMBOL, description="SAMPLE = synthetic CSV; otherwise a recorded ticker"),
    timeframe: Timeframe = "1m",
    as_of: str | None = Query(None, description="Only use minutes available at this UTC time"),
) -> dict:
    """Candles for one symbol at the requested timeframe, derived from one-minute data.

    `candles` holds exactly the six CSV fields; `status[i]` describes `candles[i]`
    (completion, coverage, whether volume is known).
    """
    series = candle_series(symbol, timeframe, _parse_as_of(as_of))
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of": as_of,
        "source": series["source"],
        "candles": [c["candle"] for c in series["candles"]],
        "status": [c["status"] for c in series["candles"]],
        "excluded_minutes": series["excluded_minutes"],
    }


def _containing(candles: list[dict], timeframe: str, ms: int) -> dict | None:
    """The candle whose interval contains the instant `ms`."""
    for item in reversed(candles):
        start = to_ms(item["candle"]["timestamp"])
        end = start + MINUTE_MS if timeframe == "1m" else to_ms(item["status"]["interval_end"])
        if start <= ms < end:
            return item
    return None


@app.websocket("/api/ws/candles")
async def candle_updates(websocket: WebSocket, symbol: str = SAMPLE_SYMBOL, timeframe: str = "1m"):
    """Push candle updates for one symbol and timeframe.

    Messages:
      {"type": "candle", "symbol", "interval", "candle": {six fields}, "status": {...}, "feed": {...}}
      {"type": "feed",   "symbol", "interval", "feed": {...}}   (on change and every second)
    """
    await websocket.accept()
    if timeframe not in ("1m", "5m", "15m", "1h", "1d"):
        await websocket.close(code=1008, reason="unsupported timeframe")
        return
    base = {"symbol": symbol, "interval": timeframe}

    if symbol == SAMPLE_SYMBOL:   # synthetic CSV: nothing live to stream
        try:
            while True:
                await websocket.send_json({**base, "type": "feed", "feed": {
                    "status": "offline", "detail": "synthetic CSV sample; no live feed",
                    "source": "synthetic CSV", "server_time_ms": now_ms()}})
                await asyncio.sleep(5)
        except (WebSocketDisconnect, RuntimeError):
            return

    feed = hub.acquire(symbol)
    queue: asyncio.Queue = asyncio.Queue()
    feed.listeners.add(queue)
    try:
        await websocket.send_json({**base, "type": "feed", "feed": feed.status()})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                event = {"kind": "feed"}
            if event["kind"] == "observation":
                series = candle_series(symbol, timeframe, None)
                item = _containing(series["candles"], timeframe, event["source_time_ms"])
                if item is not None:
                    await websocket.send_json({**base, "type": "candle", "candle": item["candle"],
                                               "status": item["status"], "feed": feed.status()})
                    continue
            await websocket.send_json({**base, "type": "feed", "feed": feed.status()})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        feed.listeners.discard(queue)
        await hub.release(feed)


@app.get("/api/live/{symbol}")
def live_status(symbol: str) -> dict:
    feed = hub.feeds.get(symbol)
    return {"symbol": symbol, "watching": feed is not None, **(feed.status() if feed else {"status": "not running"})}


@app.post("/api/live/{symbol}/disconnect")
async def live_disconnect(symbol: str) -> dict:
    """Test control: drop the upstream Yahoo connection while browsers stay connected."""
    feed = hub.feeds.get(symbol)
    if feed is None:
        raise HTTPException(404, f"no live feed running for {symbol}")
    await feed.pause()
    return {"symbol": symbol, **feed.status()}


@app.post("/api/live/{symbol}/connect")
def live_connect(symbol: str) -> dict:
    """Test control: let a paused upstream connection reconnect."""
    feed = hub.feeds.get(symbol)
    if feed is None:
        raise HTTPException(404, f"no live feed running for {symbol}")
    feed.resume()
    return {"symbol": symbol, "resumed": True}
