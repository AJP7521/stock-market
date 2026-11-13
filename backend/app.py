"""FastAPI application for the Live Market Workstation.

Run from the project root:
    python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
"""

import asyncio
import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from backend.settings import load_env

load_env()   # OPENAI_API_KEY and other settings from the ignored .env, before anything reads them

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from pydantic import BaseModel, Field

from backend.candles import iso_ms
from backend import automation  # noqa: E402  (also registers the simulator's pre-fill checks)
from backend.chart_images import ImageRejected, stored_path
from backend.decisions import create_snapshot, decide, get_decision, load_snapshot, make_provider
from backend.indicator_store import add_config, delete_config, list_configs
from backend.indicators import configured_values, describe_types, sma_crossover, validate_windows
from backend.live import LiveHub
from backend.paper import PaperError, account_state, execute, fill_paper_orders, open_account
from backend.pnl import history as pnl_history
from backend.recorder import connect, db_path, now_ms, record
from backend.replay import connect_readonly, replay, sessions
from backend.series import SAMPLE_SYMBOL, candle_series
from backend.timeframes import MINUTE_MS, aggregate, to_ms

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# LIVE_FEED=off starts the backend without any Yahoo connection (replay and review only).
LIVE_FEED = os.environ.get("LIVE_FEED", "on").lower() != "off"
# TEST_FEED=on enables POST /api/test/observations, which records labelled test prices (source='test').
TEST_FEED = os.environ.get("TEST_FEED", "off").lower() == "on"
PAPER_ACCOUNT = os.environ.get("PAPER_ACCOUNT", "paper")
PAPER_STARTING_CASH = float(os.environ.get("PAPER_STARTING_CASH", "1000"))
PAPER_ORDER_SIZE = int(os.environ.get("PAPER_ORDER_SIZE", "1"))
# Automation on TEST-labelled market data trades only in this disposable account (Prompt 12).
PAPER_TEST_ACCOUNT = os.environ.get("PAPER_TEST_ACCOUNT", "paper-test")
PAPER_TEST_STARTING_CASH = float(os.environ.get("PAPER_TEST_STARTING_CASH", "1000"))
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
    return {"status": "ok", "service": "backend", "live_feed": LIVE_FEED, "test_feed": TEST_FEED,
            "database": str(db_path())}


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

    if symbol == SAMPLE_SYMBOL or not LIVE_FEED:   # nothing live to stream
        detail = ("synthetic CSV sample; no live feed" if symbol == SAMPLE_SYMBOL
                  else "live feed is off (LIVE_FEED=off)")
        try:
            while True:
                await websocket.send_json({**base, "type": "feed", "feed": {
                    "status": "offline", "detail": detail,
                    "source": "synthetic CSV" if symbol == SAMPLE_SYMBOL else "yahoo",
                    "server_time_ms": now_ms()}})
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


@app.get("/api/replay/sessions")
def replay_sessions() -> dict:
    """Recorded symbols available for replay (read-only)."""
    try:
        conn = connect_readonly(db_path())
    except FileNotFoundError:
        return {"sessions": []}
    try:
        rows = sessions(conn)
    finally:
        conn.close()
    return {"sessions": [{**r, "first": iso_ms(r["first_ms"]), "last": iso_ms(r["last_ms"])} for r in rows]}


@app.get("/api/replay")
def replay_session(
    symbol: str,
    start: str = Query(..., description="UTC start of the source-time range, inclusive"),
    end: str = Query(..., description="UTC end of the source-time range, exclusive"),
    timeframe: Timeframe = "1m",
    short: int | None = Query(None, description="Short SMA window (with long)"),
    long: int | None = Query(None, description="Long SMA window (with short)"),
) -> dict:
    """Replay steps for one symbol and time range, computed read-only from SQLite."""
    start_ms, end_ms = _parse_as_of(start), _parse_as_of(end)
    if end_ms <= start_ms:
        raise HTTPException(422, "end must be after start")
    sma = None
    if short is not None or long is not None:
        _check_windows(short, long)
        sma = (short, long)
    try:
        conn = connect_readonly(db_path())
    except FileNotFoundError:
        raise HTTPException(404, "no recorded database yet")
    try:
        configs = list_configs_readonly(conn, symbol)
        return replay(conn, symbol, start_ms, end_ms, timeframe, sma,
                      indicators=lambda candles: configured_values(configs, candles))
    finally:
        conn.close()


def list_configs_readonly(conn, symbol: str) -> list[dict]:
    """Indicator configs from a read-only connection (an older database may not have the table yet)."""
    try:
        rows = conn.execute("SELECT id, symbol, name, type, settings, created_ms FROM indicator_configs "
                            "WHERE symbol = ? ORDER BY id", (symbol,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"id": r[0], "symbol": r[1], "name": r[2], "type": r[3], "settings": json.loads(r[4]),
             "created_ms": r[5]} for r in rows]


def _check_windows(short: int | None, long: int | None) -> None:
    if short is None or long is None:
        raise HTTPException(422, "give both short and long SMA windows")
    try:
        validate_windows(short, long)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/indicators/sma")
def sma_indicator(
    symbol: str = SAMPLE_SYMBOL,
    timeframe: Timeframe = "1m",
    short: int = Query(..., description="Short window: positive whole number, smaller than long"),
    long: int = Query(..., description="Long window: positive whole number"),
    as_of: str | None = Query(None, description="Only use candles available at this UTC time"),
) -> dict:
    """Short and long SMAs of completed closes, with crossover signals (calculated in Python)."""
    _check_windows(short, long)
    series = candle_series(symbol, timeframe, _parse_as_of(as_of))
    result = sma_crossover([{**c["candle"], "complete": c["status"]["complete"]} for c in series["candles"]],
                           short, long)
    return {"symbol": symbol, "timeframe": timeframe, "as_of": as_of, **result}


# ---------------------------------------------------------------- configurable indicators (Prompt 9B)

@app.get("/api/indicator-types")
def indicator_types() -> dict:
    """Indicator types Vue can offer, with their settings, units, inputs and output."""
    return {"types": describe_types()}


@app.get("/api/indicator-configs")
def indicator_configs(symbol: str) -> dict:
    with connect(db_path()) as conn:
        return {"symbol": symbol, "indicators": list_configs(conn, symbol)}


class IndicatorConfigIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    name: str
    type: str
    settings: dict = Field(default_factory=dict)


@app.post("/api/indicator-configs", status_code=201)
def create_indicator_config(body: IndicatorConfigIn) -> dict:
    """Validate and store a named indicator instance; invalid settings return 422 and store nothing."""
    with connect(db_path()) as conn:
        try:
            return add_config(conn, body.symbol, body.name, body.type, body.settings)
        except ValueError as exc:
            raise HTTPException(422, str(exc))


@app.delete("/api/indicator-configs/{config_id}")
def remove_indicator_config(config_id: int) -> dict:
    with connect(db_path()) as conn:
        if not delete_config(conn, config_id):
            raise HTTPException(404, f"no indicator with id {config_id}")
    return {"deleted": config_id}


@app.get("/api/indicator-values")
def indicator_values(
    symbol: str = SAMPLE_SYMBOL,
    timeframe: Timeframe = "1m",
    as_of: str | None = Query(None, description="Only use candles available at this UTC time"),
) -> dict:
    """Every configured indicator for the symbol, calculated over its candles at this timeframe."""
    series = candle_series(symbol, timeframe, _parse_as_of(as_of))
    candles = [{**c["candle"], "complete": c["status"]["complete"]} for c in series["candles"]]
    with connect(db_path()) as conn:
        configs = list_configs(conn, symbol)
    return {"symbol": symbol, "timeframe": timeframe, "as_of": as_of,
            "indicators": configured_values(configs, candles)}


# ---------------------------------------------------------------- paper trading (Prompt 9C)

def _paper_conn():
    conn = connect(db_path())
    open_account(conn, PAPER_ACCOUNT, PAPER_STARTING_CASH)   # created once with the starting cash
    return conn


@app.get("/api/paper/account")
def paper_account() -> dict:
    """Cash, positions and order history (with fills) of the local paper account."""
    conn = _paper_conn()
    try:
        return account_state(conn, PAPER_ACCOUNT)
    finally:
        conn.close()


@app.get("/api/paper/pnl")
def paper_pnl(symbol: str | None = None) -> dict:
    """P&L history rows (fills and completed-candle valuations), oldest first."""
    conn = _paper_conn()
    try:
        return {"account": PAPER_ACCOUNT, "symbol": symbol, "rows": pnl_history(conn, PAPER_ACCOUNT, symbol)}
    finally:
        conn.close()


class OrderIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    action: str
    quantity: int | float | str | None = None   # validated by the execution function, not here
    request_id: str


@app.post("/api/paper/orders")
def paper_order(body: OrderIn) -> dict:
    """Submit buy/sell/close (creates an order) or hold/wait (no order) through the one execution function."""
    conn = _paper_conn()
    try:
        result = execute(conn, PAPER_ACCOUNT, body.symbol.upper(), body.action, body.quantity, body.request_id)
        return {**result, "account": account_state(conn, PAPER_ACCOUNT)}
    except PaperError as exc:
        raise HTTPException(422, str(exc))
    finally:
        conn.close()


class TestObservationIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    price: float = Field(gt=0)


@app.post("/api/test/observations")
def test_observation(body: TestObservationIn) -> dict:
    """TEST FEED ONLY (TEST_FEED=on): record a labelled test price now, then let it fill pending orders."""
    if not TEST_FEED:
        raise HTTPException(403, "the test feed is off; start the backend with TEST_FEED=on")
    conn = connect(db_path())
    try:
        received = now_ms()
        last = conn.execute("SELECT MAX(day_volume) FROM observations WHERE ticker = ?", (body.symbol,)).fetchone()[0]
        message = {"id": body.symbol, "price": body.price, "time": str(received), "day_volume": str((last or 0) + 100)}
        result = record(conn, message, received, source="test")
        fills = fill_paper_orders(conn, result["observation_id"])
        return {"observation_id": result["observation_id"], "source": "test", "price": body.price,
                "received_at_ms": received, "orders_decided": fills}
    finally:
        conn.close()


# ---------------------------------------------------------------- decisions (Prompt 10)

class SnapshotIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    timeframe: Timeframe = "1m"
    short: int = 5
    long: int = 20
    as_of: str | None = None          # replay: only observations received by this time
    candle_time: str | None = None    # Prompt 10A: freeze at this completed candle and add the chart section
    chart_candles: int = Field(60, ge=5, le=500)


@app.post("/api/decisions/snapshots", status_code=201)
def create_decision_snapshot(body: SnapshotIn) -> dict:
    """Freeze candle, SMAs, crossover and paper account for one symbol; no model call."""
    _check_windows(body.short, body.long)
    conn = _paper_conn()
    try:
        return create_snapshot(conn, body.symbol.upper(), body.timeframe, body.short, body.long, PAPER_ACCOUNT,
                               PAPER_ORDER_SIZE, _parse_as_of(body.as_of), body.candle_time, body.chart_candles)
    finally:
        conn.close()


@app.get("/api/decisions/snapshots/{snapshot_id}")
def read_decision_snapshot(snapshot_id: int) -> dict:
    conn = _paper_conn()
    try:
        snap = load_snapshot(conn, snapshot_id)
    finally:
        conn.close()
    if snap is None:
        raise HTTPException(404, f"no snapshot {snapshot_id}")
    return snap


class DecisionIn(BaseModel):
    snapshot_id: int | None = None    # use an existing snapshot (e.g. one a chart was rendered from)
    symbol: str | None = None         # or create one now from these settings
    timeframe: Timeframe = "1m"
    short: int = 5
    long: int = 20
    as_of: str | None = None
    image_png: str | None = None      # Prompt 10A: data:image/png;base64,... captured from the snapshot's chart
    capture: dict | None = None       # what the browser captured: snapshot_id, symbol, interval, cutoff
    review: bool = True               # Prompt 11: a second (reviewer) call must accept the analyst's proposal


@app.post("/api/decisions")
def create_decision(body: DecisionIn) -> dict:
    """Ask the model what action fits one frozen snapshot; validate and store it. The paper account is untouched."""
    conn = _paper_conn()
    try:
        snapshot_id = body.snapshot_id
        if snapshot_id is None:
            if not body.symbol:
                raise HTTPException(422, "give a snapshot_id or a symbol")
            _check_windows(body.short, body.long)
            snapshot_id = create_snapshot(conn, body.symbol.upper(), body.timeframe, body.short, body.long,
                                          PAPER_ACCOUNT, PAPER_ORDER_SIZE, _parse_as_of(body.as_of))["snapshot_id"]
        if body.image_png is not None and body.snapshot_id is None:
            raise HTTPException(422, "a chart image needs the snapshot_id it was captured from")
        try:
            return decide(conn, snapshot_id, make_provider(), image=body.image_png, capture=body.capture,
                          review=body.review)
        except ImageRejected as exc:      # nothing stored, no provider call
            raise HTTPException(422, {"problem": exc.problem, "recapture": exc.recapture}) from None
    finally:
        conn.close()


@app.get("/api/decisions/{decision_id}")
def read_decision(decision_id: int) -> dict:
    conn = _paper_conn()
    try:
        d = get_decision(conn, decision_id)
    finally:
        conn.close()
    if d is None:
        raise HTTPException(404, f"no decision {decision_id}")
    return d


@app.get("/api/decisions/{decision_id}/image")
def read_decision_image(decision_id: int):
    """The PNG the model was sent for this decision (stored beside the database)."""
    conn = _paper_conn()
    try:
        d = get_decision(conn, decision_id)
        path = stored_path(conn, d["image"]["path"]) if d and d["image"] else None
    finally:
        conn.close()
    if path is None or not path.exists():
        raise HTTPException(404, f"decision {decision_id} has no stored chart image")
    return FileResponse(path, media_type="image/png")


# ---------------------------------------------------------------- paper automation (Prompt 12)

class AutomationIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    timeframe: Timeframe = "1m"
    short: int = 5
    long: int = 20


def _automation_view(conn, body_symbol: str, timeframe: str, short: int, long: int) -> dict:
    st = automation.state(conn, body_symbol, timeframe, short, long)
    acct = None
    if st is not None:
        acct = automation.account_state(conn, st["account_id"])
        acct = {k: acct[k] for k in ("account", "starting_cash", "cash", "positions", "fill_count")}
    return {"state": st, "account": acct, "runs": automation.runs(conn, body_symbol, timeframe, short, long),
            "max_decision_age_s": automation.DECISION_MAX_AGE_S}


@app.post("/api/automation/start")
def automation_start(body: AutomationIn) -> dict:
    """Enable paper automation for one symbol, interval and SMA strategy (a new generation)."""
    _check_windows(body.short, body.long)
    conn = _paper_conn()
    try:
        sym = body.symbol.upper()
        automation.start(conn, sym, body.timeframe, body.short, body.long, paper_account=PAPER_ACCOUNT,
                         test_account=PAPER_TEST_ACCOUNT, test_starting_cash=PAPER_TEST_STARTING_CASH)
        return _automation_view(conn, sym, body.timeframe, body.short, body.long)
    finally:
        conn.close()


@app.post("/api/automation/stop")
def automation_stop(body: AutomationIn) -> dict:
    """Disable new executions, including runs still waiting for a model reply and pending automation orders."""
    conn = _paper_conn()
    try:
        sym = body.symbol.upper()
        automation.stop(conn, sym, body.timeframe, body.short, body.long)
        return _automation_view(conn, sym, body.timeframe, body.short, body.long)
    finally:
        conn.close()


@app.get("/api/automation/status")
def automation_status(symbol: str, timeframe: Timeframe = "1m", short: int = 5, long: int = 20) -> dict:
    conn = _paper_conn()
    try:
        return _automation_view(conn, symbol.upper(), timeframe, short, long)
    finally:
        conn.close()


class RunIn(AutomationIn):
    candle_time: str
    chart_candles: int = Field(60, ge=5, le=500)


@app.post("/api/automation/runs")
def automation_claim(body: RunIn) -> dict:
    """Claim one newly completed candle (deduplicated) and freeze its snapshot for the automation account."""
    conn = _paper_conn()
    try:
        return automation.claim_run(conn, body.symbol.upper(), body.timeframe, body.short, body.long,
                                    body.candle_time, order_size=PAPER_ORDER_SIZE, chart_candles=body.chart_candles)
    except automation.AutomationError as exc:
        raise HTTPException(409, str(exc)) from None
    finally:
        conn.close()


class AnalyseIn(BaseModel):
    image_png: str | None = None
    capture: dict | None = None


@app.post("/api/automation/runs/{run_id}/analyse")
def automation_analyse(run_id: int, body: AnalyseIn) -> dict:
    """Analyst + reviewer on the run's snapshot and PNG; an approved, eligible decision becomes a paper order."""
    conn = _paper_conn()
    try:
        return automation.analyse_run(conn, run_id, make_provider(), body.image_png, body.capture)
    except automation.AutomationError as exc:
        raise HTTPException(404, str(exc)) from None
    finally:
        conn.close()


class FailIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@app.post("/api/automation/runs/{run_id}/fail")
def automation_fail(run_id: int, body: FailIn) -> dict:
    """Record that the chart or image was unavailable; nothing is executed."""
    conn = _paper_conn()
    try:
        return {"run": automation.fail_run(conn, run_id, body.reason)}
    except automation.AutomationError as exc:
        raise HTTPException(404, str(exc)) from None
    finally:
        conn.close()


class ApplyIn(BaseModel):
    decision_id: int


@app.post("/api/automation/apply")
def automation_apply(body: ApplyIn) -> dict:
    """Apply an automation decision (idempotent: a decision already handled returns its original outcome)."""
    conn = _paper_conn()
    try:
        return automation.apply_decision(conn, body.decision_id)
    except automation.AutomationError as exc:
        raise HTTPException(409, str(exc)) from None
    finally:
        conn.close()


@app.get("/api/automation/trace/{decision_id}")
def automation_trace(decision_id: int) -> dict:
    """Follow one decision ID: crossover, proposal, review, final check, order, fill or skip, account state."""
    conn = _paper_conn()
    try:
        return automation.trace(conn, decision_id)
    except automation.AutomationError as exc:
        raise HTTPException(404, str(exc)) from None
    finally:
        conn.close()
