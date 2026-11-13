"""Record Yahoo Finance stream messages in a local SQLite database (Python's sqlite3).

Two tables keep every message traceable:

  observations  one row per *distinct* message content for a ticker. It holds the
                parsed fields (ticker, price, source time, volumes), the original
                decoded message as canonical JSON (raw_json) and its SHA-256 hash.
  receipts      one row per message *arrival*, in receipt order (seq), with the
                backend receipt time and the observation it resolved to.

How messages are classified
---------------------------
Exact repeat: the same ticker and identical decoded content (same content_hash).
  No new observation is created. A receipt row is still added, so the repeat
  remains visible, and the observation's receipt_count goes up.

Changed message with the same source time: a different content_hash, so it is
  stored as a new observation. Earlier rows are never updated or deleted.
  Yahoo's PricingData has no correction or sequence field, and `time` has
  one-second resolution, so several distinct trades can share a source time.
  The feed therefore cannot establish that a changed message is a correction,
  and both are kept with revises_id = NULL. When a caller *can* identify a
  revision (e.g. a feed that sends an explicit correction), it passes
  revises_id and the earlier row remains unchanged.

Late arrivals: source_time_ms (when Yahoo says it happened) and receipts.seq /
  received_at_ms (when the backend got it) are stored separately, so a reader
  can order by either.

Reading without double counting: query `observations` (distinct contents), not
  `receipts`. `receipts` is the arrival log.

Commands
--------
  python3 -m backend.recorder init                       # create tables
  python3 -m backend.recorder record AAPL --seconds 30   # live capture
  python3 -m backend.recorder show [--ticker AAPL]       # print stored rows
The database path is backend/data/market.sqlite unless --db or MARKET_DB is set.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "backend" / "data" / "market.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id                  INTEGER PRIMARY KEY,          -- first-arrival order of distinct contents
    ticker              TEXT    NOT NULL,
    source_time_ms      INTEGER NOT NULL,             -- Yahoo `time`, epoch ms UTC
    price               REAL    NOT NULL,
    day_volume          INTEGER,                      -- running session total; NULL if absent
    last_size           INTEGER,                      -- size of the last trade; NULL if absent
    market_hours        INTEGER,
    exchange            TEXT,
    source              TEXT    NOT NULL DEFAULT 'yahoo',  -- 'yahoo' or 'test'
    content_hash        TEXT    NOT NULL,             -- sha256(ticker + canonical raw_json)
    raw_json            TEXT    NOT NULL,             -- decoded message, keys sorted
    revises_id          INTEGER REFERENCES observations(id),  -- set only for identified revisions
    first_received_ms   INTEGER NOT NULL,
    receipt_count       INTEGER NOT NULL DEFAULT 1,
    UNIQUE (ticker, content_hash)
);
CREATE INDEX IF NOT EXISTS obs_ticker_time ON observations (ticker, source_time_ms, id);

CREATE TABLE IF NOT EXISTS receipts (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,    -- backend receipt order
    observation_id  INTEGER NOT NULL REFERENCES observations(id),
    received_at_ms  INTEGER NOT NULL,
    is_repeat       INTEGER NOT NULL                      -- 1 if content was already stored
);
CREATE INDEX IF NOT EXISTS receipts_obs ON receipts (observation_id);
"""


def db_path(arg: str | None = None) -> Path:
    return Path(arg or os.environ.get("MARKET_DB") or DEFAULT_DB)


def connect(path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) the database and ensure the schema exists."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def _int_or_none(value):
    return int(value) if value not in (None, "") else None


def canonical(message: dict) -> str:
    return json.dumps(message, sort_keys=True, separators=(",", ":"))


def record(conn: sqlite3.Connection, message: dict, received_at_ms: int,
           source: str = "yahoo", revises_id: int | None = None) -> dict:
    """Store one decoded stream message. Returns {"observation_id", "seq", "is_repeat"}.

    Raises ValueError if the message lacks a ticker, price or source time.
    """
    ticker = message.get("id")
    if not ticker or message.get("price") is None or message.get("time") in (None, ""):
        raise ValueError(f"message needs id, price and time: {message}")
    raw = canonical(message)
    content_hash = hashlib.sha256(f"{ticker}\n{raw}".encode()).hexdigest()

    with conn:  # one transaction for the observation and its receipt
        row = conn.execute(
            "SELECT id FROM observations WHERE ticker = ? AND content_hash = ?",
            (ticker, content_hash),
        ).fetchone()
        if row:
            obs_id, is_repeat = row["id"], 1
            conn.execute("UPDATE observations SET receipt_count = receipt_count + 1 WHERE id = ?", (obs_id,))
        else:
            is_repeat = 0
            obs_id = conn.execute(
                """INSERT INTO observations (ticker, source_time_ms, price, day_volume, last_size,
                       market_hours, exchange, source, content_hash, raw_json, revises_id, first_received_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ticker, int(message["time"]), float(message["price"]),
                 _int_or_none(message.get("day_volume")), _int_or_none(message.get("last_size")),
                 _int_or_none(message.get("market_hours")), message.get("exchange"),
                 source, content_hash, raw, revises_id, received_at_ms),
            ).lastrowid
        seq = conn.execute(
            "INSERT INTO receipts (observation_id, received_at_ms, is_repeat) VALUES (?, ?, ?)",
            (obs_id, received_at_ms, is_repeat),
        ).lastrowid
    return {"observation_id": obs_id, "seq": seq, "is_repeat": bool(is_repeat)}


# Test clock (SmokeTest, Prompt 14): when set, every server-side "now" is this fixed time. Only the TEST_FEED-gated
# /api/test/clock route sets it; it is None in normal use.
TEST_NOW_MS: int | None = None


def now_ms() -> int:
    if TEST_NOW_MS is not None:
        return TEST_NOW_MS
    return round(datetime.now(timezone.utc).timestamp() * 1000)


def iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def observations(conn: sqlite3.Connection, ticker: str | None = None) -> list[dict]:
    """Distinct observations ordered by source time, then first arrival (no double counting)."""
    sql = "SELECT * FROM observations" + (" WHERE ticker = ?" if ticker else "") + " ORDER BY source_time_ms, id"
    return [dict(r) for r in conn.execute(sql, (ticker,) if ticker else ())]


def receipts(conn: sqlite3.Connection) -> list[dict]:
    """Every arrival in receipt order, joined to its observation."""
    return [dict(r) for r in conn.execute(
        """SELECT r.seq, r.received_at_ms, r.is_repeat, o.id AS observation_id, o.ticker,
                  o.source_time_ms, o.price, o.day_volume
           FROM receipts r JOIN observations o ON o.id = r.observation_id ORDER BY r.seq""")]


async def record_stream(ticker: str, seconds: float, path: Path) -> dict:
    """Subscribe to `ticker` with yfinance and record every message for `seconds`."""
    from yfinance import AsyncWebSocket

    conn = connect(path)
    counts = {"messages": 0, "new": 0, "repeats": 0, "connected": False, "error": None}

    def handler(message: dict) -> None:
        if "error" in message:  # yfinance could not decode it; nothing to store
            return
        result = record(conn, message, now_ms())
        counts["messages"] += 1
        counts["repeats" if result["is_repeat"] else "new"] += 1

    ws = AsyncWebSocket(verbose=False)
    listener = None
    try:
        await ws.subscribe(ticker)
        counts["connected"] = ws._ws is not None
        listener = asyncio.create_task(ws.listen(handler))
        await asyncio.sleep(seconds)
    except Exception as exc:
        counts["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if listener:
            listener.cancel()
        try:
            await ws.unsubscribe(ticker)
            await ws.close()
        except Exception:
            pass
        conn.close()
    return counts


def _print_rows(conn: sqlite3.Connection, ticker: str | None) -> None:
    print("observations (distinct contents, ordered by source time):")
    print(f"{'id':>4} {'ticker':6} {'source_time':24} {'price':>10} {'day_volume':>11} {'src':5} {'rcpts':>5} {'revises':>7}")
    for o in observations(conn, ticker):
        print(f"{o['id']:>4} {o['ticker']:6} {iso(o['source_time_ms']):24} {o['price']:>10} "
              f"{str(o['day_volume']):>11} {o['source']:5} {o['receipt_count']:>5} {str(o['revises_id']):>7}")
    print("receipts (arrival order):")
    print(f"{'seq':>4} {'obs':>4} {'received_at':24} {'repeat':>6}")
    for r in receipts(conn):
        if ticker and r["ticker"] != ticker:
            continue
        print(f"{r['seq']:>4} {r['observation_id']:>4} {iso(r['received_at_ms']):24} {r['is_repeat']:>6}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite recorder for Yahoo Finance observations")
    parser.add_argument("--db", help=f"database path (default {DEFAULT_DB.relative_to(PROJECT_ROOT)} or $MARKET_DB)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the database and tables")
    rec = sub.add_parser("record", help="record a live ticker for a fixed time")
    rec.add_argument("ticker", nargs="?", default="AAPL")
    rec.add_argument("--seconds", type=float, default=30)
    show = sub.add_parser("show", help="print stored observations and receipts")
    show.add_argument("--ticker")
    args = parser.parse_args()
    path = db_path(args.db)

    if args.cmd == "init":
        connect(path).close()
        print(f"initialized {path}")
    elif args.cmd == "record":
        print(f"recording {args.ticker} for {args.seconds:g}s into {path}")
        print(asyncio.run(record_stream(args.ticker, args.seconds, path)))
    elif args.cmd == "show":
        with connect(path) as conn:
            _print_rows(conn, args.ticker)


if __name__ == "__main__":
    main()
