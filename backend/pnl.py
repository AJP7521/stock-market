"""Profit-and-loss history for the long-only paper account (weighted average cost).

A row is recorded
  * after each fill ("fill" event), valued at the fill price, and
  * when a completed candle supplies a new valuation price for an open position
    ("valuation" event), valued at that candle's close.
Each row links to the paper account, symbol, event, event time and the price used,
with its source and time. Rows are only ever added: a new price or a closed position
never changes earlier rows.

Weighted average cost
---------------------
  cost basis   total purchase cost of the shares still held
  average cost basis / shares
  buy          basis += qty x price
  sale         realized += qty x (price - average cost); the remaining shares keep the
               same average cost (basis -= qty x average cost)
  unrealized   shares x (valuation price - average cost) = shares x price - basis
  total        cumulative realized + unrealized

Fees are zero (Prompt 9C). Money is integer micro-dollars. A share's share of the basis
is rounded to the nearest micro-dollar only when the basis does not divide evenly.

Each row has a unique event_key ("fill:<fill id>" or "valuation:<symbol>:<candle start>"),
so reprocessing the same event leaves a single entry. If an open position has no usable
price, the row's valuation is labelled "unavailable" and unrealized/total are NULL.
"""

import sqlite3
from fractions import Fraction

from backend.candles import iso_ms, minute_candles
from backend.timeframes import MINUTE_MS, to_ms

MICROS = 1_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_cost_basis (
    account_id       TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    basis_micros     INTEGER NOT NULL DEFAULT 0,   -- cost of the shares still held
    realized_micros  INTEGER NOT NULL DEFAULT 0,   -- cumulative realized P&L
    PRIMARY KEY (account_id, symbol)
);
CREATE TABLE IF NOT EXISTS pnl_history (
    id                 INTEGER PRIMARY KEY,
    account_id         TEXT NOT NULL,
    symbol             TEXT NOT NULL,
    event_key          TEXT NOT NULL UNIQUE,      -- reprocessing the same event keeps one row
    event_type         TEXT NOT NULL CHECK (event_type IN ('fill', 'valuation')),
    event_ms           INTEGER NOT NULL,          -- when the event happened (fill or candle end)
    description        TEXT NOT NULL,
    fill_id            INTEGER,                   -- paper_fills.id for fill events
    price_micros       INTEGER,                   -- valuation price used; NULL if unavailable
    price_source       TEXT,
    price_time_ms      INTEGER,
    cash_micros        INTEGER NOT NULL,          -- account cash after the event
    quantity           INTEGER NOT NULL,          -- shares held after the event
    basis_micros       INTEGER NOT NULL,
    realized_micros    INTEGER NOT NULL,          -- cumulative
    unrealized_micros  INTEGER,                   -- NULL when valuation is unavailable
    total_micros       INTEGER,
    valuation_status   TEXT NOT NULL CHECK (valuation_status IN ('ok', 'unavailable', 'no position'))
);
CREATE INDEX IF NOT EXISTS pnl_by_account ON pnl_history (account_id, symbol, id);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _money(micros) -> float | None:
    return None if micros is None else micros / MICROS


def _basis(conn, account_id: str, symbol: str) -> tuple[int, int]:
    row = conn.execute("SELECT basis_micros, realized_micros FROM paper_cost_basis WHERE account_id = ? AND symbol = ?",
                       (account_id, symbol)).fetchone()
    return (row[0], row[1]) if row else (0, 0)


def _valuation(quantity: int, basis: int, realized: int, price_micros: int | None) -> tuple:
    if quantity == 0:
        return 0, realized, "no position"
    if price_micros is None:
        return None, None, "unavailable"
    unrealized = quantity * price_micros - basis
    return unrealized, realized + unrealized, "ok"


def _insert(conn, **row) -> bool:
    cur = conn.execute(
        f"INSERT OR IGNORE INTO pnl_history ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
        tuple(row.values()))
    return cur.rowcount == 1


def record_fill(conn: sqlite3.Connection, fill_id: int) -> bool:
    """Update the cost basis for one fill and add its P&L row. Call inside the fill's transaction.

    Returns False (and changes nothing) if this fill was already recorded.
    """
    ensure_schema(conn)
    key = f"fill:{fill_id}"
    if conn.execute("SELECT 1 FROM pnl_history WHERE event_key = ?", (key,)).fetchone():
        return False
    f = conn.execute("""SELECT account_id, symbol, side, quantity, price_micros, observation_id, executed_ms,
                               cash_after_micros, position_after FROM paper_fills WHERE id = ?""", (fill_id,)).fetchone()
    account_id, symbol, side, qty, price, obs_id, executed, cash_after, pos_after = tuple(f)
    basis, realized = _basis(conn, account_id, symbol)
    held_before = pos_after - qty if side == "buy" else pos_after + qty
    if side == "buy":
        basis += qty * price
    else:
        removed = basis if qty == held_before else round(Fraction(basis * qty, held_before))
        realized += qty * price - removed
        basis -= removed
    conn.execute("INSERT INTO paper_cost_basis (account_id, symbol, basis_micros, realized_micros) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT (account_id, symbol) DO UPDATE SET basis_micros = excluded.basis_micros, "
                 "realized_micros = excluded.realized_micros", (account_id, symbol, basis, realized))
    unrealized, total, status = _valuation(pos_after, basis, realized, price)
    return _insert(conn, account_id=account_id, symbol=symbol, event_key=key, event_type="fill", event_ms=executed,
                   description=f"{side} {qty} at {price / MICROS:g}", fill_id=fill_id, price_micros=price,
                   price_source=f"fill price (paper fill #{fill_id}, observation #{obs_id})", price_time_ms=executed,
                   cash_micros=cash_after, quantity=pos_after, basis_micros=basis, realized_micros=realized,
                   unrealized_micros=unrealized, total_micros=total, valuation_status=status)


def record_valuation(conn: sqlite3.Connection, account_id: str, symbol: str, candle: dict | None,
                     as_of_ms: int) -> bool:
    """Add a valuation row for an open position from a completed one-minute candle.

    `candle` is the latest completed candle (six fields) or None when there is no
    usable price; then the row is labelled "unavailable". Returns True if a row was added.
    """
    ensure_schema(conn)
    qty_row = conn.execute("SELECT quantity FROM paper_positions WHERE account_id = ? AND symbol = ?",
                           (account_id, symbol)).fetchone()
    quantity = qty_row[0] if qty_row else 0
    if quantity == 0:
        return False
    last_fill = conn.execute("SELECT MAX(executed_ms) FROM paper_fills WHERE account_id = ? AND symbol = ?",
                             (account_id, symbol)).fetchone()[0] or 0
    basis, realized = _basis(conn, account_id, symbol)
    cash = conn.execute("SELECT cash_micros FROM paper_accounts WHERE id = ?", (account_id,)).fetchone()[0]
    if candle is None:
        key, price, end, source = f"valuation:{symbol}:unavailable:{last_fill}", None, as_of_ms, None
        description = "valuation unavailable: no completed candle since the last fill"
    else:
        start = to_ms(candle["timestamp"])
        end = start + MINUTE_MS
        if end <= last_fill:          # this candle closed before the latest fill; the fill row is newer
            return False
        price = round(Fraction(str(candle["close"])) * MICROS)
        key = f"valuation:{symbol}:{candle['timestamp']}"
        source = f"1m candle close {candle['timestamp']}"
        description = f"value {quantity} shares at {candle['close']:g}"
    unrealized, total, status = _valuation(quantity, basis, realized, price)
    with conn:
        return _insert(conn, account_id=account_id, symbol=symbol, event_key=key, event_type="valuation",
                       event_ms=end, description=description, fill_id=None, price_micros=price, price_source=source,
                       price_time_ms=end if candle else None, cash_micros=cash, quantity=quantity, basis_micros=basis,
                       realized_micros=realized, unrealized_micros=unrealized, total_micros=total,
                       valuation_status=status)


def value_open_positions(conn: sqlite3.Connection, symbol: str, as_of_ms: int) -> int:
    """Value every open position in `symbol` at the latest completed candle as of `as_of_ms`."""
    ensure_schema(conn)
    accounts = [r[0] for r in conn.execute(
        "SELECT account_id FROM paper_positions WHERE symbol = ? AND quantity > 0", (symbol,))]
    if not accounts:
        return 0
    completed = [c["candle"] for c in minute_candles(conn, symbol, as_of_ms) if c["status"]["complete"]]
    candle = completed[-1] if completed else None
    if candle is None:
        return 0                      # nothing completed yet: the fill row already values the position
    return sum(record_valuation(conn, a, symbol, candle, as_of_ms) for a in accounts)


def history(conn: sqlite3.Connection, account_id: str, symbol: str | None = None) -> list[dict]:
    """P&L rows in the order they were recorded, in dollars, with ISO times."""
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM pnl_history WHERE account_id = ?" + (" AND symbol = ?" if symbol else "") + " ORDER BY id"
    rows = []
    for r in conn.execute(sql, (account_id, symbol) if symbol else (account_id,)):
        q = r["quantity"]
        rows.append({
            "id": r["id"], "account": r["account_id"], "symbol": r["symbol"], "event_key": r["event_key"],
            "event": r["event_type"], "description": r["description"], "event_time": iso_ms(r["event_ms"]),
            "fill_id": r["fill_id"], "price": _money(r["price_micros"]), "price_source": r["price_source"],
            "price_time": iso_ms(r["price_time_ms"]), "cash": _money(r["cash_micros"]), "shares": q,
            "average_cost": _money(round(Fraction(r["basis_micros"], q))) if q else None,
            "realized": _money(r["realized_micros"]), "unrealized": _money(r["unrealized_micros"]),
            "total": _money(r["total_micros"]), "valuation": r["valuation_status"],
        })
    return rows
