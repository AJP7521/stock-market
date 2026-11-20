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

`signal` writes symbol TESTSIG (Prompt 16F): five one-minute candles on 2026-11-09 from 15:00 UTC, each built from
four observations (open, high, low, close in arrival order), chosen so each cutoff shows one kind of evidence for the
chart_signal rule (newer close vs the preceding close):
    15:01  open 11,   close 12    green, close up from 11            -> clear up
    15:02  open 12,   close 11    red, close down from 12            -> clear down
    15:03  open 11.6, close 11.4  RED candle, but close UP from 11   -> mixed (candle colour disagrees with the rule)
    15:04  open 11.2, close 11.4  GREEN candle, close EQUAL to 11.4  -> mixed (equal closes: wait)

`backtest` writes symbol TESTBT (Prompt 17): 18 one-minute candles on 2026-11-09 from 15:00 UTC, each built from two
observations (open at :10, close at :50). Closes and opens are BACKTEST_MINUTES below. With SMA(2)/SMA(4), starting
cash $1,000, fills at the next candle's open and whole shares (backend/backtest.py), a hand calculation gives:
    signal (candle close)   fill (next open)           cash after   shares   realized
    15:05 up                buy 48 @ 20.5   (15:06)       16.00       48         0.00
    15:08 down              sell 48 @ 21    (15:09)    1,024.00        0        24.00   win  +24.00
    15:11 up                buy 47 @ 21.5   (15:12)       13.50       47        24.00
    15:13 down              sell 47 @ 18.5  (15:14)      883.00        0      -117.00   loss -141.00
    15:16 up                buy 39 @ 22.5   (15:17)        5.50       39      -117.00   still open
Final: last close 24 -> equity $941.50 (-5.85%), unrealized +58.50, total P&L -58.50; peak equity $1,120 (15:06),
trough $859.50 (15:13): max drawdown 23.26%; 1 win / 2 closed round trips; holding at 6 of 18 closes.
Buy and hold (50 shares at the first open, 20, valued at the last close, 24): $1,200 (+20.00%).

`synthetic` writes symbol SYNTHBT (Prompt 17): two sessions (2026-11-10 and 2026-11-11, 14:30-21:00 UTC) of a seeded
random walk (random.Random(17)), four observations a minute (open, high, low, close), 780 candles. It is SYNTHETIC test
data for demonstrating a longer backtest; it is not a market record.

Every row has source = 'test'. Each fixture replaces its database file (backtest/synthetic: unless keep=True).
"""

import argparse
import random
from datetime import datetime, timedelta, timezone
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

SIGNAL_SYMBOL = "TESTSIG"
SIGNAL_MINUTES = [  # (minute, [open, high, low, close]) in arrival order
    ("15:00", [10, 11.2, 9.8, 11]),
    ("15:01", [11, 12.3, 10.9, 12]),
    ("15:02", [12, 12.1, 10.8, 11]),
    ("15:03", [11.6, 11.7, 11.3, 11.4]),
    ("15:04", [11.2, 11.6, 11.1, 11.4]),
]
DEFAULT_SIGNAL_DB = PROJECT_ROOT / "backend" / "data" / "test-signal.sqlite"

BACKTEST_SYMBOL = "TESTBT"
BACKTEST_MINUTES = [  # (minute, open, close); a gap between a close and the next open shows the fill is not the close
    ("15:00", 20, 20), ("15:01", 20, 19), ("15:02", 19, 18), ("15:03", 18, 17), ("15:04", 17, 19),
    ("15:05", 19, 21),      # SMA(2) 20 > SMA(4) 18.75: upward crossover
    ("15:06", 20.5, 23), ("15:07", 23, 22),
    ("15:08", 22, 20),      # SMA(2) 21 < SMA(4) 21.5: downward crossover
    ("15:09", 21, 18), ("15:10", 18, 19),
    ("15:11", 19, 21),      # up
    ("15:12", 21.5, 20),
    ("15:13", 20, 18),      # down
    ("15:14", 18.5, 17), ("15:15", 17, 19),
    ("15:16", 19, 22),      # up
    ("15:17", 22.5, 24),
]
DEFAULT_BACKTEST_DB = PROJECT_ROOT / "backend" / "data" / "test-backtest.sqlite"

SYNTH_SYMBOL = "SYNTHBT"
SYNTH_SESSIONS = ("2026-11-10", "2026-11-11")
SYNTH_SEED = 17
DEFAULT_SYNTH_DB = PROJECT_ROOT / "backend" / "data" / "test-synthetic.sqlite"


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


def write_signal_fixture(path: Path, keep: bool = False) -> int:
    """Write the chart_signal fixture (TESTSIG). Replaces `path` unless keep=True (then adds to it)."""
    path = Path(path)
    if not keep:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
    conn = connect(path)
    volume, n = 30_000, 0
    for minute, prices in SIGNAL_MINUTES:
        for second, price in zip((5, 20, 35, 50), prices):
            volume += 40
            t = _ms(f"{minute}:{second:02d}")
            record(conn, {"id": SIGNAL_SYMBOL, "price": price, "time": str(t), "day_volume": str(volume)},
                   t + 300, source="test")
            n += 1
    conn.close()
    return n


def _fresh(path: Path, keep: bool):
    path = Path(path)
    if not keep:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
    return connect(path)


def write_backtest_fixture(path: Path, keep: bool = False) -> int:
    """Write the backtest fixture (TESTBT). Replaces `path` unless keep=True (then adds to it)."""
    conn = _fresh(path, keep)
    volume, n = 50_000, 0
    for minute, open_, close in BACKTEST_MINUTES:
        for second, price in ((10, open_), (50, close)):
            volume += 100
            t = _ms(f"{minute}:{second:02d}")
            record(conn, {"id": BACKTEST_SYMBOL, "price": price, "time": str(t), "day_volume": str(volume)},
                   t + 300, source="test")
            n += 1
    conn.close()
    return n


def write_synthetic_fixture(path: Path, keep: bool = False) -> int:
    """Write the SYNTHETIC random-walk fixture (SYNTHBT). Replaces `path` unless keep=True."""
    conn = _fresh(path, keep)
    rng = random.Random(SYNTH_SEED)
    price, n = 100.0, 0
    for day in SYNTH_SESSIONS:
        volume = 0
        start = datetime.fromisoformat(f"{day}T14:30:00+00:00")
        for i in range(390):
            minute = start + timedelta(minutes=i)
            open_ = round(price, 2)
            close = round(open_ + rng.gauss(0.005, 0.12), 2)
            high = round(max(open_, close) + abs(rng.gauss(0, 0.04)), 2)
            low = round(min(open_, close) - abs(rng.gauss(0, 0.04)), 2)
            for second, p in ((5, open_), (20, high), (35, low), (50, close)):
                volume += rng.randint(50, 400)
                t = round((minute + timedelta(seconds=second)).timestamp() * 1000)
                record(conn, {"id": SYNTH_SYMBOL, "price": p, "time": str(t), "day_volume": str(volume)},
                       t + 300, source="test")
                n += 1
            price = close
    conn.close()
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="Write labelled test fixtures to a separate SQLite file")
    parser.add_argument("fixture", choices=["sma", "range", "signal", "backtest", "synthetic"])
    parser.add_argument("--db", help="target SQLite file (default backend/data/test-<fixture>.sqlite)")
    parser.add_argument("--keep", action="store_true", help="backtest/synthetic: add to the file instead of replacing it")
    args = parser.parse_args()
    if args.fixture == "sma":
        db, symbol = args.db or DEFAULT_SMA_DB, SMA_SYMBOL
        n = write_sma_fixture(Path(db))
    elif args.fixture == "range":
        db, symbol = args.db or DEFAULT_RANGE_DB, RANGE_SYMBOL
        n = write_range_fixture(Path(db))
    elif args.fixture == "signal":
        db, symbol = args.db or DEFAULT_SIGNAL_DB, SIGNAL_SYMBOL
        n = write_signal_fixture(Path(db))
    elif args.fixture == "backtest":
        db, symbol = args.db or DEFAULT_BACKTEST_DB, BACKTEST_SYMBOL
        n = write_backtest_fixture(Path(db), keep=args.keep)
    else:
        db, symbol = args.db or DEFAULT_SYNTH_DB, SYNTH_SYMBOL
        n = write_synthetic_fixture(Path(db), keep=args.keep)
    print(f"wrote {n} TEST observations for {symbol} to {db}")


if __name__ == "__main__":
    main()
