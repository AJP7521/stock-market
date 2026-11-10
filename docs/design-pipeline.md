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

## What exists today (after Prompt 9D)

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
| Live feed inside FastAPI (yfinance subscribe, own receive loop, records to SQLite, feed status) | `backend/live.py` | exists |
| WebSocket `/api/ws/candles?symbol&timeframe` (candle + feed messages) | `backend/app.py` | exists |
| Timeframe selector, live status bar, in-place candle updates | `frontend/src/App.vue`, `CandleChart.vue` | exists |
| Read-only replay (receipt order, as-of each arrival) + `/api/replay`, `/api/replay/sessions` | `backend/replay.py`, `backend/app.py` | exists |
| Replay mode in the dashboard (Replay label, step, replay clock, source time, flags) | `frontend/src/App.vue` | exists |
| `LIVE_FEED=off` switch (no Yahoo connection) | `backend/app.py` | exists |
| SMA short/long + crossovers (exact arithmetic, completed candles only); `/api/indicators/sma`; per-step SMA in replay | `backend/indicators.py`, `backend/app.py`, `backend/replay.py` | exists |
| SMA lines and crossover markers on the chart | `frontend/src/CandleChart.vue`, `chartAdapter.js`, `App.vue` | exists |
| Labelled test fixtures in a separate DB | `backend/fixtures.py` | exists |
| Indicator interface (`INDICATOR_TYPES`: sma, price_range) and stored named instances | `backend/indicators.py`, `backend/indicator_store.py` | exists |
| Indicator panel, lines per instance (price pane / second pane), values table | `frontend/src/IndicatorPanel.vue`, `CandleChart.vue`, `App.vue` | exists |
| Long-only paper account, one execution function, next-price fills, idempotent request ids | `backend/paper.py`, `/api/paper/*` | exists |
| Paper panel (cash, shares, orders, buy/sell/close/hold/wait, retry) | `frontend/src/PaperPanel.vue` | exists |
| Test price feed (`TEST_FEED=on`, `POST /api/test/observations`) | `backend/app.py` | exists |
| P&L history (weighted average cost; rows on fills and completed-candle valuations; one row per event) | `backend/pnl.py`, `/api/paper/pnl`, `PaperPanel.vue` | exists |
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
5. **Volume:** the last running total (`day_volume`) in the minute minus the last running total before the minute in the same trading session (America/New_York date). The baseline must come from the immediately preceding minute; otherwise volume is unknown (a gap in observations, added in Prompt 8). It is **unknown (None)** when there is no baseline, an observation lacks a running total, or the running total decreases (a reset; the baseline restarts). A known 0 stays 0.
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

## Live update path (Prompt 8)

```
Yahoo WS --(yfinance subscribe/decode)--> live.SymbolFeed._handle
                                              | recorder.record()  -> SQLite observations/receipts
                                              v
                              queue event {source_time_ms}
                                              v
WS /api/ws/candles ---- candle_series(symbol, timeframe, now) ---- candles + timeframes
     |   {type:"candle", symbol, interval, candle{6}, status, feed}
     |   {type:"feed", feed{status, detail, last_source_time_ms, ...}}  every 1 s
     v
App.vue (status bar, timeframe selector) --> CandleChart.upsert(row): series.update(bar, historical)
```

Feed states: `connecting`, `live`, `quiet` (connected, no message for 30 s), `disconnected` (dropped; retry every 5 s), `paused` (test control), and `offline` (SAMPLE). The browser shows the feed state separately from its own FastAPI socket state.

## Replay path (Prompt 9)

```
SQLite (opened mode=ro) --receipts ORDER BY seq, source time in [start,end)--> replay.replay()
   for each arrival:  as_of = its receipt time
       build_minute_candles(observations seen so far, as_of) -> aggregate(timeframe, as_of)
       -> step {replay_time, observation{source_time, late, revises_id, is_repeat}, candle, status}
GET /api/replay ---> App.vue plays steps at N steps/s ---> CandleChart.upsert (historical updates allowed)
```

Replay uses the same candle code and availability rules as live, so a late observation or a revision changes its candle at the step where it originally arrived.

## SMA and crossover rules (Prompt 9A)

- **Inputs:** candles for one symbol and interval with their `complete` flag, and windows short < long (positive whole numbers).
- **SMA(N) at a candle:** the mean close of the last N *completed* candles, including that candle. It is `None` during warm-up and for a forming candle.
- **Side:** short > long is "above", short < long is "below". A tie keeps the last side, and the first comparable pair sets a side without a signal.
- **Signal:** only a change of side; below → above is **up**, above → below is **down**.
- **Precision:** comparisons use exact rationals; floats are produced only for display.
- **Replay:** each step recomputes from the candles available at that step's receipt time, so an unfinished candle never confirms a crossover.

## Indicator interface (Prompt 9B)

```
INDICATOR_TYPES[key] = {label, description, units, pane, settings: [schema], compute(candles, settings)}
        |                                   GET /api/indicator-types  --> IndicatorPanel (type, name, settings)
        |                                   POST /api/indicator-configs --> validate_settings --> SQLite indicator_configs
        v
GET /api/indicator-values: candle_series(symbol, timeframe) --> compute() per stored config
        --> [{id, name, type, pane, units, points: [{timestamp, value|null}]}] --> CandleChart lines + legend + table
```

- **Inputs:** completed candles and validated settings.
- **Output:** one value per candle time, or a warm-up gap (null).
- **SMA:** shares `_sma_exact` with the 9A crossover.
- **Adding a type:** write a `compute` function and register it; no Vue change is needed, because the panel builds its settings inputs from the schema.

## Paper trading path (Prompt 9C)

```
Vue PaperPanel --POST /api/paper/orders {symbol, action, quantity, request_id}--> paper.execute()
      buy/sell/close -> paper_orders (pending | rejected+reason)      hold/wait -> no order
      same request_id again -> the original order (duplicate: true)

new observation (live.py, or POST /api/test/observations with TEST_FEED=on) -> recorder.record()
      -> paper.fill_paper_orders(observation)
           for each pending order submitted before this observation was received (oldest first):
             BEGIN; recheck cash/shares; fill at the observation price (zero fees) or reject;
             paper_fills + paper_orders + paper_accounts.cash + paper_positions; COMMIT
```

## P&L history (Prompt 9D)

```
observation arrives -> paper.process_observation
    1. pnl.value_open_positions(symbol, as_of = receipt time)
         latest completed 1m candle (candles.minute_candles) closed after the last fill?
         -> pnl_history row "valuation:<symbol>:<candle start>" at the candle close (INSERT OR IGNORE)
    2. pending orders fill -> in the same transaction: pnl.record_fill(fill)
         paper_cost_basis (basis, cumulative realized) + pnl_history row "fill:<id>" at the fill price
GET /api/paper/pnl?symbol= -> PaperPanel "Profit and loss history" table
```
