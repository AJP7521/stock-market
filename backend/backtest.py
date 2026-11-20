"""Backtest of the SMA crossover strategy on stored candles (Prompt 17). SIMULATED: no orders reach any market.

Strategy (rule-based, no model)
-------------------------------
* Long only, one symbol, one timeframe. SMA(short) and SMA(long) of completed closes, the same averages and
  crossover rule as the chart (backend/indicators.py: side "above"/"below", a tie keeps the last side).
* An upward crossover at a candle's close while flat -> buy. A downward crossover while holding -> sell the whole
  position. Anything else -> no order.

Only information available at each decision time
------------------------------------------------
The engine walks the completed candles in time order. At candle i it is handed `history = candles[:i+1]` and
nothing later: the SMAs come from the last `short`/`long` closes of that history, and the crossover compares them
with the side carried from the previous step. The decision is made at the candle's close and can only be filled at
a later price. The unit tests check that changing or removing every later candle leaves earlier decisions unchanged,
and that the signals equal the chart's crossovers (sma_crossover on the full series).

Simulated execution and accounting (assumptions)
------------------------------------------------
* Fill: at the OPEN of the next completed candle. A signal on the last candle stays "not_filled" (no later price).
  A signal on a session's last candle fills at the next session's first open, so overnight gaps are included.
* Size: buy as many whole shares as the cash allows at the fill price (fully invested); sell the whole position.
  Cash is checked at the fill; if it does not buy one share the order is "rejected".
* Zero fees and commissions, no slippage, no bid/ask spread, no partial fills, no dividends or splits, no
  borrowing. Orders are not checked against market hours (test data has none).
* P&L: weighted average cost, the same function as the paper account (pnl.apply_fill). Realized P&L comes from
  sales; unrealized = shares x close - cost basis. An open position at the end is NOT force-closed: it is valued at
  the last close and reported as unrealized.
* Equity is valued at every candle's close: cash + shares x close. Max drawdown is the largest fall from a running
  equity peak (starting cash counts as the first peak).
* Buy and hold, for comparison: whole shares bought at the first candle's open with the same starting cash,
  valued at the last close, zero fees.
* Candles still forming at the as-of time are excluded and counted.
* Money is integer micro-dollars (1e-6 USD), as in backend/paper.py.

Storage (one transaction per run)
---------------------------------
  backtest_runs     one row per run: settings, data source and range, counts, summary
  backtest_fills    one row per order: signal candle, side, status, fill time/price, balances after
  backtest_equity   one row per completed candle: close, cash, shares, equity, realized, unrealized
"""

import sqlite3
from fractions import Fraction

from backend.candles import iso_ms
from backend.indicators import _exact, validate_windows
from backend.paper import MICROS, to_micros
from backend.pnl import apply_fill
from backend.recorder import now_ms
from backend.series import candle_series
from backend.timeframes import to_ms

STRATEGY = "sma_crossover_long_only"
FILL_RULE = "next candle open; whole shares, fully invested; zero fees, no slippage or spread"

SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    id                      INTEGER PRIMARY KEY,
    created_ms              INTEGER NOT NULL,
    symbol                  TEXT NOT NULL,
    timeframe               TEXT NOT NULL,
    short                   INTEGER NOT NULL,
    long                    INTEGER NOT NULL,
    strategy                TEXT NOT NULL,
    fill_rule               TEXT NOT NULL,
    data_source             TEXT NOT NULL,     -- e.g. "TEST data (labelled fixture, SQLite)"
    data_labels             TEXT NOT NULL,     -- comma list of observation sources: test, yahoo, synthetic
    as_of_ms                INTEGER NOT NULL,  -- data known at this time was used
    first_candle            TEXT NOT NULL,
    last_candle             TEXT NOT NULL,
    candle_count            INTEGER NOT NULL,  -- completed candles used
    excluded_count          INTEGER NOT NULL,  -- candles still forming, not used
    starting_cash_micros    INTEGER NOT NULL,
    final_cash_micros       INTEGER NOT NULL,
    final_shares            INTEGER NOT NULL,
    final_equity_micros     INTEGER NOT NULL,
    realized_micros         INTEGER NOT NULL,
    unrealized_micros       INTEGER NOT NULL,
    total_return_pct        REAL NOT NULL,
    buy_hold_equity_micros  INTEGER NOT NULL,
    buy_hold_return_pct     REAL NOT NULL,
    max_drawdown_micros     INTEGER NOT NULL,
    max_drawdown_pct        REAL NOT NULL,
    round_trips             INTEGER NOT NULL,  -- closed (buy then sell)
    wins                    INTEGER NOT NULL,
    exposure_candles        INTEGER NOT NULL   -- closes at which shares were held
);
CREATE TABLE IF NOT EXISTS backtest_fills (
    id                     INTEGER PRIMARY KEY,
    run_id                 INTEGER NOT NULL REFERENCES backtest_runs(id),
    seq                    INTEGER NOT NULL,
    signal_candle          TEXT NOT NULL,      -- candle whose close produced the signal
    signal                 TEXT NOT NULL CHECK (signal IN ('up', 'down')),
    side                   TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    status                 TEXT NOT NULL CHECK (status IN ('filled', 'rejected', 'not_filled')),
    reason                 TEXT,
    quantity               INTEGER,
    fill_candle            TEXT,               -- candle whose open supplied the price
    price_micros           INTEGER,
    cash_after_micros      INTEGER,
    position_after         INTEGER,
    realized_after_micros  INTEGER,
    UNIQUE (run_id, seq)
);
CREATE TABLE IF NOT EXISTS backtest_equity (
    run_id             INTEGER NOT NULL REFERENCES backtest_runs(id),
    candle_ts          TEXT NOT NULL,
    close_micros       INTEGER NOT NULL,
    cash_micros        INTEGER NOT NULL,
    shares             INTEGER NOT NULL,
    equity_micros      INTEGER NOT NULL,
    realized_micros    INTEGER NOT NULL,
    unrealized_micros  INTEGER NOT NULL,
    PRIMARY KEY (run_id, candle_ts)
);
"""


class BacktestError(ValueError):
    pass


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _price(value) -> int:
    return round(_exact(value) * MICROS)


def _pct(numerator: int, denominator: int) -> float:
    return round(float(Fraction(numerator * 100, denominator)), 4) if denominator else 0.0


# ---------------------------------------------------------------- decisions (only the history so far)

def decide(history: list[dict], short: int, long: int, prev_side: str | None) -> tuple[str | None, str | None]:
    """Crossover at the last candle of `history` (all completed). Returns (side, signal).

    Uses only `history`: SMA(n) = mean of its last n closes; same tie rule as indicators.sma_crossover.
    """
    if len(history) < long:
        return prev_side, None
    closes = [_exact(c["close"]) for c in history[-long:]]
    s, l = sum(closes[-short:]) / short, sum(closes) / long
    side = "above" if s > l else "below" if s < l else prev_side
    signal = None
    if prev_side is not None and side != prev_side:
        signal = "up" if side == "above" else "down"
    return side, signal


def simulate(candles: list[dict], short: int, long: int, starting_cash_micros: int) -> dict:
    """Run the strategy over completed candles (six fields each). Pure: no database access."""
    cash, shares, basis, realized = starting_cash_micros, 0, 0, 0
    side, pending = None, None
    orders, equity = [], []
    for i, c in enumerate(candles):
        if pending is not None:                       # fill the previous close's order at this open
            price = _price(c["open"])
            order = pending
            pending = None
            held_before = shares
            if order["side"] == "buy":
                qty = cash // price
                if qty == 0:
                    order.update(status="rejected", reason=f"cash {cash / MICROS:.2f} does not buy one share at "
                                 f"{price / MICROS:g}", fill_candle=c["timestamp"])
                    orders.append(order)
                    qty = None
                else:
                    cash -= qty * price
                    shares += qty
            else:
                qty = shares
                cash += qty * price
                shares = 0
            if qty:
                basis, realized = apply_fill(basis, realized, held_before, order["side"], qty, price)
                order.update(status="filled", reason=None, quantity=qty, fill_candle=c["timestamp"],
                             price_micros=price, cash_after_micros=cash, position_after=shares,
                             realized_after_micros=realized)
                orders.append(order)
        close = _price(c["close"])
        equity.append({"candle_ts": c["timestamp"], "close_micros": close, "cash_micros": cash, "shares": shares,
                       "equity_micros": cash + shares * close, "realized_micros": realized,
                       "unrealized_micros": shares * close - basis})
        side, signal = decide(candles[:i + 1], short, long, side)  # the decision sees nothing after candle i
        if signal == "up" and shares == 0:
            pending = {"signal_candle": c["timestamp"], "signal": "up", "side": "buy"}
        elif signal == "down" and shares > 0:
            pending = {"signal_candle": c["timestamp"], "signal": "down", "side": "sell"}
    if pending is not None:
        orders.append({**pending, "status": "not_filled", "reason": "no later candle to fill at"})

    # Closed round trips: a filled buy and the filled sell that follows it (details: _trips, from stored rows).
    closed, entry = [], None
    for o in orders:
        if o["status"] == "filled" and o["side"] == "buy":
            entry = o
        elif o["status"] == "filled" and entry is not None:
            closed.append(o["price_micros"] > entry["price_micros"])
            entry = None

    peak, dd, dd_pct = starting_cash_micros, 0, 0.0
    for e in equity:
        peak = max(peak, e["equity_micros"])
        if peak - e["equity_micros"] > dd:
            dd, dd_pct = peak - e["equity_micros"], _pct(peak - e["equity_micros"], peak)
    first_open, last = _price(candles[0]["open"]), equity[-1]
    bh_shares = starting_cash_micros // first_open
    bh_equity = starting_cash_micros - bh_shares * first_open + bh_shares * last["close_micros"]
    summary = {
        "starting_cash_micros": starting_cash_micros, "final_cash_micros": cash, "final_shares": shares,
        "final_equity_micros": last["equity_micros"], "realized_micros": realized,
        "unrealized_micros": last["unrealized_micros"],
        "total_return_pct": _pct(last["equity_micros"] - starting_cash_micros, starting_cash_micros),
        "buy_hold_equity_micros": bh_equity,
        "buy_hold_return_pct": _pct(bh_equity - starting_cash_micros, starting_cash_micros),
        "max_drawdown_micros": dd, "max_drawdown_pct": dd_pct,
        "round_trips": len(closed), "wins": sum(closed),
        "exposure_candles": sum(1 for e in equity if e["shares"] > 0),
    }
    return {"orders": orders, "equity": equity, "summary": summary}


# ---------------------------------------------------------------- run + store

def run_backtest(conn: sqlite3.Connection, symbol: str, timeframe: str, short: int, long: int,
                 starting_cash: float = 1000, start: str | None = None, end: str | None = None,
                 as_of_ms: int | None = None) -> dict:
    """Backtest stored candles for `symbol` (start <= candle < end, as known at as_of_ms) and store the run."""
    validate_windows(short, long)
    starting = to_micros(starting_cash)
    if starting <= 0:
        raise BacktestError("starting cash must be positive")
    ensure_schema(conn)
    as_of_ms = as_of_ms if as_of_ms is not None else now_ms()
    series = candle_series(symbol, timeframe, as_of_ms, conn=conn)
    items = series["candles"]
    if start:
        items = [x for x in items if to_ms(x["candle"]["timestamp"]) >= to_ms(start)]
    if end:
        items = [x for x in items if to_ms(x["candle"]["timestamp"]) < to_ms(end)]
    candles = [x["candle"] for x in items if x["status"]["complete"]]
    excluded = len(items) - len(candles)
    if len(candles) <= long:
        raise BacktestError(f"{symbol} {timeframe}: {len(candles)} completed candles; the backtest needs more than "
                            f"the long window ({long})")
    result = simulate(candles, short, long, starting)
    s = result["summary"]
    with conn:
        run_id = conn.execute(
            f"""INSERT INTO backtest_runs (created_ms, symbol, timeframe, short, long, strategy, fill_rule,
                    data_source, data_labels, as_of_ms, first_candle, last_candle, candle_count, excluded_count,
                    {', '.join(s)}) VALUES ({', '.join('?' for _ in range(14 + len(s)))})""",
            (now_ms(), symbol, timeframe, short, long, STRATEGY, FILL_RULE, series["source"],
             ",".join(series["sources"]), as_of_ms, candles[0]["timestamp"], candles[-1]["timestamp"],
             len(candles), excluded, *s.values())).lastrowid
        for seq, o in enumerate(result["orders"], start=1):
            conn.execute(
                """INSERT INTO backtest_fills (run_id, seq, signal_candle, signal, side, status, reason, quantity,
                       fill_candle, price_micros, cash_after_micros, position_after, realized_after_micros)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, seq, o["signal_candle"], o["signal"], o["side"], o["status"], o.get("reason"),
                 o.get("quantity"), o.get("fill_candle"), o.get("price_micros"), o.get("cash_after_micros"),
                 o.get("position_after"), o.get("realized_after_micros")))
        conn.executemany(
            """INSERT INTO backtest_equity (run_id, candle_ts, close_micros, cash_micros, shares, equity_micros,
                   realized_micros, unrealized_micros) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(run_id, e["candle_ts"], e["close_micros"], e["cash_micros"], e["shares"], e["equity_micros"],
              e["realized_micros"], e["unrealized_micros"]) for e in result["equity"]])
    return get_run(conn, run_id)


# ---------------------------------------------------------------- read back (dollars for the API)

def _d(micros):
    return None if micros is None else micros / MICROS


def _usd(v: float) -> str:
    return f"-${-v:,.2f}" if v < 0 else f"${v:,.2f}"


def interpretation(run: dict) -> dict:
    """Plain statements of what this run establishes and what it leaves uncertain."""
    s = run["summary"]
    beat = s["total_return_pct"] - s["buy_hold_return_pct"]
    establishes = [
        f"On these {run['candle_count']} completed {run['timeframe']} candles of {run['symbol']} "
        f"({run['first_candle']} to {run['last_candle']}), the SMA({run['short']})/SMA({run['long']}) crossover rule "
        f"turned {_usd(s['starting_cash'])} into {_usd(s['final_equity'])} ({s['total_return_pct']:+.2f}%).",
        f"Buy and hold over the same candles: {s['buy_hold_return_pct']:+.2f}%; the rule was "
        f"{abs(beat):.2f} percentage points {'better' if beat > 0 else 'worse' if beat < 0 else 'equal'}.",
        f"{s['round_trips']} closed round trip(s), {s['wins']} profitable; largest fall from an equity peak "
        f"{s['max_drawdown_pct']:.2f}% ({_usd(s['max_drawdown'])}).",
        "Each decision used only candles completed by its time and was filled at a later price (next candle's open), "
        "so the result has no look-ahead.",
    ]
    uncertain = []
    if s["round_trips"] < 30:
        uncertain.append(f"Only {s['round_trips']} closed round trip(s): far too few to tell skill from chance.")
    uncertain.append("One symbol, one period and one timeframe: other periods (trends, ranges, volatility) can give "
                     "the opposite result.")
    uncertain.append(f"The windows {run['short']}/{run['long']} were chosen, not tested on data held back; picking "
                     "the best windows after seeing results overstates performance.")
    uncertain.append("Execution is idealised: zero fees, no slippage or bid/ask spread, no partial fills, always "
                     "filled at the next open. Real costs would lower the result, more so with many trades.")
    if s["final_shares"]:
        uncertain.append(f"The last position is still open: {_usd(s['unrealized'])} of the result is unrealized and "
                         "depends on the final close.")
    labels = set(run["data_labels"].split(",")) if run["data_labels"] else set()
    if labels & {"test", "synthetic"}:
        uncertain.append("The data is labelled TEST/SYNTHETIC, not a market record: the run demonstrates the "
                         "mechanics only and says nothing about real markets.")
    uncertain.append("Past simulated results do not predict future results. This is a simulation, not advice.")
    return {"establishes": establishes, "uncertain": uncertain}


def _run_row(r: sqlite3.Row) -> dict:
    run = {k: r[k] for k in ("id", "symbol", "timeframe", "short", "long", "strategy", "fill_rule", "data_source",
                             "data_labels", "first_candle", "last_candle", "candle_count", "excluded_count")}
    run["created"] = iso_ms(r["created_ms"])
    run["as_of"] = iso_ms(r["as_of_ms"])
    summary = {}
    for k in r.keys():
        if k.endswith("_micros") and k not in ("created_ms", "as_of_ms"):
            summary[k[:-len("_micros")]] = _d(r[k])
    for k in ("final_shares", "total_return_pct", "buy_hold_return_pct", "max_drawdown_pct", "round_trips", "wins",
              "exposure_candles"):
        summary[k] = r[k]
    run["summary"] = summary
    return run


def get_run(conn: sqlite3.Connection, run_id: int) -> dict:
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM backtest_runs WHERE id = ?", (run_id,)).fetchone()
    if r is None:
        raise KeyError(run_id)
    run = _run_row(r)
    run["orders"] = [{
        "seq": o["seq"], "signal_candle": o["signal_candle"], "signal": o["signal"], "side": o["side"],
        "status": o["status"], "reason": o["reason"], "quantity": o["quantity"], "fill_candle": o["fill_candle"],
        "price": _d(o["price_micros"]), "cash_after": _d(o["cash_after_micros"]), "position_after": o["position_after"],
        "realized_after": _d(o["realized_after_micros"]),
    } for o in conn.execute("SELECT * FROM backtest_fills WHERE run_id = ? ORDER BY seq", (run_id,))]
    run["equity"] = [{
        "time": e["candle_ts"], "close": _d(e["close_micros"]), "cash": _d(e["cash_micros"]), "shares": e["shares"],
        "equity": _d(e["equity_micros"]), "realized": _d(e["realized_micros"]),
        "unrealized": _d(e["unrealized_micros"]),
    } for e in conn.execute("SELECT * FROM backtest_equity WHERE run_id = ? ORDER BY candle_ts", (run_id,))]
    run["trips"] = _trips(run["orders"], run["equity"])
    run["interpretation"] = interpretation(run)
    return run


def _trips(orders: list[dict], equity: list[dict]) -> list[dict]:
    trips, entry = [], None
    for o in orders:
        if o["status"] != "filled":
            continue
        if o["side"] == "buy":
            entry = o
        elif entry is not None:
            trips.append({"entry_candle": entry["fill_candle"], "entry_price": entry["price"],
                          "exit_candle": o["fill_candle"], "exit_price": o["price"], "shares": o["quantity"],
                          "pnl": round(o["quantity"] * (o["price"] - entry["price"]), 6),
                          "return_pct": round((o["price"] / entry["price"] - 1) * 100, 4), "status": "closed"})
            entry = None
    if entry is not None and equity and equity[-1]["shares"]:
        last = equity[-1]
        trips.append({"entry_candle": entry["fill_candle"], "entry_price": entry["price"], "exit_candle": None,
                      "exit_price": last["close"], "shares": last["shares"],
                      "pnl": round(last["shares"] * (last["close"] - entry["price"]), 6),
                      "return_pct": round((last["close"] / entry["price"] - 1) * 100, 4),
                      "status": "open (valued at last close)"})
    return trips


def list_runs(conn: sqlite3.Connection, symbol: str | None = None, limit: int = 20) -> list[dict]:
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM backtest_runs" + (" WHERE symbol = ?" if symbol else "") + " ORDER BY id DESC LIMIT ?"
    return [_run_row(r) for r in conn.execute(sql, (symbol, limit) if symbol else (limit,))]
