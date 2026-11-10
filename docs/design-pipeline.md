# Design pipeline

How data is planned to move through the workstation, and which parts exist today.

## Planned data flow

```
  [CSV 1-min candles]          [Yahoo Finance WebSocket]
  prompts/data/*.csv                    |
        |                               v
        |                     backend: receiver (yfinance)
        |                               |
        |                               v
        |                  backend: SQLite observation store  <---- replay (read-only)
        |                               |                               |
        v                               v                               |
  backend: 1-min candles  <---- backend: candle builder <---------------'
        |
        v
  backend: timeframe aggregation (5m / 15m / 1h / daily, session-aligned)
        |
        +--> backend: indicators (SMA, crossovers, rolling range, ...)
        |                 |
        v                 v
  FastAPI  GET /api/history,  WebSocket candle updates,  indicator endpoints
        |
        v
  Vue dashboard: Lightweight Charts (candles, volume, indicator lines, markers)
        |                         |
        |   "Ask about chart" PNG + snapshot ID
        |                         v
        |        FastAPI POST /api/decisions
        |              |-> snapshot (frozen candles, indicators, account)
        |              |-> analyst model call -> reviewer model call
        |              `-> Python validation -> decision record (SQLite)
        |                         |
        v                         v
  Paper account controls --> backend: execution function (fills, cash, shares)
                                   |
                                   v
                          SQLite: orders, fills, P&L history
                                   |
                                   v
                          Vue: account, orders, P&L table, decision trace,
                               voice session (WebRTC, read-only summary)
```

## What exists today (after Prompt 7A)

```
browser --GET /api/history--> Vite :5173 --proxy /api--> FastAPI :8000 --> backend/history.py
                                                                            reads sample-candles.csv
App.vue <---------- JSON [ {timestamp,open,high,low,close,volume}, ... ] <-----'
   |
   v
chartAdapter.js (UTC ISO -> UTCTimestamp) --> CandleChart.vue: candlestick + volume histogram
```

| Component | File(s) | Status |
|---|---|---|
| Sample one-minute candles | `prompts/data/sample-candles.csv` | exists (3 synthetic rows) |
| FastAPI app, `GET /api/health` | `backend/app.py` | exists |
| Vue app showing backend health | `frontend/src/App.vue`, `src/main.js`, `index.html` | exists |
| `/api` proxy (HTTP; `ws: true` ready for WebSockets) | `frontend/vite.config.js` | exists |
| CSV loader (`csv` module; six fields; sorted by UTC minute start) | `backend/history.py` | exists |
| `GET /api/history?symbol=&timeframe=&as_of=` returns `{candles: [six fields], status: [...]}`; SAMPLE = CSV, other symbols = SQLite observations | `backend/app.py` | exists |
| Candlestick + volume chart with an inspection legend (Lightweight Charts 5.2.1) | `frontend/src/CandleChart.vue` | exists |
| Chart adapter: UTC ISO timestamp → UTCTimestamp seconds, the same time for both series | `frontend/src/chartAdapter.js` | exists |
| Yahoo stream receiver (yfinance `AsyncWebSocket`; time-limited check; not yet wired into FastAPI) | `backend/yahoo_stream.py` | exists |
| SQLite observation store: `observations` (distinct contents) + `receipts` (arrival log) | `backend/recorder.py`, `backend/data/market.sqlite` | exists (CLI `record`; not yet run inside FastAPI) |
| One-minute candle builder from observations (as-of aware) | `backend/candles.py`, tests in `backend/tests/test_candles.py` | exists (not yet served over HTTP) |
| Larger timeframes 5m/15m/1h/1d, session-aligned, with coverage status | `backend/timeframes.py` | exists |
| NYSE regular-session calendar (holidays, early closes, DST) | `backend/market_calendar.py` | exists |
| Live chart updates, replay | – | planned (Prompts 8–9) |
| Indicators, paper account, P&L | – | planned (Prompts 9A–9D) |
| Decisions, review, automation | – | planned (Prompts 10–12) |
| Voice | – | planned (Prompt 13) |
| SmokeTest command | – | planned (Prompt 14) |

## File responsibilities

- `backend/`: all calculations (candles, indicators, P&L, validation), storage,
  provider calls and FastAPI routes. The single source of truth for numbers.
- `frontend/`: user input, chart rendering, chart capture, and presentation of
  values supplied by the backend. It does no trading or indicator maths.
- `docs/verification.md`: every check run, with expected and observed results.

## One-minute candle aggregation rules (Prompt 7)

1. **Availability:** use an observation only if it was received by the as-of time and its source time is not after it. Live use passes now; replay passes the displayed time.
2. **Corrections:** an identified revision (`revises_id`) replaces the row it revises once it is available. Distinct messages sharing a source time are all used.
3. **Order within a minute:** source time, then first arrival (observation id). A late observation is placed by its source time.
4. **Prices:** open is the first, high the maximum, low the minimum, close the last.
5. **Volume:** the last running total (`day_volume`) in the minute minus the last running total before the minute in the same trading session (America/New_York date). It is **unknown (None)** when there is no baseline, an observation lacks a running total, or the running total decreases (a reset; the baseline restarts). A known 0 stays 0.
6. **Status lives outside the six fields:** `complete` (the as-of time has reached the end of the minute), `observation_count`, `volume_known`, `volume_note`, `last_source_time`, `last_received_time`.
7. **Recalculation:** candles are recomputed from the store, so late or corrected observations update the affected candle on the next calculation, even after the minute has completed.

## Larger timeframe rules (Prompt 7A)

```
1-minute candles (CSV or candles.py) --> timeframes.aggregate(timeframe, as_of)
                                             | market_calendar.session_containing(minute)
                                             v
          groups aligned to the session open (09:30 ET), ending at or before the close
          1d = whole session, timestamped at its open (UTC)
```

- **Session:** NYSE regular hours, 09:30–16:00 ET (13:00 on early closes). Minutes outside it are excluded from 5m/15m/1h/1d and counted in `excluded_minutes`.
- **Aggregation:** first open, highest high, lowest low, last close, sum of volumes. A later minute with the same timestamp replaces the earlier one.
- **Status:** `complete` (the interval has ended by as-of and no live minute is still forming), `minutes_present`/`minutes_expected`/`coverage_complete` (gaps), and `volume_known`/`minutes_with_unknown_volume` (unknown is separate from 0).
- **as_of:** a CSV minute is used only after it has ended; a live minute carries its own `complete` flag.
