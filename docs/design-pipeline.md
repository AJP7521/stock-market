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

## What exists today (after Prompt 14)

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
| `candle_series` shared by HTTP, WebSocket and decision snapshots | `backend/series.py` | exists |
| `.env` loader (does not override the environment; never logs values) | `backend/settings.py`, `.env.example` | exists |
| Decision snapshots (frozen candle, SMAs, crossover, account; as-of aware; incomplete = ineligible) and decisions (provider call, rule check, explanation, one decision ID) | `backend/decisions.py`, `/api/decisions*` | exists |
| Decision providers: Claude Messages API (`anthropic` SDK, structured-output choice) (default) and OpenAI `POST /v1/decisions` (`DECISIONS_PROVIDER=openai`) | `backend/decisions.py` | exists |
| Chart snapshot at a selected completed candle (decision time = when it completed; chart section with candles, SMA and indicator lines through the cutoff) | `backend/decisions.py` (`create_snapshot(candle_time=…)`) | exists |
| PNG check (base64, signature, chunk CRCs, IHDR/IEND, decompressed size) and capture storage beside the database | `backend/chart_images.py` | exists |
| Three chart questions (action, crossover, evidence), checks by name, eligibility; provider test double (`DECISIONS_PROVIDER=test`) | `backend/decisions.py`, `GET /api/decisions/{id}/image` | exists |
| Ask about chart panel: off-screen render of the frozen snapshot, `takeScreenshot` PNG, preview, questions, answers, checks | `frontend/src/AskChartPanel.vue`, `CandleChart.vue` (`capture()`), `App.vue` | exists |
| Reviewer: second, separate call through the same provider client (verdict question), strategy rules, final check with every gate; saved under the same decision ID | `backend/review.py`, `backend/decisions.py` (`decide(review=True)`), `POST /api/decisions` (`review` defaults to true) | exists |
| Reviewer verdict, observed values and final-check table in the panel | `frontend/src/AskChartPanel.vue` | exists |
| Paper automation: start/stop (generation), runs deduplicated per account/strategy/symbol/interval/candle, apply only eligible approved decisions, pre-fill rechecks, trace | `backend/automation.py`, `/api/automation/*` | exists |
| Orders and fills linked to a decision (`decision_id`, one order per decision), FILL_CHECKS / ORDER_DECIDED hooks inside the fill transaction | `backend/paper.py` | exists |
| Automation panel: Start/Stop, polling for completed candles, off-screen render + capture, runs table, decision trace | `frontend/src/AutomationPanel.vue`, `App.vue` | exists |
| Voice about one decision: read-only record from SQLite, Claude replies with session history, sessions/turns stored; `GET /api/decisions`, `/api/voice/sessions*` | `backend/voice.py`, `backend/app.py` | exists |
| SmokeTest: one command, temporary DB, FastAPI + Vite + headless Chrome (Playwright), three checks, report, cleanup; GitHub Actions on push | `backend/smoke.py`, `.github/workflows/smoke.yml`, `backend/requirements-dev.txt` | exists |
| Test controls (TEST_FEED=on only): fixed server clock, runtime test-double answers / failures; automation order size per Start | `backend/recorder.py` (`TEST_NOW_MS`), `backend/decisions.py` (`TEST_ANSWERS`), `backend/app.py` (`/api/test/clock`, `/api/test/provider`), `backend/automation.py` | exists |
| Voice panel: decision picker, microphone (getUserMedia) indicator, Web Speech recognition + synthesis, End releases the microphone | `frontend/src/VoicePanel.vue` | exists |
| Voice | – | planned (Prompt 13) |

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

## Decision path (Prompt 10)

```
POST /api/decisions/snapshots {symbol, timeframe, short, long, as_of?}
    -> decisions.create_snapshot: series.candle_series (as of the decision time) -> last COMPLETED candle,
       indicators.sma_crossover at that candle, paper ledger cash/position from fills executed by then
    -> decision_snapshots row (kind application | test, complete 0/1, snapshot_json); no model call

POST /api/decisions {snapshot_id} (or settings to create one)
    -> decide(): incomplete/unknown snapshot -> status missing_input, no provider call
    -> build_request (provider-neutral: STRATEGY text, snapshot JSON, named choice question "action")
    -> make_provider(): default (unset or claude) -> ClaudeDecisions: client.beta.messages.create,
                          output_config.format json_schema {"action": enum[buy,sell,hold,wait,close]},
                          fallbacks "default" on a refusal
                        DECISIONS_PROVIDER=openai -> OpenAIDecisions: POST /v1/decisions (urllib)
    -> status decided | malformed_answer | timeout | provider_error
    -> check_rules (cash, shares, flat/open) -> validation valid | invalid | not_checked
    -> explain() from snapshot facts; decisions row (proposal, backend facts, request/response, latency)
    -> eligible_for_order only for a valid decision on a complete application snapshot
The paper tables are never written on this path.
```

## Ask about chart path (Prompt 10A)

```
AskChartPanel (cutoff = a completed candle, candles shown = N)
  1. POST /api/decisions/snapshots {symbol, timeframe, short, long, candle_time, chart_candles, as_of?}
       decision time = when the cutoff candle completed (never later than as_of / now)
       -> snapshot as in Prompt 10 + chart {range, candles (last N completed through the cutoff),
                                             sma (points, crossovers), indicators (configured lines)}
  2. render snapshot.chart in an off-screen CandleChart (same adapter as the dashboard)
       -> chart.takeScreenshot(true) -> PNG data URL (canvas only) -> preview with symbol, interval, cutoff
  3. POST /api/decisions {snapshot_id, image_png, capture {snapshot_id, symbol, interval, cutoff}}
       chart_images.read_png (unreadable -> 422 + recapture text) ; check_capture (mismatch -> 422)
       store_png -> <db folder>/captures/snapshot-<id>-<sha16>.png
       build_request: one user message [input_text, input_image (data URL), input_json (snapshot)],
                      questions action + crossover + evidence
       provider (Claude: image block + structured output with three enum fields; OpenAI; test double)
       check_rules(action) + chart_checks(crossover vs Python crossover, readable, evidence sufficient)
       decisions row: answers, questions, chart checks, image path + sha256 (request stored with a file reference)
  4. panel shows what was asked, the answers, the checks and eligibility. Paper tables are never written.
```

## Review path (Prompt 11)

```
decide(snapshot_id, provider, image?, review=True)
  analyst  : build_request (STRATEGY text, snapshot, image?, action [+ crossover, evidence])  -> provider.send  (call 1)
             -> status decided | malformed_answer | timeout | provider_error
  reviewer : only if the analyst decided
             build_review_request (REVIEWER_INSTRUCTIONS; same image; input_json {analyst: proposal + named answers,
             backend_facts + order_cost_at_close, snapshot: the SAME model_input}) -> provider.send  (call 2)
             -> reviewed (verdict) | malformed_answer | timeout | provider_error | not_run
  Python   : check_rules (account) + strategy_checks + chart_checks + reviewer valid + verdict == accept
             + application snapshot -> final_check {eligible, gates}; verdict_evidence (observed values)
  SQLite   : one decisions row: analyst request/response, reviewer_model, reviewer_status, verdict,
             review_json (reviewer request with the image as a file reference, response, latency, evidence),
             strategy_json, final_check_json, eligible_for_order. Paper tables untouched.
Each role: one HTTP call, DECISIONS_TIMEOUT_S timeout, max_retries = 0.
```

## Paper automation path (Prompt 12)

```
Vue AutomationPanel  Start -> POST /api/automation/start  (automation_state: enabled, generation + 1, account:
                                                           paper-test if the symbol's data is labelled test)
  every 2 s: GET /api/history -> newest completed candle not yet in automation_runs
    POST /api/automation/runs {candle_time}   claim: UNIQUE (account, strategy, symbol, interval, candle_ts);
                                              only candles completed after Start; snapshot at that candle
    render snapshot.chart off screen -> CandleChart.capture() -> PNG   (failure -> POST runs/{id}/fail)
    POST /api/automation/runs/{id}/analyse {image_png, capture}
        decide(review=True)  -> analyst + reviewer + final check (decisions row)
        same generation still enabled?  no -> run "stopped" (reply saved, not executed)
        apply_decision: eligible + verdict accept + same account/symbol
            buy/sell (order size) / close (whole position) -> execute(origin=automation, decision_id,
                request_id = auto:account:strategy:symbol:interval:candle) -> order pending
            hold/wait -> no order;  otherwise run "skipped" with the failed gates
next observation (live feed or TEST feed) -> paper.process_observation, per pending order, ONE transaction:
    cash / shares recheck (simulator rule) + FILL_CHECKS: not already filled, decision age <= DECISION_MAX_AGE_S,
    automation generation still enabled -> fill (paper_fills.decision_id) + balances + position + P&L row
    + ORDER_DECIDED (run "filled") ; or rejection reason (run "rejected")
Stop -> POST /api/automation/stop: enabled = 0; pending automation orders rejected
GET /api/automation/trace/{decision_id}: crossover -> proposal -> review -> final check -> order/fill -> account
```

## Voice path (Prompt 13)

```
VoicePanel: choose decision -> Start voice
  getUserMedia(audio)  (denied -> message, nothing to release)        "● Microphone active"
  POST /api/voice/sessions {decision_id}  -> voice.record(): READ-ONLY summary from SQLite
       snapshot (crossover, SMAs, account) + analyst + reviewer + final check
       + automation run / order / fill / P&L / account now, or "no simulated trade" and why
       -> voice_sessions row (summary_text frozen for the session)        (failure -> release mic)
  loop: SpeechRecognition (browser; Chrome uses Google's speech service) -> final text
        POST /api/voice/sessions/{id}/turns {text}
             -> Claude messages (system = instructions + summary; messages = earlier turns + question;
                no tools, no database access) -> reply text; voice_turns rows (user, assistant)
        speechSynthesis.speak(reply) -> listen again (follow-up in the same session)
  End voice / error: recognition.abort, speechSynthesis.cancel, every track.stop() (microphone released),
        POST /api/voice/sessions/{id}/end
Deviation from the prompt: no WebRTC audio stream to a model (the Claude API has no audio).
```

## SmokeTest path (Prompt 14)

```
python3 -m backend.smoke
  temp dir: smoke.sqlite <- write_sma_fixture (TESTSMA, source test)
  FastAPI :8000 (MARKET_DB=temp, LIVE_FEED=off, TEST_FEED=on, DECISIONS_PROVIDER=test, keys empty) + Vite :5173
  wait for GET :5173/api/health (database = temp)
  Playwright -> Chrome (headless)
    1 Replay TESTSMA -> add SMA3 -> values table @15:04 vs hand calc -> remove, add SMA2 -> @15:04, @15:06
    2 Live TESTSMA, SMA 2/3, cutoff 15:04 -> Ask about chart (POST /api/test/provider answers)
        screen answers / verdict / eligibility; SQLite response_json.received_images + received_snapshot
        (analyst and reviewer); stored PNG decoded in the browser; fills unchanged; then fail=provider_error
    3 TESTAUTO on the test clock (POST /api/test/clock + /api/test/observations), automation Start (size 4)
        m4 up: buy 4 -> fill 100; duplicate apply/claim; Stop, Start (size 1); m5 down: sell 1 -> fill 110;
        m6 up: veto; m7: hold (no order); m8 down: close 3 -> fill 105; screen + SQLite: $1,025, 0, $25, $0;
        Stop -> new candles -> no run, no order
  report.md / report.json (+ failure.png, logs, DB on failure); close browser, kill process groups, remove temp DB
  exit 0 (all pass) or 1
```

## SQLite schema

One database file: `backend/data/market.sqlite` by default, or `MARKET_DB` / `--db`. Every module creates its own
tables with `CREATE TABLE IF NOT EXISTS` when it first opens the database (`python3 -m backend.recorder init` creates the
market tables up front); columns added in later prompts are added with `ALTER TABLE` if missing, so an older file keeps
working. Times are epoch milliseconds UTC (`*_ms`); money is integer micro-dollars (`*_micros`, 1e-6 USD).
WAL journal mode, foreign keys on.

### Market data (Prompts 6–9)
| Table | One row per | Key columns | Uniqueness | Written by |
|---|---|---|---|---|
| `observations` | distinct message content | `id` (first-arrival order), `ticker`, `source_time_ms` (Yahoo `time`), `price`, `day_volume`, `last_size`, `source` (`yahoo` / `test`), `content_hash`, `raw_json`, `revises_id`, `first_received_ms`, `receipt_count` | `UNIQUE (ticker, content_hash)` | `recorder.record()` |
| `receipts` | message **arrival** | `seq` (receipt order), `observation_id`, `received_at_ms`, `is_repeat` | `seq` autoincrement | `recorder.record()` |

Candles are **not stored**: they are recomputed from `observations` (and, for replay, `receipts`) for any as-of time,
including whether each candle is complete. See the README section "Duplicates, revisions and out-of-order
observations".

### Indicators (Prompt 9B)
| Table | One row per | Key columns | Uniqueness | Written by |
|---|---|---|---|---|
| `indicator_configs` | named indicator instance | `symbol`, `name`, `type` (`sma`, `price_range`), `settings` (validated JSON) | `UNIQUE (symbol, name)` | `indicator_store` |

### Paper account and P&L (Prompts 9C–9D, 12)
| Table | One row per | Key columns | Uniqueness | Written by |
|---|---|---|---|---|
| `paper_accounts` | account (`paper`, `paper-test`) | `starting_cash_micros`, `cash_micros` (≥ 0) | `id` | `paper.open_account`, fills |
| `paper_positions` | account + symbol | `quantity` (≥ 0, long only) | `(account_id, symbol)` | fills |
| `paper_orders` | order | `request_id`, `side` (buy/sell/close), `quantity`, `status` (pending/filled/rejected), `reason`, `origin` (manual/automation), `decision_id` | `UNIQUE request_id`; one order per `decision_id` (partial unique index) | `paper.execute`, fills, automation |
| `paper_fills` | fill | `order_id`, `price_micros`, `observation_id` (the price used), `executed_ms`, `cash_after_micros`, `position_after`, `decision_id` | `UNIQUE order_id` | `paper.process_observation` (one transaction) |
| `paper_cost_basis` | account + symbol | `basis_micros`, `realized_micros` (weighted average cost) | `(account_id, symbol)` | `pnl.record_fill` |
| `pnl_history` | fill or valuation event | `event_key`, `event_type`, `price_micros`, `price_source`, `cash_micros`, `quantity`, `realized_micros`, `unrealized_micros`, `total_micros`, `valuation_status` | `UNIQUE event_key` | `pnl` |

### Decisions and review (Prompts 10–11)
| Table | One row per | Key columns | Uniqueness | Written by |
|---|---|---|---|---|
| `decision_snapshots` | frozen snapshot | `kind` (application/test), `symbol`, `interval`, `decision_ms`, `candle_ts`, `complete`, `snapshot_json` (candle, SMAs, crossover, account, chart section) | `id` | `decisions.create_snapshot` |
| `decisions` | decision (analyst + reviewer) | `snapshot_id`, `provider`, `model`, `call_kind` (live/mock), `status`, `proposal`, `validation_*`, `eligible_for_order`, `explanation`, `request_json` / `response_json` (images as file references), `questions_json`, `answers_json`, `chart_checks_json`, `image_path` + `image_json` (sha256), `reviewer_status`, `verdict`, `reviewer_model`, `review_json`, `strategy_json`, `final_check_json` | `id` | `decisions.decide` |

Chart captures are PNG files beside the database (`captures/snapshot-<id>-<sha>.png`, ignored by Git).

### Automation and voice (Prompts 12–13)
| Table | One row per | Key columns | Uniqueness | Written by |
|---|---|---|---|---|
| `automation_state` | strategy + symbol + interval | `account_id`, `short`, `long`, `order_size`, `enabled`, `generation`, `started_ms`, `stopped_ms` | `key` | `automation.start/stop` |
| `automation_runs` | analysed candle | `account_id`, `strategy`, `symbol`, `interval`, `candle_ts`, `generation`, `status`, `reason`, `snapshot_id`, `decision_id`, `order_id`, `fill_id` | `UNIQUE (account_id, strategy, symbol, interval, candle_ts)` | `automation` |
| `voice_sessions` | voice session | `decision_id`, `provider`, `model`, `call_kind`, `summary_text` (the read-only record), `ended_ms`, `end_reason` | `id` | `voice` |
| `voice_turns` | spoken question or reply | `session_id`, `role`, `text`, `model`, `latency_ms`, `status` | `id` | `voice` |
