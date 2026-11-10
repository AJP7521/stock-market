"""Labelled TEST fixtures written to a separate SQLite file (never the live database by default).

    python3 -m backend.fixtures sma --db backend/data/test-sma.sqlite

`sma` writes symbol TESTSMA: seven one-minute candles on 2026-11-09 from 15:00 UTC
with closes 13, 10, 7, 13, 19, 7, 1 (one observation per minute, so open = high =
low = close), then an eighth observation at 15:07:10 with price 30. That last minute
is still forming at the end of the fixture; if it were counted, it would create an
upward SMA(2)/SMA(3) crossover. Every row has source = 'test'.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from backend.recorder import PROJECT_ROOT, connect, record

SMA_SYMBOL = "TESTSMA"
SMA_CLOSES = [13, 10, 7, 13, 19, 7, 1]
SMA_FORMING_PRICE = 30
DEFAULT_SMA_DB = PROJECT_ROOT / "backend" / "data" / "test-sma.sqlite"


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Write labelled test fixtures to a separate SQLite file")
    parser.add_argument("fixture", choices=["sma"])
    parser.add_argument("--db", default=str(DEFAULT_SMA_DB))
    args = parser.parse_args()
    n = write_sma_fixture(Path(args.db))
    print(f"wrote {n} TEST observations for {SMA_SYMBOL} to {args.db}")


if __name__ == "__main__":
    main()
