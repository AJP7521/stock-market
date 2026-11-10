"""Form one-minute candles from stored observations (same base resolution as the CSV).

Each candle has exactly the six CSV fields:
    timestamp  start of the minute, ISO 8601 UTC ("2026-11-09T15:00:00Z")
    open, high, low, close   prices of the minute's observations, ordered by source time
    volume     shares traded during the minute, or None when it cannot be determined

Status is kept separately from the candle:
    complete            True once the as-of time has reached the end of the minute
    observation_count   observations used
    volume_known        False when volume is None
    volume_note         why volume is unknown
    last_source_time / last_received_time   newest inputs that shaped the candle

Aggregation rules
-----------------
1. Availability: an observation counts only if it had been received by the as-of
   time (first_received_ms <= as_of) and its source time is not after as_of. Live
   use passes "now"; replay passes the displayed time.
2. Corrections: an observation identified as a revision (revises_id set) replaces
   the row it revises, provided the revision is available at the as-of time.
   Distinct messages that share a source time are both used (see recorder.py).
3. Ordering within a minute: by source time, then by first arrival (observation id).
   A late observation is therefore placed by its source time, not by its arrival.
4. Prices: open = first, high = max, low = min, close = last.
5. Volume: Yahoo's day_volume is a running session total. Minute volume =
   the last running total in the minute minus the baseline, where the baseline is
   the last running total *before* the minute in the same trading session
   (America/New_York calendar date). Volume is None (unknown) when:
     - there is no earlier observation in the session (no baseline)
     - an observation in the minute lacks day_volume
     - the running total decreases (a session reset); the baseline then restarts
   A known zero (the running total did not change) stays 0, which is different from None.
6. Candles are recomputed from the store, so a late or corrected observation
   changes the affected candle the next time it is calculated.
"""

import argparse
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MINUTE_MS = 60_000
EXCHANGE_TZ = ZoneInfo("America/New_York")


def iso_minute(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def session_date(ms: int) -> str:
    """Trading-session key: the exchange-local calendar date of the observation."""
    return datetime.fromtimestamp(ms / 1000, tz=EXCHANGE_TZ).date().isoformat()


def available(observations: list[dict], as_of_ms: int | None) -> list[dict]:
    """Apply rules 1 and 2: keep what was known at as_of, with revisions replacing originals."""
    known = [
        o for o in observations
        if as_of_ms is None or (o["first_received_ms"] <= as_of_ms and o["source_time_ms"] <= as_of_ms)
    ]
    revised = {o["revises_id"] for o in known if o.get("revises_id") is not None}
    return [o for o in known if o["id"] not in revised]


def build_minute_candles(observations: list[dict], as_of_ms: int | None = None) -> list[dict]:
    """Group one ticker's observations into one-minute candles.

    `observations` are dicts with id, source_time_ms, price, day_volume,
    first_received_ms and revises_id (as stored by backend.recorder).
    Returns [{"candle": {six fields}, "status": {...}}] ordered by minute.
    """
    rows = sorted(available(observations, as_of_ms), key=lambda o: (o["source_time_ms"], o["id"]))

    candles: list[dict] = []
    current = None
    prev_cum = None          # last running total seen, in source-time order
    prev_session = None

    for o in rows:
        minute_start = o["source_time_ms"] - o["source_time_ms"] % MINUTE_MS
        session = session_date(o["source_time_ms"])
        if session != prev_session:   # new trading session: no usable baseline
            prev_cum = None
            prev_session = session

        if current is None or current["start"] != minute_start:
            if current:
                candles.append(_finish(current, as_of_ms))
            current = {
                "start": minute_start, "prices": [], "baseline": prev_cum, "last_cum": None,
                "note": None if prev_cum is not None else "no earlier running total in this session",
                "last_source": None, "last_received": None,
            }

        current["prices"].append(o["price"])
        current["last_source"] = o["source_time_ms"]
        current["last_received"] = max(current["last_received"] or 0, o["first_received_ms"])

        cum = o.get("day_volume")
        if cum is None:
            current["note"] = current["note"] or "an observation has no running volume"
        elif prev_cum is not None and cum < prev_cum:
            current["note"] = "running volume decreased (session total reset)"
            prev_cum = cum
        else:
            prev_cum = cum
        if cum is not None:
            current["last_cum"] = cum

    if current:
        candles.append(_finish(current, as_of_ms))
    return candles


def _finish(c: dict, as_of_ms: int | None) -> dict:
    prices = c["prices"]
    volume = None
    if c["note"] is None and c["baseline"] is not None and c["last_cum"] is not None:
        volume = c["last_cum"] - c["baseline"]
    candle = {
        "timestamp": iso_minute(c["start"]),
        "open": prices[0],
        "high": max(prices),
        "low": min(prices),
        "close": prices[-1],
        "volume": volume,
    }
    status = {
        "complete": as_of_ms is None or as_of_ms >= c["start"] + MINUTE_MS,
        "observation_count": len(prices),
        "volume_known": volume is not None,
        "volume_note": c["note"] if volume is None else None,
        "last_source_time": iso_ms(c["last_source"]),
        "last_received_time": iso_ms(c["last_received"]),
    }
    return {"candle": candle, "status": status}


def load_observations(conn: sqlite3.Connection, ticker: str) -> list[dict]:
    """All stored observations for one ticker (the as-of filter is applied when building)."""
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        """SELECT id, source_time_ms, price, day_volume, first_received_ms, revises_id
           FROM observations WHERE ticker = ? ORDER BY source_time_ms, id""", (ticker,))]


def minute_candles(conn: sqlite3.Connection, ticker: str, as_of_ms: int | None = None) -> list[dict]:
    return build_minute_candles(load_observations(conn, ticker), as_of_ms)


def main() -> None:
    from backend.recorder import connect, db_path

    parser = argparse.ArgumentParser(description="Print one-minute candles built from stored observations")
    parser.add_argument("ticker", nargs="?", default="AAPL")
    parser.add_argument("--db")
    parser.add_argument("--as-of", help="ISO UTC time, e.g. 2026-11-10T19:18:30Z (default: now)")
    args = parser.parse_args()
    as_of = (datetime.fromisoformat(args.as_of.replace("Z", "+00:00")) if args.as_of
             else datetime.now(timezone.utc))
    with connect(db_path(args.db)) as conn:
        for item in minute_candles(conn, args.ticker, round(as_of.timestamp() * 1000)):
            c, s = item["candle"], item["status"]
            print(f"{c['timestamp']}  O {c['open']}  H {c['high']}  L {c['low']}  C {c['close']}  "
                  f"V {c['volume']}  | complete={s['complete']} n={s['observation_count']}"
                  + (f" volume unknown: {s['volume_note']}" if not s["volume_known"] else ""))


if __name__ == "__main__":
    main()
