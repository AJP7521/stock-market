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
    Both averages come from the registered "sma" indicator type (_sma_exact).
    """
    validate_windows(short, long)
    shorts, longs = _sma_exact(candles, short), _sma_exact(candles, long)
    side = None
    points, crossovers = [], []
    for c, s, l in zip(candles, shorts, longs):
        ts = c["timestamp"]
        if not c.get("complete", True):
            points.append({"timestamp": ts, "short": None, "long": None, "side": side,
                           "signal": None, "note": "candle still forming"})
            continue
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


# ---------------------------------------------------------------------------
# Indicator types: the interface for adding new indicators
# ---------------------------------------------------------------------------
#
# Every indicator type is a calculation with:
#   inputs   the candles for one symbol and interval (six fields + `complete`)
#            and its settings, validated against the type's `settings` schema
#   units    what the output values measure
#   output   exactly one point per input candle: {"timestamp", "value"}, where
#            value is a float, or None for a warm-up gap or a candle still forming
#
# Only completed candles feed a calculation, so an unfinished candle never changes
# a confirmed value. To add a type, write a compute(candles, settings) function
# and register it in INDICATOR_TYPES. FastAPI lists the types to Vue, Vue lets the
# user create named instances, and their settings are stored in SQLite
# (backend/indicator_store.py).

def _completed_window_values(candles: list[dict], window: int, reduce) -> list[Fraction | None]:
    """Exact `reduce(last `window` completed candles)` at each candle; None during warm-up or forming."""
    done: list[dict] = []
    values = []
    for c in candles:
        if not c.get("complete", True):
            values.append(None)
            continue
        done.append(c)
        values.append(reduce(done[-window:]) if len(done) >= window else None)
    return values


def _points(candles: list[dict], values: list[Fraction | None]) -> list[dict]:
    """Exact values -> one output point per candle (floats only here, for the response)."""
    out = []
    for c, v in zip(candles, values):
        p = {"timestamp": c["timestamp"], "value": None if v is None else float(v)}
        if not c.get("complete", True):
            p["note"] = "candle still forming"
        out.append(p)
    return out


def _sma_exact(candles: list[dict], n: int) -> list[Fraction | None]:
    return _completed_window_values(candles, n, lambda w: sum(_exact(c["close"]) for c in w) / n)


def sma_points(candles: list[dict], settings: dict) -> list[dict]:
    """Average close of the last N completed candles (the same values sma_crossover compares)."""
    return _points(candles, _sma_exact(candles, settings["window"]))


def range_points(candles: list[dict], settings: dict) -> list[dict]:
    """Highest high minus lowest low over the last N completed candles."""
    return _points(candles, _completed_window_values(
        candles, settings["window"],
        lambda w: max(_exact(c["high"]) for c in w) - min(_exact(c["low"]) for c in w)))


WINDOW_SETTING = {"name": "window", "label": "Window (candles)", "type": "integer", "minimum": 1,
                  "maximum": 1000, "default": 14,
                  "description": "Number of completed candles in the calculation"}

INDICATOR_TYPES = {
    "sma": {
        "label": "Simple moving average",
        "description": "Average closing price of the last N completed candles.",
        "units": "price (quote currency, same scale as the candles)",
        "pane": "price",
        "settings": [WINDOW_SETTING],
        "compute": sma_points,
    },
    "price_range": {
        "label": "Rolling price range",
        "description": "Highest high minus lowest low of the last N completed candles.",
        "units": "price difference (quote currency)",
        "pane": "separate",
        "settings": [WINDOW_SETTING],
        "compute": range_points,
    },
}


def describe_types() -> list[dict]:
    """Indicator types and their settings, without the compute functions (for the API)."""
    return [{"key": key, **{k: v for k, v in t.items() if k != "compute"},
             "inputs": "completed candles (timestamp, open, high, low, close, volume) and the settings below",
             "output": "one value per candle time, or a warm-up gap (null)"}
            for key, t in INDICATOR_TYPES.items()]


def validate_settings(type_key: str, settings: dict) -> dict:
    """Return settings with defaults applied, or raise ValueError with a readable message."""
    if type_key not in INDICATOR_TYPES:
        raise ValueError(f"unknown indicator type {type_key!r}; choose one of {sorted(INDICATOR_TYPES)}")
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")
    schema = INDICATOR_TYPES[type_key]["settings"]
    known = {s["name"] for s in schema}
    extra = set(settings) - known
    if extra:
        raise ValueError(f"unknown setting(s) {sorted(extra)} for {type_key}")
    clean = {}
    for s in schema:
        value = settings.get(s["name"], s["default"])
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{s['label']} must be a whole number, got {value!r}")
        if value < s["minimum"]:
            raise ValueError(f"{s['label']} must be at least {s['minimum']}, got {value}")
        if value > s["maximum"]:
            raise ValueError(f"{s['label']} must be at most {s['maximum']}, got {value}")
        clean[s["name"]] = value
    return clean


def compute(type_key: str, candles: list[dict], settings: dict) -> list[dict]:
    return INDICATOR_TYPES[type_key]["compute"](candles, validate_settings(type_key, settings))
