"""Labelled TEST fixtures written to a separate SQLite file (never the live database by default).

    python3 -m backend.fixtures sma --db backend/data/test-sma.sqlite

    python3 -m backend.fixtures range --db backend/data/test-range.sqlite

`sma` writes symbol TESTSMA: seven one-minute candles on 2026-11-09 from 15:00 UTC
with closes 13, 10, 7, 13, 19, 7, 1 (one observation per minute, so open = high =
low = close), then an eighth observation at 15:07:10 with price 30. That last minute
is still forming at the end of the fixture; if it were counted, it would create an
upward SMA(2)/SMA(3) crossover.

`range` writes symbol TESTRANGE: three one-minute candles on 2026-11-09 from 15:00 UTC
with (high, low) = (12, 9), (15, 10), (13, 11), each built from four observations.
Rolling range with window 2 at the third candle = 15 - 10 = 5; window 3 = 15 - 9 = 6.

Every row has source = 'test'. Each fixture replaces its database file.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from backend.recorder import PROJECT_ROOT, connect, record

SMA_SYMBOL = "TESTSMA"
SMA_CLOSES = [13, 10, 7, 13, 19, 7, 1]
SMA_FORMING_PRICE = 30
DEFAULT_SMA_DB = PROJECT_ROOT / "backend" / "data" / "test-sma.sqlite"

RANGE_SYMBOL = "TESTRANGE"
RANGE_MINUTES = [  # prices in arrival order -> open, high, low, close
    ("15:00", [10, 12, 9, 11]),    # high 12, low 9
    ("15:01", [11, 15, 10, 14]),   # high 15, low 10
    ("15:02", [12, 13, 11, 12]),   # high 13, low 11
]
DEFAULT_RANGE_DB = PROJECT_ROOT / "backend" / "data" / "test-range.sqlite"


def _ms(hms: str) -> int:
    return round(datetime.fromisoformat(f"2026-11-09T{hms}+00:00").timestamp() * 1000)


def write_sma_fixture(path: Path) -> int:
    """(Re)create the SMA fixture in `path`. Returns the number of observations written."""
    path = Path(path)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    conn = connect(path)
    volume = 10_000
    rows = [(f"15:0{i}:30", close) for i, close in enumerate(SMA_CLOSES)] + [("15:07:10", SMA_FORMING_PRICE)]
    for hms, price in rows:
        volume += 100
        t = _ms(hms)
        record(conn, {"id": SMA_SYMBOL, "price": price, "time": str(t), "day_volume": str(volume)},
               t + 300, source="test")
    conn.close()
    return len(rows)


def write_range_fixture(path: Path) -> int:
    """(Re)create the rolling-range fixture in `path`. Returns the number of observations written."""
    path = Path(path)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    conn = connect(path)
    volume, n = 20_000, 0
    for minute, prices in RANGE_MINUTES:
        for second, price in zip((5, 20, 35, 50), prices):
            volume += 50
            t = _ms(f"{minute}:{second:02d}")
            record(conn, {"id": RANGE_SYMBOL, "price": price, "time": str(t), "day_volume": str(volume)},
                   t + 300, source="test")
            n += 1
    conn.close()
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="Write labelled test fixtures to a separate SQLite file")
    parser.add_argument("fixture", choices=["sma", "range"])
    parser.add_argument("--db", help="target SQLite file (default backend/data/test-<fixture>.sqlite)")
    args = parser.parse_args()
    if args.fixture == "sma":
        db, symbol = args.db or DEFAULT_SMA_DB, SMA_SYMBOL
        n = write_sma_fixture(Path(db))
    else:
        db, symbol = args.db or DEFAULT_RANGE_DB, RANGE_SYMBOL
        n = write_range_fixture(Path(db))
    print(f"wrote {n} TEST observations for {symbol} to {db}")


if __name__ == "__main__":
    main()
