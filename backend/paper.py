"""Local, long-only paper trading account in SQLite.

One execution function, `execute()`, handles every request (manual buttons in Vue
today, the decision agent later):

  buy N    adds N whole shares         -> creates an order
  sell N   removes N shares (N <= held) -> creates an order
  close    sells the whole position     -> creates an order
  hold     keep an existing position    -> no order
  wait     stay in cash                 -> no order

Simulated fill rule (first implementation)
------------------------------------------
* An accepted order is "pending" until the next eligible observed price: the first
  observation of that symbol *received after the order was submitted*. It fills at
  that observation's price, with zero fees. In replay the same rule applies with the
  replay clock, because observations are processed only as they arrive.
* At submission: quantity must be a positive whole number (buy/sell), and a sale
  cannot exceed the shares held minus shares already reserved by pending sales.
  Close needs an open position.
* At fill: cash (buy) or shares (sell/close) are checked again. If they are no longer
  enough, the order is rejected at that point with the reason.
* The fill, the order status, cash and position change in **one SQLite transaction**.
* Each request carries a stable request_id. Retrying the same request_id returns the
  original order (and fill) and never creates another one.

Money is stored as integer micro-dollars (1e-6 USD), so 100 * 4 shares or prices with
four decimals are exact. The API converts to dollars for display.
"""

import sqlite3
from decimal import Decimal

from backend.pnl import ensure_schema as ensure_pnl_schema
from backend.pnl import record_fill, value_open_positions
from backend.recorder import iso, now_ms

MICROS = 1_000_000
ORDER_ACTIONS = {"buy", "sell", "close"}
NO_ORDER_ACTIONS = {"hold", "wait"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_accounts (
    id                    TEXT PRIMARY KEY,
    starting_cash_micros  INTEGER NOT NULL,
    cash_micros           INTEGER NOT NULL CHECK (cash_micros >= 0),
    created_ms            INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_positions (
    account_id  TEXT NOT NULL REFERENCES paper_accounts(id),
    symbol      TEXT NOT NULL,
    quantity    INTEGER NOT NULL CHECK (quantity >= 0),
    PRIMARY KEY (account_id, symbol)
);
CREATE TABLE IF NOT EXISTS paper_orders (
    id              INTEGER PRIMARY KEY,
    request_id      TEXT NOT NULL UNIQUE,
    account_id      TEXT NOT NULL REFERENCES paper_accounts(id),
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL CHECK (side IN ('buy', 'sell', 'close')),
    quantity        INTEGER NOT NULL,          -- for close: shares held at submission
    submitted_ms    INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'filled', 'rejected')),
    reason          TEXT,                       -- rejection reason
    decided_ms      INTEGER,                    -- when it was filled or rejected
    origin          TEXT NOT NULL DEFAULT 'manual'
);
CREATE INDEX IF NOT EXISTS paper_orders_pending ON paper_orders (symbol, status, submitted_ms);
CREATE TABLE IF NOT EXISTS paper_fills (
    id                    INTEGER PRIMARY KEY,
    order_id              INTEGER NOT NULL UNIQUE REFERENCES paper_orders(id),
    account_id            TEXT NOT NULL,
    symbol                TEXT NOT NULL,
    side                  TEXT NOT NULL,
    quantity              INTEGER NOT NULL,
    price                 REAL NOT NULL,
    price_micros          INTEGER NOT NULL,
    observation_id        INTEGER NOT NULL,     -- observations.id that supplied the price
    observation_time_ms   INTEGER NOT NULL,     -- its source time
    executed_ms           INTEGER NOT NULL,     -- when the fill was applied (observation receipt time)
    cash_after_micros     INTEGER NOT NULL,
    position_after        INTEGER NOT NULL
);
"""


class PaperError(ValueError):
    """A request that cannot be accepted, with a readable reason."""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    ensure_pnl_schema(conn)


def to_micros(dollars) -> int:
    return int((Decimal(str(dollars)) * MICROS).to_integral_value())


def dollars(micros: int | None) -> float | None:
    return None if micros is None else micros / MICROS


def open_account(conn: sqlite3.Connection, account_id: str, starting_cash: float) -> dict:
    """Create the account if it does not exist (existing accounts keep their balances)."""
    ensure_schema(conn)
    with conn:
        conn.execute("INSERT OR IGNORE INTO paper_accounts (id, starting_cash_micros, cash_micros, created_ms) "
                     "VALUES (?, ?, ?, ?)", (account_id, to_micros(starting_cash), to_micros(starting_cash), now_ms()))
    return account_state(conn, account_id)


def _position(conn, account_id: str, symbol: str) -> int:
    row = conn.execute("SELECT quantity FROM paper_positions WHERE account_id = ? AND symbol = ?",
                       (account_id, symbol)).fetchone()
    return row[0] if row else 0


def _reserved_for_sale(conn, account_id: str, symbol: str) -> int:
    return conn.execute("SELECT COALESCE(SUM(quantity), 0) FROM paper_orders WHERE account_id = ? AND symbol = ? "
                        "AND status = 'pending' AND side IN ('sell', 'close')", (account_id, symbol)).fetchone()[0]


def _order(conn, order_id: int) -> dict:
    conn.row_factory = sqlite3.Row
    o = dict(conn.execute("SELECT * FROM paper_orders WHERE id = ?", (order_id,)).fetchone())
    f = conn.execute("SELECT * FROM paper_fills WHERE order_id = ?", (order_id,)).fetchone()
    o["submitted_at"] = iso(o["submitted_ms"])
    o["decided_at"] = iso(o["decided_ms"])
    o["fill"] = None if f is None else {
        **dict(f), "price": f["price"], "executed_at": iso(f["executed_ms"]),
        "observation_time": iso(f["observation_time_ms"]),
        "cash_after": dollars(f["cash_after_micros"]),
    }
    return o


def execute(conn: sqlite3.Connection, account_id: str, symbol: str, action: str, quantity: int | None,
            request_id: str, submitted_ms: int | None = None, origin: str = "manual") -> dict:
    """The single execution function for manual and agent requests.

    Returns {"action", "order": order dict or None, "duplicate": bool, "note": str}.
    Raises PaperError for a request that is rejected before an order exists
    (unknown action, missing request_id, unknown account).
    """
    ensure_schema(conn)
    action = (action or "").lower()
    if action not in ORDER_ACTIONS | NO_ORDER_ACTIONS:
        raise PaperError(f"action must be one of buy, sell, close, hold, wait (got {action!r})")
    if not request_id or len(request_id) > 100:
        raise PaperError("a request_id (1-100 characters) is required so a retry cannot repeat the order")
    if conn.execute("SELECT 1 FROM paper_accounts WHERE id = ?", (account_id,)).fetchone() is None:
        raise PaperError(f"no paper account {account_id!r}")

    held = _position(conn, account_id, symbol)
    if action in NO_ORDER_ACTIONS:
        if action == "hold":
            note = f"hold: keeping {held} {symbol} shares; no order" if held else "hold: no open position; no order"
        else:
            note = f"wait: staying in cash; no order" + (f" (note: {held} shares are held)" if held else "")
        return {"action": action, "order": None, "duplicate": False, "note": note}

    existing = conn.execute("SELECT id FROM paper_orders WHERE request_id = ?", (request_id,)).fetchone()
    if existing:                                            # retry of the same request: no new order or fill
        return {"action": action, "order": _order(conn, existing[0]), "duplicate": True,
                "note": "duplicate request_id: returning the original order"}

    submitted_ms = submitted_ms if submitted_ms is not None else now_ms()
    reason = None
    if action in ("buy", "sell"):
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            reason = f"quantity must be a positive whole number of shares (got {quantity!r})"
        elif action == "sell":
            available = held - _reserved_for_sale(conn, account_id, symbol)
            if quantity > available:
                reason = f"cannot sell {quantity} {symbol}: only {available} shares available"
    else:                                                   # close
        quantity = held - _reserved_for_sale(conn, account_id, symbol)
        if quantity <= 0:
            reason = f"no open {symbol} position to close"

    qty_value = quantity if isinstance(quantity, int) and not isinstance(quantity, bool) else 0
    with conn:
        order_id = conn.execute(
            """INSERT INTO paper_orders (request_id, account_id, symbol, side, quantity, submitted_ms, status,
                   reason, decided_ms, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (request_id, account_id, symbol, action, qty_value, submitted_ms,
             "rejected" if reason else "pending", reason, submitted_ms if reason else None, origin)).lastrowid
    note = f"rejected: {reason}" if reason else "accepted: pending until the next observed price"
    return {"action": action, "order": _order(conn, order_id), "duplicate": False, "note": note}


def process_observation(conn: sqlite3.Connection, observation: dict) -> list[dict]:
    """Fill pending orders for the observation's symbol that were submitted before it arrived.

    `observation` needs id, ticker (or symbol), price, source_time_ms, first_received_ms.
    Each order is filled (or rejected after the recheck) in its own transaction, oldest first.
    """
    ensure_schema(conn)
    symbol = observation.get("ticker") or observation.get("symbol")
    received = observation["first_received_ms"]
    price_micros = to_micros(observation["price"])
    # A new observation may complete the previous candle: value open positions first (P&L history),
    # so the valuation row comes before any fill this observation triggers.
    value_open_positions(conn, symbol, received)
    pending = [r[0] for r in conn.execute(
        "SELECT id FROM paper_orders WHERE symbol = ? AND status = 'pending' AND submitted_ms < ? "
        "ORDER BY submitted_ms, id", (symbol, received))]
    results = []
    for order_id in pending:
        with conn:   # one transaction: recheck, fill, order status, cash and position
            o = conn.execute("SELECT account_id, side, quantity, status FROM paper_orders WHERE id = ?",
                             (order_id,)).fetchone()
            if o is None or o[3] != "pending":
                continue
            account_id, side, qty = o[0], o[1], o[2]
            cash = conn.execute("SELECT cash_micros FROM paper_accounts WHERE id = ?", (account_id,)).fetchone()[0]
            held = _position(conn, account_id, symbol)
            if side == "close":
                qty = held
            cost = qty * price_micros
            reason = None
            if side == "buy" and cost > cash:
                reason = (f"insufficient cash at fill: {qty} x {dollars(price_micros):.4f} = "
                          f"{dollars(cost):.2f} > {dollars(cash):.2f}")
            elif side in ("sell", "close") and (qty <= 0 or qty > held):
                reason = f"insufficient shares at fill: need {qty}, hold {held}"
            if reason:
                conn.execute("UPDATE paper_orders SET status = 'rejected', reason = ?, decided_ms = ? WHERE id = ?",
                             (reason, received, order_id))
                results.append(_order(conn, order_id))
                continue
            new_cash = cash - cost if side == "buy" else cash + cost
            new_pos = held + qty if side == "buy" else held - qty
            conn.execute("UPDATE paper_accounts SET cash_micros = ? WHERE id = ?", (new_cash, account_id))
            conn.execute("INSERT INTO paper_positions (account_id, symbol, quantity) VALUES (?, ?, ?) "
                         "ON CONFLICT (account_id, symbol) DO UPDATE SET quantity = excluded.quantity",
                         (account_id, symbol, new_pos))
            conn.execute("UPDATE paper_orders SET status = 'filled', quantity = ?, decided_ms = ? WHERE id = ?",
                         (qty, received, order_id))
            conn.execute(
                """INSERT INTO paper_fills (order_id, account_id, symbol, side, quantity, price, price_micros,
                       observation_id, observation_time_ms, executed_ms, cash_after_micros, position_after)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (order_id, account_id, symbol, side, qty, observation["price"], price_micros, observation["id"],
                 observation["source_time_ms"], received, new_cash, new_pos))
            fill_id = conn.execute("SELECT id FROM paper_fills WHERE order_id = ?", (order_id,)).fetchone()[0]
            record_fill(conn, fill_id)     # P&L row and cost basis in the same transaction as the fill
        results.append(_order(conn, order_id))
    return results


def fill_paper_orders(conn: sqlite3.Connection, observation_id: int) -> list[dict]:
    """Look up a stored observation and let it fill pending orders (used by the live and test feeds)."""
    row = conn.execute("SELECT id, ticker, price, source_time_ms, first_received_ms FROM observations WHERE id = ?",
                       (observation_id,)).fetchone()
    if row is None:
        return []
    obs = dict(zip(("id", "ticker", "price", "source_time_ms", "first_received_ms"), tuple(row)))
    return process_observation(conn, obs)


def account_state(conn: sqlite3.Connection, account_id: str) -> dict:
    """Cash, positions, orders (newest first) with their fills."""
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    a = conn.execute("SELECT * FROM paper_accounts WHERE id = ?", (account_id,)).fetchone()
    if a is None:
        raise PaperError(f"no paper account {account_id!r}")
    positions = {r["symbol"]: r["quantity"] for r in conn.execute(
        "SELECT symbol, quantity FROM paper_positions WHERE account_id = ? ORDER BY symbol", (account_id,))}
    orders = [_order(conn, r["id"]) for r in conn.execute(
        "SELECT id FROM paper_orders WHERE account_id = ? ORDER BY id DESC", (account_id,))]
    fills = conn.execute("SELECT COUNT(*) FROM paper_fills WHERE account_id = ?", (account_id,)).fetchone()[0]
    return {"account": account_id, "starting_cash": dollars(a["starting_cash_micros"]),
            "cash": dollars(a["cash_micros"]), "positions": positions, "orders": orders, "fill_count": fills}
