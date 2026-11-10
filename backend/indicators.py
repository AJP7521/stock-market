"""Simple moving averages (SMA) and crossover signals. Python calculates; Vue only draws.

Input: candles for one symbol and interval, each with its six fields and a
`complete` flag (status from candles.py / timeframes.py).

Rules
-----
* SMA(N) at a candle = the average close of the last N **completed** candles,
  including that candle. It is unavailable (None) until N completed candles exist.
  An unfinished candle gets no value, so it can never produce a confirmed signal.
* Side: "above" when short SMA > long SMA, "below" when short < long. A tie keeps
  the last known side. The first comparable pair (both SMAs available) establishes
  a side without producing a signal. Only a change of side produces a signal:
  below -> above is "up", above -> below is "down".
* Comparisons use exact rational arithmetic (fractions.Fraction built from the
  decimal string of each close), so rounding cannot create or hide a crossover.
  Values are converted to float only for the response.
"""

from decimal import Decimal
from fractions import Fraction


def validate_windows(short: int, long: int) -> None:
    """Raise ValueError with a readable message unless 0 < short < long (whole numbers)."""
    for name, value in (("short", short), ("long", long)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} window must be a whole number, got {value!r}")
        if value <= 0:
            raise ValueError(f"{name} window must be a positive whole number, got {value}")
    if short >= long:
        raise ValueError(f"short window ({short}) must be smaller than long window ({long})")


def _exact(close) -> Fraction:
    return Fraction(Decimal(str(close)))


def sma_crossover(candles: list[dict], short: int, long: int) -> dict:
    """Return {"points": [...], "crossovers": [...]} aligned with `candles`.

    Each point: {"timestamp", "short", "long", "side", "signal"}; short/long are
    floats or None (warm-up or unfinished candle); signal is "up", "down" or None.
    """
    validate_windows(short, long)
    closes: list[Fraction] = []          # completed closes so far
    side = None
    points, crossovers = [], []
    for c in candles:
        ts = c["timestamp"]
        if not c.get("complete", True):
            points.append({"timestamp": ts, "short": None, "long": None, "side": side,
                           "signal": None, "note": "candle still forming"})
            continue
        closes.append(_exact(c["close"]))
        s = sum(closes[-short:]) / short if len(closes) >= short else None
        l = sum(closes[-long:]) / long if len(closes) >= long else None
        signal = None
        if s is not None and l is not None:
            new_side = "above" if s > l else "below" if s < l else side   # tie keeps last side
            if side is not None and new_side != side:
                signal = "up" if new_side == "above" else "down"
                crossovers.append({"timestamp": ts, "signal": signal,
                                   "short": float(s), "long": float(l)})
            side = new_side
        points.append({"timestamp": ts,
                       "short": float(s) if s is not None else None,
                       "long": float(l) if l is not None else None,
                       "side": side, "signal": signal})
    return {"short_window": short, "long_window": long, "points": points, "crossovers": crossovers}
