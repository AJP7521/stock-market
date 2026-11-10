"""Read one-minute candles from a local CSV file.

Each row is one minute for one instrument. The UTC timestamp marks the start
of the minute: the 14:30 row covers 14:30:00 up to, but excluding, 14:31:00.
These one-minute candles are the source for every larger timeframe.
"""

import csv
from datetime import datetime
from pathlib import Path

FIELDS = ["timestamp", "open", "high", "low", "close", "volume"]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CSV = PROJECT_ROOT / "prompts" / "data" / "sample-candles.csv"


def _parse_utc(text: str) -> datetime:
    """Parse an ISO 8601 UTC timestamp such as 2026-11-09T14:30:00Z."""
    if not text.endswith("Z"):
        raise ValueError(f"timestamp must be UTC with a trailing 'Z': {text!r}")
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def load_candles(path: Path = SAMPLE_CSV) -> list[dict]:
    """Return the CSV's candles as dicts with the six fields, ordered by timestamp.

    Prices are floats. Volume is an integer number of shares traded in the minute.
    Raises ValueError if the header is not exactly the six expected fields or a
    value cannot be parsed.
    """
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != FIELDS:
            raise ValueError(f"expected header {FIELDS}, found {reader.fieldnames}")
        rows = []
        for line_no, row in enumerate(reader, start=2):
            try:
                ts = _parse_utc(row["timestamp"])
                rows.append(
                    (
                        ts,
                        {
                            "timestamp": row["timestamp"],
                            "open": float(row["open"]),
                            "high": float(row["high"]),
                            "low": float(row["low"]),
                            "close": float(row["close"]),
                            "volume": int(row["volume"]),
                        },
                    )
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path.name} line {line_no}: {exc}") from exc
    rows.sort(key=lambda pair: pair[0])
    return [candle for _, candle in rows]
