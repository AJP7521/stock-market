"""Candle series for one symbol and timeframe, shared by the HTTP API, WebSocket and decision snapshots."""

from backend.candles import minute_candles
from backend.history import load_candles
from backend.recorder import connect, db_path, now_ms
from backend.timeframes import aggregate

SAMPLE_SYMBOL = "SAMPLE"


def data_sources(conn, symbol: str) -> set[str]:
    """Labels of the stored observations for a symbol ('yahoo', 'test')."""
    return {r[0] for r in conn.execute("SELECT DISTINCT source FROM observations WHERE ticker = ?", (symbol,))}


def candle_series(symbol: str, timeframe: str, as_of_ms: int | None, conn=None) -> dict:
    """One-minute data for the symbol, aggregated to the timeframe.

    SAMPLE reads the synthetic CSV; any other symbol uses observations stored in SQLite,
    as of `as_of_ms` (default: now, so the latest candle can show as still forming).
    Returns {"source", "sources", "timeframe", "candles", "excluded_minutes"}.
    """
    if symbol == SAMPLE_SYMBOL:
        minutes, source, sources = load_candles(), "synthetic CSV (prompts/data/sample-candles.csv)", {"synthetic"}
    else:
        if as_of_ms is None:
            as_of_ms = now_ms()
        own = conn is None
        conn = conn or connect(db_path())
        try:
            live = minute_candles(conn, symbol, as_of_ms)
            sources = data_sources(conn, symbol)
        finally:
            if own:
                conn.close()
        minutes = [{**item["candle"], "complete": item["status"]["complete"]} for item in live]
        source = ("SQLite (no observations recorded yet)" if not sources
                  else "TEST data (labelled fixture, SQLite)" if sources == {"test"}
                  else "Yahoo Finance + TEST data (SQLite)" if "test" in sources
                  else "Yahoo Finance (recorded in SQLite)")
    result = aggregate(minutes, timeframe, as_of_ms)
    return {"source": source, "sources": sorted(sources), **result}
