"""Derive 5-minute, 15-minute, hourly and daily candles from one-minute candles.

Input: one-minute candles with the six fields (timestamp = UTC minute start), in
arrival order. A minute may carry `complete` (True/False) when it came from the
live candle builder. Output: [{"candle": {six fields}, "status": {...}}].

Rules
-----
* Session: the exchange's regular session (backend.market_calendar), including
  early closes and daylight-saving changes. Minutes outside it are left out of
  5m/15m/1h/1d candles and counted in the result's `excluded_minutes`.
* Alignment: intraday groups start at the session open (09:30 ET), so 5m groups are
  09:30, 09:35, …; hourly groups are 09:30, 10:30, …, 15:30 (the last one runs to the
  close). A group never crosses the session close, so it is shorter on early-close days.
  A daily candle covers one session and is timestamped at its open, in UTC.
* Aggregation: open of the first minute, highest high, lowest low, close of the last
  minute, and the sum of minute volumes.
* Revisions: a later minute with the same timestamp replaces the earlier one.
* Unknown volume vs coverage (kept outside the six fields):
    volume = None if any minute in the group has unknown volume (`volume_known` False);
    coverage = minutes present / minutes the group spans, so gaps show as partial
    coverage. A known zero volume stays 0.
* Availability (live use and replay): with `as_of_ms`, a plain one-minute candle is
  used only once its minute has ended; a live minute that carries `complete` is used
  from its start (it was already built as of that time). `complete` on a group means
  the group's interval has ended by `as_of_ms`.
"""

from datetime import datetime, timezone

from backend.market_calendar import session_containing

MINUTE_MS = 60_000
TIMEFRAMES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": None}  # minutes per group; None = session


def to_ms(iso_utc: str) -> int:
    return round(datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).timestamp() * 1000)


def to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _available(minutes: list[dict], as_of_ms: int | None) -> list[dict]:
    latest: dict[int, dict] = {}
    for m in minutes:                                   # later entries replace earlier (revisions)
        start = to_ms(m["timestamp"])
        if as_of_ms is not None:
            usable_from = start if "complete" in m else start + MINUTE_MS
            if usable_from > as_of_ms:
                continue
        latest[start] = m
    return [latest[k] for k in sorted(latest)]


def aggregate(minutes: list[dict], timeframe: str = "1m", as_of_ms: int | None = None) -> dict:
    """Return {"timeframe", "candles": [{"candle", "status"}], "excluded_minutes"}."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {list(TIMEFRAMES)}")
    rows = _available(minutes, as_of_ms)

    if timeframe == "1m":
        out = []
        for m in rows:
            candle = {k: m[k] for k in ("timestamp", "open", "high", "low", "close", "volume")}
            out.append({"candle": candle, "status": {
                "complete": m.get("complete", True),   # plain minutes passed the as-of filter, so they have ended
                "minutes_present": 1, "minutes_expected": 1, "coverage_complete": True,
                "volume_known": m["volume"] is not None,
            }})
        return {"timeframe": timeframe, "candles": out, "excluded_minutes": 0}

    size = TIMEFRAMES[timeframe]
    groups: dict[int, dict] = {}
    excluded = 0
    for m in rows:
        start = to_ms(m["timestamp"])
        session = session_containing(start)
        if session is None:
            excluded += 1
            continue
        open_ms, close_ms = session
        if size is None:
            g_start, g_end = open_ms, close_ms
        else:
            g_start = open_ms + (start - open_ms) // (size * MINUTE_MS) * size * MINUTE_MS
            g_end = min(g_start + size * MINUTE_MS, close_ms)
        g = groups.setdefault(g_start, {"end": g_end, "minutes": []})
        g["minutes"].append(m)

    out = []
    for g_start in sorted(groups):
        g = groups[g_start]
        ms_ = g["minutes"]                                # already in timestamp order
        volumes = [m["volume"] for m in ms_]
        unknown = sum(v is None for v in volumes)
        live_forming = any(m.get("complete") is False for m in ms_)
        expected = (g["end"] - g_start) // MINUTE_MS
        out.append({
            "candle": {
                "timestamp": to_iso(g_start),
                "open": ms_[0]["open"],
                "high": max(m["high"] for m in ms_),
                "low": min(m["low"] for m in ms_),
                "close": ms_[-1]["close"],
                "volume": None if unknown else sum(volumes),
            },
            "status": {
                "complete": (as_of_ms is None or as_of_ms >= g["end"]) and not live_forming,
                "interval_end": to_iso(g["end"]),
                "minutes_present": len(ms_),
                "minutes_expected": expected,
                "coverage_complete": len(ms_) == expected,
                "volume_known": unknown == 0,
                "minutes_with_unknown_volume": unknown,
            },
        })
    return {"timeframe": timeframe, "candles": out, "excluded_minutes": excluded}
