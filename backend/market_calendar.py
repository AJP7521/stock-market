"""NYSE/Nasdaq regular-session calendar, built from published exchange rules (stdlib only).

Regular session: 09:30–16:00 America/New_York; early closes end at 13:00.
zoneinfo applies the US daylight-saving changes, so the UTC open moves between
13:30Z (EDT) and 14:30Z (EST).

Full-day holidays (NYSE rules):
  New Year's Day        Jan 1; Sunday -> Monday. Saturday -> not observed (Dec 31 stays open).
  Martin Luther King Jr. Day   3rd Monday of January
  Washington's Birthday        3rd Monday of February
  Good Friday                  Friday before Easter Sunday
  Memorial Day                 last Monday of May
  Juneteenth                   Jun 19 (from 2022); Saturday -> Friday, Sunday -> Monday
  Independence Day             Jul 4; Saturday -> Friday, Sunday -> Monday
  Labor Day                    1st Monday of September
  Thanksgiving Day             4th Thursday of November
  Christmas Day                Dec 25; Saturday -> Friday, Sunday -> Monday
Early closes (13:00 ET):
  Jul 3 when it falls Monday–Thursday; the day after Thanksgiving; Dec 24 when it falls Monday–Thursday.
One-off closures that no rule can predict are listed in SPECIAL_CLOSURES. Add new
ones when the exchange announces them.
"""

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

EXCHANGE_TZ = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

SPECIAL_CLOSURES = {
    date(2018, 12, 5): "National Day of Mourning (George H. W. Bush)",
    date(2025, 1, 9): "National Day of Mourning (Jimmy Carter)",
}


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """Saturday -> Friday before, Sunday -> Monday after."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def holidays(year: int) -> dict[date, str]:
    MON, THU = 0, 3
    h = {}
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:                      # Saturday New Year is not observed
        h[_observed(new_year)] = "New Year's Day"
    h[_nth_weekday(year, 1, MON, 3)] = "Martin Luther King Jr. Day"
    h[_nth_weekday(year, 2, MON, 3)] = "Washington's Birthday"
    h[_easter(year) - timedelta(days=2)] = "Good Friday"
    h[_last_weekday(year, 5, MON)] = "Memorial Day"
    if year >= 2022:
        h[_observed(date(year, 6, 19))] = "Juneteenth"
    h[_observed(date(year, 7, 4))] = "Independence Day"
    h[_nth_weekday(year, 9, MON, 1)] = "Labor Day"
    h[_nth_weekday(year, 11, THU, 4)] = "Thanksgiving Day"
    h[_observed(date(year, 12, 25))] = "Christmas Day"
    h.update({d: name for d, name in SPECIAL_CLOSURES.items() if d.year == year})
    return h


@lru_cache(maxsize=None)
def early_closes(year: int) -> dict[date, str]:
    e = {}
    jul3 = date(year, 7, 3)
    if jul3.weekday() <= 3:
        e[jul3] = "Day before Independence Day"
    e[_nth_weekday(year, 11, 3, 4) + timedelta(days=1)] = "Day after Thanksgiving"
    dec24 = date(year, 12, 24)
    if dec24.weekday() <= 3:
        e[dec24] = "Christmas Eve"
    return {d: n for d, n in e.items() if d not in holidays(year)}


def session_for(day: date) -> tuple[datetime, datetime] | None:
    """(open, close) in UTC for a trading day, or None for weekends and holidays."""
    if day.weekday() >= 5 or day in holidays(day.year):
        return None
    close_t = EARLY_CLOSE if day in early_closes(day.year) else REGULAR_CLOSE
    open_dt = datetime.combine(day, REGULAR_OPEN, EXCHANGE_TZ).astimezone(timezone.utc)
    close_dt = datetime.combine(day, close_t, EXCHANGE_TZ).astimezone(timezone.utc)
    return open_dt, close_dt


def session_containing(ms: int) -> tuple[int, int] | None:
    """(open_ms, close_ms) of the regular session containing this instant, else None."""
    local_day = datetime.fromtimestamp(ms / 1000, tz=EXCHANGE_TZ).date()
    s = session_for(local_day)
    if s is None:
        return None
    open_ms, close_ms = (round(x.timestamp() * 1000) for x in s)
    return (open_ms, close_ms) if open_ms <= ms < close_ms else None
