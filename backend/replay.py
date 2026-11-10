"""Replay a recorded session from SQLite, read-only.

The selected symbol's observations (source time in [start, end)) are fed to the
existing candle calculation (candles.build_minute_candles -> timeframes.aggregate)
one arrival at a time, in the order the backend originally received them
(receipts.seq). Each step is computed "as of" that arrival's receipt time, so the
same availability rules as live use apply:
  * a late observation (earlier source time, later arrival) changes its own
    candle at the step where it arrived, not earlier
  * an identified revision replaces the original from the step where it arrived
  * exact repeats appear as steps but do not change any candle

The database is opened with SQLite's read-only URI mode (mode=ro), so replay
cannot modify the recorded session.

Volume in the first replayed minute is unknown unless the selection includes the
preceding minute, the same rule as live (candles.py).
"""

import sqlite3
from pathlib import Path

from backend.candles import build_minute_candles, iso_ms
from backend.indicators import sma_crossover
from backend.timeframes import MINUTE_MS, aggregate, to_ms


def connect_readonly(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no recorded database at {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def sessions(conn: sqlite3.Connection) -> list[dict]:
    """Recorded symbols with their source-time span and counts, for the selection UI."""
    return [dict(r) for r in conn.execute(
        """SELECT o.ticker AS symbol, MIN(o.source_time_ms) AS first_ms, MAX(o.source_time_ms) AS last_ms,
                  COUNT(DISTINCT o.id) AS observations, COUNT(r.seq) AS receipts,
                  GROUP_CONCAT(DISTINCT o.source) AS sources
           FROM observations o JOIN receipts r ON r.observation_id = o.id
           GROUP BY o.ticker ORDER BY o.ticker""")]


def _arrivals(conn: sqlite3.Connection, symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT r.seq, r.received_at_ms, r.is_repeat,
                  o.id, o.source_time_ms, o.price, o.day_volume, o.first_received_ms, o.revises_id, o.source
           FROM receipts r JOIN observations o ON o.id = r.observation_id
           WHERE o.ticker = ? AND o.source_time_ms >= ? AND o.source_time_ms < ?
           ORDER BY r.seq""", (symbol, start_ms, end_ms))]


def _containing(candles: list[dict], timeframe: str, ms: int) -> dict | None:
    for item in reversed(candles):
        start = to_ms(item["candle"]["timestamp"])
        end = start + MINUTE_MS if timeframe == "1m" else to_ms(item["status"]["interval_end"])
        if start <= ms < end:
            return item
    return None


def _series(observations: list[dict], timeframe: str, as_of_ms: int) -> list[dict]:
    minutes = [{**c["candle"], "complete": c["status"]["complete"]}
               for c in build_minute_candles(observations, as_of_ms)]
    return aggregate(minutes, timeframe, as_of_ms)["candles"]


def _sma(series: list[dict], sma: tuple[int, int] | None) -> dict | None:
    if sma is None:
        return None
    return sma_crossover([{**c["candle"], "complete": c["status"]["complete"]} for c in series], *sma)


def _candles(series: list[dict]) -> list[dict]:
    return [{**c["candle"], "complete": c["status"]["complete"]} for c in series]


def replay(conn: sqlite3.Connection, symbol: str, start_ms: int, end_ms: int, timeframe: str = "1m",
           sma: tuple[int, int] | None = None, indicators=None) -> dict:
    """Every arrival in the selection as a replay step, plus the final candles.

    With `sma=(short, long)`, each step also carries the SMA points and crossovers
    computed from only the candles available at that step. `indicators`, if given,
    is a function candles -> configured indicator values, applied the same way.
    """
    arrivals = _arrivals(conn, symbol, start_ms, end_ms)
    seen: dict[int, dict] = {}           # distinct observations available so far
    steps = []
    max_source = None
    for a in arrivals:
        obs = {k: a[k] for k in ("id", "source_time_ms", "price", "day_volume", "first_received_ms", "revises_id")}
        seen.setdefault(obs["id"], obs)
        as_of = a["received_at_ms"]
        late = max_source is not None and a["source_time_ms"] < max_source
        max_source = a["source_time_ms"] if max_source is None else max(max_source, a["source_time_ms"])
        series = _series(list(seen.values()), timeframe, as_of)
        item = _containing(series, timeframe, a["source_time_ms"])
        steps.append({
            "step": len(steps) + 1,
            "replay_time": iso_ms(as_of),
            "observation": {
                "id": a["id"], "seq": a["seq"], "source_time": iso_ms(a["source_time_ms"]),
                "received_at": iso_ms(a["received_at_ms"]), "price": a["price"], "day_volume": a["day_volume"],
                "is_repeat": bool(a["is_repeat"]), "late": late, "revises_id": a["revises_id"], "source": a["source"],
            },
            "candle": item["candle"] if item else None,
            "status": item["status"] if item else None,
            **({"sma": _sma(series, sma)} if sma else {}),
            **({"indicators": indicators(_candles(series))} if indicators else {}),
        })
    final_as_of = arrivals[-1]["received_at_ms"] if arrivals else None
    final = _series(list(seen.values()), timeframe, final_as_of) if arrivals else []
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "start": iso_ms(start_ms),
        "end": iso_ms(end_ms),
        "observation_count": len(seen),
        "receipt_count": len(arrivals),
        "empty": not arrivals,
        "steps": steps,
        "final": {"candles": [c["candle"] for c in final], "status": [c["status"] for c in final],
                  **({"sma": _sma(final, sma)} if sma else {}),
                  **({"indicators": indicators(_candles(final))} if indicators else {})},
    }
