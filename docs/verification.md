# Verification log

Each entry records the command, the expected result and the observed result.
Live-provider observations and mocked or test-data observations are labelled separately.

---

## Prompt 1 · Project structure and documentation (2026-11-10)

Setup decisions: the project root is `/Users/prest/Desktop/Claude/stock-market`.
`prompts/` was moved here unchanged from `live_market_workstation/prompts/`, and the
empty `live_market_workstation/` folder was removed. `git init` was run; nothing is
committed yet.

| # | Check | Command | Expected | Observed | Result |
|---|---|---|---|---|---|
| 1 | Operating system | `sw_vers; uname -m` | macOS, arch known | macOS 27.3 (26C5035r), arm64 | pass |
| 2 | Active Python | `python3 -c "import sys;print(sys.version, sys.executable)"` | Python 3.14, no venv | 3.14.7 at `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`; `VIRTUAL_ENV` and conda unset | pass |
| 3 | pip in that interpreter | `python3 -m pip --version` | pip present | pip 26.2.1 (python 3.14); only pip installed | pass |
| 4 | Built-in modules for Prompts 3 and 6 | `python3 -c "import csv,sqlite3,json; print(sqlite3.sqlite_version)"` | imports succeed | imports OK; SQLite 3.50.4 | pass |
| 5 | Node.js and npm | `node --version; npm --version` | Node present | v24.20.0 (arm64), npm 11.19.0; pnpm, yarn, bun, uv and conda not found | pass |
| 6 | Git | `git --version; git status` | repository initialised | git 2.54.0; empty repository, no commits | pass |
| 7 | Dependency manifests and lockfiles | `find . -name package*.json -o -name requirements*.txt -o -name pyproject.toml -o -name *.lock` | none yet | none found | pass |
| 8 | Third-party Python packages | `python3 -c "import fastapi"` (and `uvicorn`, `yfinance`, `openai`) | not yet needed | `ModuleNotFoundError` for all four | expected; install in later prompts |
| 9 | Sample data present | `head prompts/data/sample-candles.csv` | 6-field header and 3 rows | header `timestamp,open,high,low,close,volume`; rows at 14:30, 14:31 and 14:32 UTC on 2026-11-09 | pass |

### Checks still to run
- FastAPI, uvicorn and Vue/Vite install and startup checks (Prompt 2).
- No SmokeTest exists yet. It becomes possible once a UI exists (Prompt 4) and gets one command in Prompt 14.

---

## Prompt 2 · Connect Vue and FastAPI (2026-11-10)

Installed with student approval:
- `python3 -m pip install -r backend/requirements.txt`, which installed fastapi 0.162.4 and uvicorn 0.54.0 into the system Python 3.14 site-packages
- `npm install vue@3.5.45` and `npm install -D vite@8.3.7 @vitejs/plugin-vue@6.0.10` in `frontend/`. These created `package.json` and `package-lock.json`.

Services: backend `python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000`; frontend `cd frontend && npm run dev` (Vite on 127.0.0.1:5173, which forwards `/api` to 127.0.0.1:8000).

| # | Check | Command / URL | Expected | Observed | Result |
|---|---|---|---|---|---|
| 1 | Backend import and version | `python3 -c "import fastapi,uvicorn; ..."` | 0.162.4 / 0.54.0 | fastapi 0.162.4, uvicorn 0.54.0 | pass |
| 2 | Frontend packages | `npm ls --depth=0` | the three packages | @vitejs/plugin-vue 6.0.10, vite 8.3.7, vue 3.5.45 | pass |
| 3 | Frontend build | `npm run build` | build succeeds | `dist/assets/index-*.js` 63.67 kB; built in 35 ms | pass |
| 4 | Direct backend request | `curl http://127.0.0.1:8000/api/health` | 200, JSON status | 200 `{"status":"ok","service":"backend"}` | pass |
| 5 | Through the frontend server | `curl http://127.0.0.1:5173/api/health` | same as #4 | 200 `{"status":"ok","service":"backend"}` (identical) | pass |
| 6 | Backend stopped | `curl http://127.0.0.1:5173/api/health` | request fails | 502, empty body; Vite log `http proxy error: /api/health ... ECONNREFUSED 127.0.0.1:8000` | pass (proves it depends on FastAPI) |
| 7 | Backend restarted | `curl http://127.0.0.1:5173/api/health` | recovers | 200 `{"status":"ok","service":"backend"}` | pass |
| 8 | Browser view | open http://127.0.0.1:5173/ | page shows health | "Backend health: ok" | pass |
| 9 | Stop command | `kill $(lsof -tiTCP:8000 -sTCP:LISTEN) $(lsof -tiTCP:5173 -sTCP:LISTEN)` | ports freed | both ports free | pass |

Notes: pip offered 26.3 and npm offered 12.2.2. Neither was upgraded.

---

## Prompt 3 · Serve historical prices from a CSV file (2026-11-10)

Source: `prompts/data/sample-candles.csv` (supplied, **synthetic** one-minute candles), read with Python's built-in `csv` module in `backend/history.py` and served at `GET /api/history`. No packages were installed.

| # | Check | Command | Expected | Observed | Result |
|---|---|---|---|---|---|
| 1 | Endpoint responds | `curl http://127.0.0.1:8000/api/history` | 200, JSON array | 200, 3 candles | pass |
| 2 | Order and fields | the response | ordered by timestamp; exactly the six fields | 14:30, 14:31, 14:32; keys `timestamp,open,high,low,close,volume` on every row | pass |
| 3 | Row 2026-11-09T14:31:00Z against CSV line 2 | the response | 102 / 104 / 100 / 101 / 850 | open 102.0, high 104.0, low 100.0, close 101.0, volume 850; all six equal the CSV | pass |
| 4 | Ordering of out-of-order input | `load_candles()` on a temporary shuffled CSV | sorted ascending | 14:32, 14:30 in the file → 14:30, 14:32 returned | pass |
| 5 | Wrong header is rejected | `load_candles()` on a temporary CSV with header `time,...` | `ValueError` | `expected header [...] found ['time', ...]` | pass |

Representation: prices are returned as JSON floats (`102.0`), and volume as an integer number of shares in that minute.

---

## Prompt 4 · Display a candlestick chart in Vue (2026-11-10)

Installed with student approval: `npm install lightweight-charts@5.2.1` in `frontend/`, which changed `package.json` and `package-lock.json`. Its only dependency is fancy-canvas 2.1.0. Version 5.3.0 was not used because it was published on 2026-11-10 at 14:41 UTC, only hours earlier.

Data: the three **synthetic** one-minute candles from `GET /api/history` (Prompt 3), labelled "Synthetic one-minute sample" on screen.

| # | Check | Command / action | Expected | Observed | Result |
|---|---|---|---|---|---|
| 1 | Package resolves | `npm ls --depth=0`; `import('lightweight-charts')` in Node | 5.2.1 with v5 exports | 5.2.1; `createChart`, `CandlestickSeries`, `HistogramSeries` exported | pass |
| 2 | Frontend build | `cd frontend && npm run build` | builds | `index-*.js` 231 kB (79 kB gzip), built in 175 ms | pass |
| 3 | Time conversion | `toChartTime('2026-11-09T14:31:00Z')` | UTC seconds; formats back to 14:31 UTC | 1794234660, which formats back to `2026-11-09`, `14:31 UTC` | pass |
| 4 | Separate candles (browser, http://127.0.0.1:5173/) | screenshot | 14:30, 14:31 and 14:32 as three candles, each with a volume bar below | three candles (green, red, green) with aligned volume bars; time axis 14:30 / 14:31 / 14:32 | pass, after one fix (see below) |
| 5 | Inspect 14:31 (browser) | hover the 14:31 candle | close 101, volume 850 (CSV row 2) | legend: `2026-11-09 14:31 UTC O 102 H 104 L 100 C 101 Vol 850 shares`; axis label `09 Nov '26 14:31` | pass |
| 6 | Attribution | page links | TradingView notice and docs link | built-in logo link to tradingview.com, plus footer links to tradingview.com and the docs | pass |
| 7 | Console | browser console | no errors | no messages | pass |

AutoLoop cycle 1 (check 4): the first screenshot showed all candles squeezed against the right edge. Cause: `fitContent()` ran before `autoSize` had measured the container. Fix: run `fitContent()` in `requestAnimationFrame`. The rerun passed.

---

## Prompt 5 · Receive a Yahoo Finance update (2026-11-10)

Installed with student approval: `python3 -m pip install -r backend/requirements.txt`, after adding `yfinance==1.8.0`. All 20 packages came from prebuilt wheels into the system Python 3.14 site-packages.

| # | Check | Command | Expected | Observed | Result |
|---|---|---|---|---|---|
| 1 | Import and versions in the backend interpreter | `python3 -c "import yfinance, websockets, google.protobuf ..."` | importable | yfinance 1.8.0, websockets 17.4, protobuf 7.36.2 | pass |
| 2 | Time-limited **live** subscription (no login) | `python3 -m backend.yahoo_stream AAPL --timeout 60` (started 2026-11-10 19:16:02 UTC, 14:16 ET, regular session) | connection opens; one AAPL update, or receipt marked unverified | connection opened; update received after about 1.5 s | **pass (live)** |

One received update (**live Yahoo data**, not test data):

| Field | Value |
|---|---|
| ticker (`id`) | AAPL (exchange `NMS`) |
| price | 348.01 |
| source time (`time`) | `1794338162000` = 2026-11-10T19:16:02.000Z |
| backend receipt time | 2026-11-10T19:16:03.453Z (receipt − source = 1454 ms) |
| `day_volume` | `16325014` |
| `last_size` | `45` |
| `market_hours` | 1 |

**Time units:** `time` is int64 **milliseconds** since the Unix epoch, in UTC. The backend receipt time is taken with `datetime.now(timezone.utc)` when the handler runs. Yahoo's timestamp here is whole seconds (it ends in `000`).

**Volume:** `day_volume` is the **running total of shares traded in the current session**, not the size of this trade. 16.3 million shares by mid-afternoon is a session figure, while `last_size` = 45 is the size of the single latest trade. Prompt 7 turns the running total into per-minute volume by differencing.

**Format:** int64 fields arrive as **strings** (`MessageToDict` behaviour) and must be converted to integers before storage or arithmetic.

Still to verify: how the stream behaves outside the regular session, such as `market_hours` values and the gaps between messages.

---

## Prompt 6 · Record market observations in SQLite (2026-11-10)

Recorder: `backend/recorder.py`, using Python's built-in `sqlite3` (library SQLite 3.50.4). No packages were installed. The database is `backend/data/market.sqlite` (WAL mode), which Git ignores along with its `-wal`/`-shm` files. Override it with `--db` or `MARKET_DB`.

### How messages are classified
- **Exact repeat:** the same ticker and identical decoded content, matched by SHA-256 of the canonical JSON. No new observation is created. A receipt row with `is_repeat = 1` is added, and `receipt_count` goes up.
- **Changed message with the same source time:** stored as a **new** observation, and earlier rows are never updated. Yahoo's PricingData has no correction or sequence field, and `time` has one-second resolution, so the feed cannot establish a correction. Both rows are kept with `revises_id = NULL`. A caller that *can* identify a revision passes `revises_id`, and the earlier row stays unchanged.
- **Late arrivals:** `source_time_ms` (Yahoo's time) and `receipts.seq` / `received_at_ms` (the backend's arrival order) are stored separately.
- **Avoiding double counting:** read `observations` (distinct contents), not `receipts` (the arrival log).

### A. Disposable database, **constructed test data** (`source = 'test'`)
Database: `$SCRATCHPAD/test-recorder.sqlite`, created fresh and outside the project.

| # | Input | Expected | Observed | Result |
|---|---|---|---|---|
| 1 | AAPL 348.01 @ 19:16:02.000Z, received .453 | new observation | obs 1, seq 1, `is_repeat` false | pass |
| 2 | the identical message again, received .900 | repeat; no new observation | obs 1, seq 2, `is_repeat` true; `receipt_count` = 2 | pass |
| 3 | 348.05 (day_volume 16325114) with the **same** source time | new, separate observation | obs 2, seq 3; obs 1 unchanged | pass |
| 4 | Counts | 3 receipts, 2 distinct observations | `count(receipts)` = 3, `count(observations)` = 2 | pass |
| 5 | Late message with source 19:16:01, arriving after the others | ordered first by source time; last by arrival | obs 3, seq 4; `observations()` lists it first | pass |
| 6 | Identified revision of obs 2 (`revises_id = 2`) | new row linked to obs 2; obs 2 kept | obs 4 with `revises_id` 2; obs 2 still 348.05 | pass |
| 7 | Reopen: a new Python process (`python3 -m backend.recorder --db … show`) and the `sqlite3` CLI | same rows | identical rows, hashes and receipt order | pass |

### B. Project database, **live Yahoo data**
Commands: `python3 -m backend.recorder init`, then `python3 -m backend.recorder record AAPL --seconds 30` (14:18 ET).

| # | Check | Expected | Observed | Result |
|---|---|---|---|---|
| 8 | Live capture | messages stored | 30 messages: 30 new, 0 repeats; connected | **pass (live)** |
| 9 | Reopen in a new process (`show`, `sqlite3`) | the same 30 rows | 30 observations (19:18:19Z to 19:18:47Z, 348.165 to 348.2599), 30 receipts | pass |
| 10 | Shared source seconds | distinct messages kept | 6 source seconds have 2–5 distinct observations each (e.g. 19:18:32 has 5), all with `revises_id` NULL | pass; confirms the "retain both" rule |

Note: the restart check used fresh processes, because the recorder is not yet part of the FastAPI app. FastAPI integration starts with Prompt 8.

---

## Prompt 7 · Form candles from saved observations (2026-11-10)

Code: `backend/candles.py` (`build_minute_candles`, `minute_candles`, CLI `python3 -m backend.candles AAPL [--as-of ISO]`). Tests: `backend/tests/test_candles.py`, which uses stdlib `unittest` and **constructed test data** (ticker `TEST`, `source='test'`) in a temporary SQLite file that is deleted after each test. Aggregation rules are written in the `backend/candles.py` docstring and in `docs/design-pipeline.md`.

Command: `python3 -m unittest backend.tests.test_candles -v` → **7 tests, OK** (0.012 s).

| # | Test | Expected | Observed | Result |
|---|---|---|---|---|
| 1 | Prompt rows; as-of 15:00:30 (before the minute ends) | forming candle: 100 / 103 / 100 / 103, volume 1009 − 1000 = 9, `complete` false | as expected | pass |
| 2 | Same rows; as-of 15:01:00 (after the minute ends) | **15:00:00Z: open 100, high 103, low 100, close 101, volume 11**, `complete` true, 3 observations, exactly six fields | as expected | pass |
| 3 | Middle observation (15:00:20, 103) received late at 15:01:10 | as-of 15:01:05: high 101, otherwise the same; as-of 15:01:15: back to 100 / 103 / 100 / 101 / 11 | as expected; `last_received_time` 15:01:10.300Z | pass |
| 4 | Identified correction (`revises_id`) of the 103 price to 102, received 15:01:20 | high 103 before the correction is available, 102 after | as expected | pass |
| 5 | No observation before 15:00 in the session | volume `None` ("no earlier running total"); prices still formed | as expected | pass |
| 6 | Running total drops 1004 → 5 (reset) | 15:00 volume `None`; 15:01 volume 8 − 5 = 3 (baseline restarts) | as expected | pass |
| 7 | Running total unchanged (1000 → 1000) | volume **0**, known (not `None`) | as expected | pass |
| 8 | Next trading session (Nov 10) after a Nov 9 observation | first minute's volume `None` (no baseline across sessions) | as expected | pass |

**Live-data spot check** (the 30 live AAPL observations from Prompt 6): `python3 -m backend.candles AAPL` → `2026-11-10T19:18:00Z O 348.255 H 348.2599 L 348.165 C 348.23 V None`, complete, n = 30. Volume is correctly unknown, because the capture began mid-session with no earlier running total. The first minute of every capture will show unknown volume.

---

## Prompt 7A · Derive larger candles from one-minute data (2026-11-10)

Code:
- `backend/market_calendar.py`: NYSE regular-session rules using only the standard library. It covers holidays, Good Friday, 13:00 early closes, a list of special closures, and DST through `zoneinfo`.
- `backend/timeframes.py`: `aggregate(minutes, timeframe, as_of_ms)`.
- `backend/app.py`: `GET /api/history?symbol=SAMPLE&timeframe=1m|5m|15m|1h|1d&as_of=ISO`. The response is `{symbol, timeframe, as_of, source, candles: [six fields], status: [...], excluded_minutes}`.
- `frontend/src/App.vue`: now reads `.candles`.

No packages were installed. I chose standard-library calendar rules over a calendar package. Unannounced closures have to be added to `SPECIAL_CLOSURES` by hand.

Data: the three **synthetic** sample minutes; 14:35 and next-session minutes are **constructed test data**.

Command: `python3 -m unittest discover -s backend/tests -t . -v` → **21 tests, OK** (7 from Prompt 7 and 14 new).

| # | Check | Expected | Observed | Result |
|---|---|---|---|---|
| 1 | 5m from the 3 sample minutes | 14:30Z: O 100, H 105, L 99, C 104, V 3550 | identical; coverage 3/5 (partial) | pass |
| 2 | 1d from the sample | timestamp 14:30Z (09:30 EST open), V 3550, partial day | identical; coverage 3/390 | pass |
| 3 | Add a test minute at 14:35 | second 5m candle at 14:35; the same daily candle | 5m: 14:30Z and 14:35Z, the first still 3550; 1d: one candle, H 106, C 105, V 4250, 4 minutes | pass |
| 4 | Add a test minute on the next session (2026-11-10 14:30Z) | new daily candle | daily candles 2026-11-09T14:30Z and 2026-11-10T14:30Z (V 400) | pass |
| 5 | 15m and 1h alignment to the 09:30 open | 15m 15:30/15:45/20:45Z; 1h 15:30Z and 20:30Z (15:30 ET, 30 min to close) | as expected | pass |
| 6 | Revised minute (same timestamp, later) | replaces the earlier one | V = 1200 + 900 + 1500; still 3 minutes | pass |
| 7 | Unknown volume vs zero vs coverage | `None` + `volume_known` false, separate from a known 0 | as expected | pass |
| 8 | as_of 14:32:00Z (replay/live) | 14:32 minute not yet available | C 101, V 2050, `complete` false | pass |
| 9 | Minute outside the regular session | excluded from 1d, counted | `excluded_minutes` 1, V 3550 | pass |
| 10 | 2026 calendar | NYSE 2026 holidays; early closes Nov 27 and Dec 24; DST open 14:30Z → 13:30Z on Mar 9, back on Nov 2 | as expected; Nov 27 close 18:00Z; 2025-01-09 closed; 2027-12-31 open | pass |
| 11 | Early close shortens groups (2026-11-27) | 1d spans 210 min; last 1h 30 min | as expected | pass |

HTTP checks through the Vite proxy (`curl http://127.0.0.1:5173/api/history?...`):

| Query | Observed | Result |
|---|---|---|
| `timeframe=5m` | `{"timestamp":"2026-11-09T14:30:00Z","open":100.0,"high":105.0,"low":99.0,"close":104.0,"volume":3550}`; status: 3 of 5 minutes, `coverage_complete` false | pass |
| `timeframe=1d` | same values; 3 of 390 minutes; `interval_end` 21:00Z | pass |
| `timeframe=5m&as_of=2026-11-09T14:32:00Z` | C 101, V 2050, `complete` false | pass |
| `timeframe=2m` | HTTP 422, listing the allowed values | pass |
| `symbol=AAPL&timeframe=1m` (recorded **live** data) | 19:18Z candle, `volume: null`, `volume_known` false | pass |
| Browser http://127.0.0.1:5173/ | chart still shows the three sample candles; no console errors | pass |

---

## Prompt 8 · Display live updates on the existing chart (2026-11-10)

**What was traced before editing:**
- Chart: `frontend/src/CandleChart.vue` and `chartAdapter.js`.
- Yahoo subscription: `backend/yahoo_stream.py`, which uses yfinance `AsyncWebSocket`.
- Observation → candle: `recorder.record()` → `candles.minute_candles()` → `timeframes.aggregate()`.

**Dependencies:** none installed. uvicorn 0.54 serves WebSockets with the `websockets` 17.4 package that came with yfinance. Lightweight Charts 5.2.1 supports `series.update(bar, historicalUpdate)`.

**Changes:**
- `backend/live.py` (new): `SymbolFeed` and `LiveHub`. It reuses yfinance `AsyncWebSocket.subscribe()` and its decoder, but runs its own receive loop. yfinance 1.8.0's `listen()` reconnects by reusing the closed socket and would spin on a clean close, so it can't report the feed state. Every message is recorded with `recorder.record()`.
- `backend/app.py`:
  - `WS /api/ws/candles?symbol&timeframe` sends `{type: "candle", symbol, interval, candle: {six fields}, status, feed}` for every new observation, and `{type: "feed", …}` every second and on each change.
  - `GET /api/live/{symbol}` returns feed status.
  - `POST /api/live/{symbol}/disconnect` and `/connect` are test controls.
  - Live symbols use as-of "now", so the latest candle shows as forming.
- `frontend/src/App.vue`:
  - symbol field (default AAPL) and timeframe selector (default 1m)
  - a status bar showing symbol, interval, source, age of the last source observation, feed status, Browser ↔ FastAPI connection state, and whether the latest candle is forming or complete
- `CandleChart.vue`: `upsert()` updates a candle and its volume bar in place, or appends the next candle. Unknown volume is drawn as no bar and shown as "unknown" in the legend.
- **Fix found during this check (AutoLoop cycle 1):** the first live screen showed 19:28 volume 292,136. The baseline came from the 19:18 capture, so the shares traded during the 10-minute gap were counted in one minute. `candles.py` now requires the baseline to come from the immediately preceding minute, and otherwise reports unknown ("gap in observations"). A new test (`test_volume_unknown_after_gap`) brings the suite to **22 tests, OK**.

### Live checks (Yahoo AAPL, regular session, 2026-11-10 ≈ 19:29 UTC / 14:29 ET), browser at http://127.0.0.1:5173/

| # | Check | Expected | Observed | Result |
|---|---|---|---|---|
| 1 | Page loads, 1m by default | live feed, forming candle | Feed `live`, Browser ↔ FastAPI `open`, latest candle `forming`, age 4–5 s | pass |
| 2 | **Follow one Yahoo update from SQLite to the screen** | on-screen 19:29 candle = the same candle computed from SQLite | Last stored record: observation **67**, source **19:29:11Z**, price **348.33**, day_volume 16694660, received 19:29:16.393Z. Screen at 19:29:16.569Z: `2026-11-10 19:29 UTC O 348.355 H 348.355 L 348.33 C 348.33 Vol 7234`. SQL over the same minute: O 348.355, H 348.355, L 348.33, C 348.33, V 7234, 10 observations | **pass (live)** |
| 3 | Switch to 5m | matching history and live updates, same symbol | 19:25 candle O 348.23 H 348.36 L 348.23 C 348.35, `forming · 2/5 min`, volume unknown (19:28 follows a gap), identical to `/api/history?symbol=AAPL&timeframe=5m` | pass |
| 4 | **Disconnect upstream** (`POST /api/live/AAPL/disconnect` at 19:29:40Z) with the page open | feed status changes while the browser socket stays open | Feed: `paused (upstream disconnected on request)`; Browser ↔ FastAPI: `open`; age kept growing (11 s); 0 observations recorded while paused | pass |
| 5 | Reconnect (`POST /api/live/AAPL/connect`, 19:29:51Z) | returns to live | log: connecting 19:29:51.254 → live 19:29:52.389; screen: `live`, age 0 s, new high 348.43 | pass |
| 6 | Switch to SAMPLE | synthetic data, no live feed; AAPL feed released | Feed `offline (synthetic CSV sample; no live feed)`; 5m 14:30 = 100/105/99/104/3550; `/api/live/AAPL` → `not running` | pass |
| 7 | Console and backend log | no errors | none | pass |

**Not yet exercised:**
- **Unrequested drop:** the `disconnected` path, when the upstream drops by itself (network loss, Yahoo closing the connection), is coded with a 5 s retry but was only exercised through the `paused` test control.
- **`quiet` state:** shown after 30 s without messages, for example after the close. It was not observed.

---

## Prompt 9 · Replay a recorded session from SQLite (2026-11-10)

**Code:**
- `backend/replay.py` (new):
  - opens the database with `file:…?mode=ro`
  - steps through the selected symbol's arrivals in `receipts.seq` order, filtered by source time in [start, end)
  - at each step runs the existing `candles.build_minute_candles` → `timeframes.aggregate` as of that arrival's receipt time, so late arrivals and revisions show up at the step where they originally arrived
- `backend/app.py`:
  - `GET /api/replay/sessions`
  - `GET /api/replay?symbol&start&end&timeframe`
  - `LIVE_FEED=off` starts the backend with no Yahoo connection; `/api/health` reports `live_feed`
- `frontend/src/App.vue`: Live/Replay mode buttons. Replay has symbol, From/To in UTC prefilled from the recording, steps per second, Start, Pause and Skip to end, and a status bar with the step number, replay clock (receipt time), source time, price, and late/revision/repeat flags. The chart is labelled "Replay · AAPL · 1m". An empty selection shows a message.
- `CandleChart.vue`: the chart refits when a new candle is appended.

No packages were installed.

### Unit tests (constructed **test data**, disposable DB)
`python3 -m unittest discover -s backend/tests -t . -v` → **27 tests, OK** (5 new in `test_replay.py`):
- **Order and late arrival:** steps follow receipt order. The late 15:00:20 observation, received at 15:01:10.3, changes the 15:00 candle at step 5 from high 101 to 100/103/100/101/11.
- **Repeat:** an exact repeat is a step but changes nothing.
- **Revision:** an identified revision changes the high to 102.5 at its own step.
- **Repeatable and read-only:** two runs give identical results; the DB file hash is unchanged; a write through the replay connection raises `OperationalError` (read-only).
- **Empty selection:** returns `empty: true`.

### Recorded **live** session replayed with the live feed off
- **Backend:** `LIVE_FEED=off python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000`. `/api/health` returned `"live_feed": false`, and the log shows no feed activity.
- **Selection:** AAPL, 2026-11-10T19:18:00Z → 19:31:00Z, 1m; 115 observations / 115 receipts (Yahoo, recorded in Prompts 6 and 8).
- **Chosen interval:** 2026-11-10T19:29:00Z.
- **Expected, from SQLite (read-only):** close = the last price by source time in 19:29 = **348.42**. Volume = last running total 16708017 − baseline 16687426 (at 19:28:47Z, in the preceding minute) = **20591**. There are 40 observations in the minute.

| # | Check | Expected | Observed | Result |
|---|---|---|---|---|
| 1 | Replay run 1 in the browser (50 steps/s) | "Replay" label; observation time shown; 19:29 close 348.42, volume 20591 | "Replay · AAPL · 1m"; progress 115 / 115; replay time 19:30:19.051Z; source 19:30:17.000Z; legend at 19:29: `O 348.355 H 348.43 L 348.32 C 348.42 Vol 20591` | **pass** |
| 2 | Replay run 2 (same selection) | same result | identical legend, 115 / 115, same replay time | pass |
| 3 | API response, run 1 vs run 2 | identical | both SHA-256 `0b976057…a7ef`; observation_count 115, receipt_count 115 | pass |
| 4 | Stored counts and files before vs after | unchanged | observations 115 → 115; receipts 115 → 115; `market.sqlite` SHA-256 `af252936…c6f4` before and after (same for `-shm`/`-wal`) | pass (read-only) |
| 5 | Last candle at the end of the replay | 19:30 still forming at replay time 19:30:19Z | `forming` | pass |
| 6 | Empty selection (20:00–20:05Z) | message | "AAPL has no recorded observations between 2026-11-10T20:00:00.000Z and 2026-11-10T20:05:00.000Z." | pass |
| 7 | Console and backend log | no errors | none | pass |

**Still requiring an actual capture:** the recorded live session contains no repeats, late arrivals or revisions (0 of 115). Those behaviours are verified only with test data. Seeing them in a real capture needs a session where Yahoo actually delivers one.

**First-run fix:** before the fix, the first replay candle sat at the right edge because the chart didn't refit when a candle was appended. `upsert()` now calls `fitContent()` for new candles.

---

## Prompt 9A · Calculate and display two simple moving averages (2026-11-10)

**Code:**
- `backend/indicators.py` (new): `validate_windows` requires positive whole numbers with short < long. `sma_crossover` averages the closes of the last N **completed** candles; an unfinished candle gets no value and no signal. The first comparable pair sets a side, a tie keeps the last side, and only a change of side is a signal. Comparisons use exact `Fraction(Decimal(str(close)))` arithmetic; floats are used only in the response.
- `backend/app.py`: `GET /api/indicators/sma?symbol&timeframe&short&long&as_of`. `GET /api/replay` accepts `short`/`long`, and each replay step then carries the SMA computed from only the candles available at that step.
- `backend/fixtures.py` (new): `python3 -m backend.fixtures sma` writes the labelled **test** symbol `TESTSMA` to `backend/data/test-sma.sqlite`, a separate file that Git ignores. Closes are 13, 10, 7, 13, 19, 7, 1 (one observation per minute from 15:00Z on 2026-11-09), plus a forming 15:07 observation at 30 that *would* cross upward if it were counted.
- `frontend`: SMA controls (on/off, Short, Long, Apply, crossover list); two line series (short blue, long orange) with gaps during warm-up; `createSeriesMarkers` arrows ("Cross up" below the bar, "Cross down" above it); SMA values and signal in the hover legend, rounded only for display. Live mode recalculates at most once per second; replay applies each step's SMA.

No packages were installed.

### Unit tests (**test data**)
`python3 -m unittest discover -s backend/tests -t . -v` → **33 tests, OK** (6 new in `test_indicators.py`).

| # | Test | Expected | Result |
|---|---|---|---|
| 1 | Full run over 7 closes, windows 2/3 | c1 —/—; c2 11.5/—; c3 8.5/10 (side below, no signal); c4 10/10 tie; c5 16/13 **up**; c6 13/13 tie; c7 4/9 **down** | pass |
| 2 | Reveal one candle at a time (k = 1…7) | each partial run's points equal the full run's first k points, warm-up gaps included; signals collected = full run's | pass |
| 3 | Forming 8th candle (close 30) | no value and no signal; the same close as a *completed* candle would give "up" | pass |
| 4 | Exact comparison | closes 0.1, 0.1, 0.2, 0.15 with windows 1/3: exact tie at candle 4 keeps "above". Naive float (0.1+0.2+0.15)/3 = 0.15000000000000002 would invent a "down" | pass |
| 5 | Window validation | 0, −1, 3/3, 4/3, 2.5, "3" rejected; 2/3 accepted | pass |
| 6 | Replay of the TESTSMA fixture, step by step | 8 steps; at step k only k−1 candles are complete, and their values equal the hand calculation; up appears from step 6, down from step 8; 15:07 stays forming with no signal | pass |

### Browser (app run against the **labelled fixture**: `MARKET_DB=backend/data/test-sma.sqlite LIVE_FEED=off`)
Replay TESTSMA 15:00:00Z → 15:08:00Z, 1m, SMA Short 2 / Long 3, 1 step per second:

| # | Check | Expected | Observed | Result |
|---|---|---|---|---|
| 1 | Mid-replay, step 5/8 (replay time 15:04:30.3Z) | 15:04 still forming, so no value and no crossover yet | legend at 15:04: `C 19 … SMA2 — SMA3 —`; "Crossovers: none" | pass |
| 2 | End, 8/8 (15:07:10.3Z) | two markers; 15:07 forming without a marker | "Crossovers: up 15:04Z, down 15:06Z"; **two arrows on the chart** ("Cross up" under 15:04, "Cross down" over 15:06); 15:07 `forming`, SMA — | pass |
| 3 | Hover each candle | hand calculation | 15:00 —/—; 15:01 11.5/—; 15:02 8.5/10; 15:03 10/10; 15:04 **16/13 ▲ cross up**; 15:05 13/13; 15:06 **4/9 ▼ cross down** | pass |
| 4 | Short = 0 → Apply | readable error | "Replay request failed: short window must be a positive whole number, got 0" | pass |
| 5 | API validation | 422 errors | short=0 → "short window must be a positive whole number, got 0"; 3/3 → "short window (3) must be smaller than long window (3)"; 2.5 → integer parsing error | pass |
| 6 | `/api/indicators/sma?symbol=TESTSMA&short=2&long=3&as_of=2026-11-09T15:07:10.300Z` | same values as the replay | 15:00–15:06 as in #3; 15:07 None | pass |
| 7 | Console and backend log | no errors | none | pass |

Note: without `as_of`, `/api/indicators/sma` for TESTSMA uses the current time. The 15:07 minute (price 30) is then complete and correctly produces a third, upward crossover at 15:07.

---

## Prompt 9B · Create and configure a chart indicator (2026-11-10)

### The indicator interface (`backend/indicators.py`)
- **Inputs:** completed candles for one symbol and interval (the six fields and `complete`), plus settings validated against the type's schema (`validate_settings`: whole numbers between `minimum` and `maximum`, defaults applied, unknown keys and types rejected).
- **Units:** declared per type. `sma` is in price (quote currency, the candles' scale); `price_range` is a price difference (quote currency).
- **Output:** exactly one point per candle, `{timestamp, value}`. `value` is null for a warm-up gap or a forming candle.
- **Adding a type:** write `compute(candles, settings)` and register it in `INDICATOR_TYPES`. FastAPI lists types at `GET /api/indicator-types`.
- **SMA reuse:** `sma` uses the same helper (`_sma_exact`) as the Prompt 9A crossover, which now builds both of its averages from it. A test confirms the values are identical.
- **Rolling price range:** the highest high minus the lowest low of the last N completed candles. Exact arithmetic; drawn in a separate pane below the candles.

### Storage and API
- **Storage:** `backend/indicator_store.py` keeps the table `indicator_configs(id, symbol, name, type, settings JSON, created_ms)` in the market database (`MARKET_DB`). Names are unique per symbol, and invalid settings are never stored.
- **Endpoints:** `GET /api/indicator-configs?symbol`, `POST /api/indicator-configs` (201, or 422 with a message), `DELETE /api/indicator-configs/{id}`, `GET /api/indicator-values?symbol&timeframe&as_of`. Replay steps include the configured indicators, computed from the candles available at each step.
- **Vue:** `IndicatorPanel.vue` handles type choice, name, settings generated from the schema, Add and Remove, and shows errors. The chart draws one line per configuration, in the price pane or a second pane, with values in the hover legend. A table lists the latest candles with each indicator's value. The chart's symbol and interval are kept in the URL (`?symbol=…&interval=…`), so a refresh reopens the same chart.
- **Source label fix:** the status bar called test-data symbols "Yahoo Finance (recorded in SQLite)". It now reads the `source` column, and TESTRANGE shows "TEST data (labelled fixture, SQLite)".

No packages were installed.

### Unit tests (**test data**)
`python3 -m unittest discover -s backend/tests -t . -v` → **40 tests, OK** (7 new in `test_indicator_types.py`): rolling range values (windows 1, 2, 3), one point per candle with a forming-candle gap, SMA type equal to the crossover's averages, settings validation (0, −2, 2.5, "3", null, an unknown key, an unknown type), type descriptions, fixture candles, and stored configs surviving a new connection with a window of 0 and a duplicate name rejected.

### Browser, against the **labelled fixture** (`python3 -m backend.fixtures range`; `MARKET_DB=backend/data/test-range.sqlite LIVE_FEED=off`)
Fixture TESTRANGE: candles 15:00, 15:01, 15:02Z on 2026-11-09 with (high, low) = (12, 9), (15, 10), (13, 11), from 4 test observations each.

| # | Action | Expected | Observed | Result |
|---|---|---|---|---|
| 1 | Load TESTRANGE; add "Range 2" (Rolling price range, window 2) and "Range 3" (window 3) | both listed | "Range 2 · Rolling price range (window 2)", "Range 3 · Rolling price range (window 3)" | pass |
| 2 | Values at the third candle (15:02) | Range 2 = **5**, Range 3 = **6** | legend: `15:02 UTC O 12 H 13 L 11 C 12 … Range 2 5 Range 3 6`; table row 15:02Z: 13 / 11 / 12 / **5** / **6**; 15:01Z: Range 2 = 6, Range 3 —; 15:00Z: — / — | pass |
| 3 | Refresh the page (`location.reload()` on `?symbol=TESTRANGE&interval=1m`) | both configurations return | the same two indicators listed; values 5 and 6 again | pass |
| 4 | Add "Range 0" with window 0 | error explained; valid indicators kept | "Not added: Window (candles) must be at least 1, got 0"; list and values unchanged (Range 2, Range 3) | pass |
| 5 | SQLite after the checks | two rows | `1 TESTRANGE Range 2 price_range {"window": 2}`, `2 TESTRANGE Range 3 price_range {"window": 3}` | pass |
| 6 | Duplicate name through the API | 422 | "TESTRANGE already has an indicator named 'Range 2'" | pass |
| 7 | Console and backend log | only the intended 422s | two 422s: the Range 0 attempt, and the Short = 0 check from the 9A session; backend log clean | pass |

---

## Prompt 9C · Buy, sell, and close a simulated position (2026-11-10)

### Design
- **`backend/paper.py`**, one execution function: `execute(conn, account, symbol, action, quantity, request_id, submitted_ms, origin)` serves the Vue buttons now and the decision agent later. `buy N`, `sell N` and `close` create an order; `hold` (keep a position) and `wait` (stay in cash) create **no order** and return a note.
- **Fill rule:** an accepted order stays `pending` until the **next observed price**, the first observation of that symbol *received after* submission. It fills at that price with **zero fees**. Observations are processed only as they arrive, so the same rule holds with the replay clock (`submitted_ms`).
- **Checks at submission:** quantity must be a positive whole number. A sale can't exceed the shares held minus shares reserved by pending sales. Close needs an open position. A failed check is stored as a `rejected` order with its reason.
- **Checks at fill:** cash (buy) or shares (sell/close) are checked again. A failure rejects the order at that point with the reason.
- **One SQLite transaction** per fill updates the order status, the fill row (price, `observation_id`, observation time, execution time, cash and position after), cash and position.
- **Idempotency:** `request_id` is UNIQUE. A retry returns the original order (and fill) with `duplicate: true` and creates nothing.
- **Money:** integer micro-dollars (exact); the API converts to dollars.
- **Tables** (in the market database): `paper_accounts`, `paper_positions`, `paper_orders`, `paper_fills`. The account `paper` is created with `PAPER_STARTING_CASH` (default 1000).
- **Price sources:** live Yahoo observations (`live.py` → `fill_paper_orders`) and, for testing only, `POST /api/test/observations` (enabled with `TEST_FEED=on`; records `source='test'`). The page header shows "TEST price feed on".
- **Vue (`PaperPanel.vue`):** cash, shares of the chart symbol, starting cash and fill count; Quantity, Buy, Sell, Close position, Hold, Wait, and *Retry last request* (re-sends the same `request_id`). The order history shows status, fill price, observation, execution time, cash after and reason.
- **Source label fix:** a symbol with no observations yet is labelled "SQLite (no observations recorded yet)" instead of "Yahoo Finance".

No packages were installed.

### Unit tests (**test data**, disposable DB)
`python3 -m unittest discover -s backend/tests -t . -v` → **49 tests, OK** (9 new in `test_paper.py`):
- **Sequence:** buy 4 @ 100 → 600 / 4; sell 1 @ 110 → 710 / 3; close @ 105 → 1025 / 0. Fills record observation ids 1–3 and cash and position after.
- **Persistence:** the same state after reopening the database.
- **Retry:** the same `request_id` while pending and after the fill returns the original order; one order and one fill.
- **Empty account:** selling or closing is rejected with a reason.
- **Invalid quantities:** 0, −1, 2.5, "3", null and true are rejected; an unknown action raises an error.
- **Hold/wait:** no order.
- **Timing:** a price received before submission does not fill.
- **Recheck at fill:** cash is checked again (4 × 300 > 1000 → rejected).
- **Reservations:** pending sales reserve shares.

### Browser (disposable DB: `MARKET_DB=$SCRATCHPAD/paper-demo.sqlite LIVE_FEED=off TEST_FEED=on`), symbol TESTPAPER, starting cash $1,000
Prices were supplied with `curl -X POST http://127.0.0.1:8000/api/test/observations -d '{"symbol":"TESTPAPER","price":…}'` (**test data**).

| # | Browser action, then price | Expected | Observed on screen | Result |
|---|---|---|---|---|
| 1 | Quantity 4 → **Buy** | pending, cash unchanged | "Order #1 buy 4 TESTPAPER: accepted: pending until the next observed price"; $1,000.00, 0 shares | pass |
| 2 | price $100 (observation #1) | cash $600, 4 shares | **$600.00, 4 shares**; #1 filled $100.00, obs #1 | pass |
| 3 | Quantity 1 → **Sell**; price $110 (#2) | cash $710, 3 shares | **$710.00, 3 shares** | pass |
| 4 | **Close position**; price $105 (#3) | cash $1,025, 0 shares | "Order #3 close 3 …"; **$1,025.00, 0 shares** | pass |
| 5 | **Retry last request** (the close) | no new order or fill | "Order #3 close 3 TESTPAPER: duplicate request_id: returning the original order"; still 3 order rows; cash unchanged | pass |
| 6 | **Sell** 1 from the empty position | rejection with reason | "Order #4 sell 1 TESTPAPER: rejected: cannot sell 1 TESTPAPER: only 0 shares available" | pass |
| 7 | **Restart the backend**, reload the page | exactly 3 fills persist | header "Cash $1,025.00 · TESTPAPER shares 0 · fills 3"; 3 filled rows; `/api/paper/account` fill_count 3, cash 1025 | pass |

**SQLite compared with the dashboard** (direct `sqlite3` query on the disposable DB):
- `paper_orders`: 1 buy 4 filled · 2 sell 1 filled · 3 close 3 filled · 4 sell 1 rejected ("cannot sell 1 TESTPAPER: only 0 shares available").
- `paper_fills`: (order 1, buy, 4, 100.0, obs 1, cash after 600.0, position 4) · (order 2, sell, 1, 110.0, obs 2, 710.0, 3) · (order 3, close, 3, 105.0, obs 3, 1025.0, 0).
- `paper_accounts`: cash 1025.0, start 1000.0. `paper_positions`: TESTPAPER 0. `observations`: 100 / 110 / 105, all `source = test`.

These match the dashboard row for row. The backend logs show no errors. The browser console showed 502s from the account polling while the backend was restarting, plus the 422s left from the earlier 9A and 9B checks.

**Not yet exercised:** paper orders filled by a **live** Yahoo price (the hook is in `live.py`, but this check used the test feed), and orders placed during replay (the replay UI does not trade; automation comes in Prompt 12).

---

## Prompt 9D · Record realized and unrealized profit and loss (2026-11-10)

### Design (`backend/pnl.py`)
- **When rows are written:**
  - **Fill:** a row after each fill, inside the fill's SQLite transaction, valued at the fill price.
  - **Valuation:** a row when a **completed one-minute candle** gives a new price for an open position, valued at its close. It is checked when the next observation arrives (before that observation's fills), so it is ordered before any fill the observation causes. A candle that closed before the latest fill, or one still forming, does not value.
- **What a row links to:** account, symbol, event (`fill:<id>` or `valuation:<symbol>:<candle start>`), event time, price, price source and price time.
- **Weighted average cost:** a buy adds qty × price to the basis. A sale realizes qty × (price − average cost), and the remaining shares keep the average cost. Unrealized = shares × price − basis. Total = cumulative realized + unrealized. Fees are zero.
- **Money:** integer micro-dollars. When a sale's share of the basis does not divide evenly, it is rounded to the nearest micro-dollar.
- **Single entry per event:** `pnl_history.event_key` is UNIQUE (`INSERT OR IGNORE`), and the cost basis changes only when a new fill row is inserted. Reprocessing the same event leaves one row.
- **Unavailable price:** an open position without a usable price gets a row labelled `unavailable`, with unrealized and total NULL. Earlier rows are only ever added to, never changed.
- **Tables:** `paper_cost_basis` (basis and cumulative realized per account and symbol) and `pnl_history`.
- **API:** `GET /api/paper/pnl?symbol=`.
- **Vue:** a "Profit and loss history" table in the paper panel with event, time, price, price source, cash, shares, average cost, realized, unrealized and total. A missing valuation shows "unavailable" in red.

### Unit tests (**test data**, disposable DB, explicit times)
`python3 -m unittest discover -s backend/tests -t . -v` → **55 tests, OK** (6 new in `test_pnl.py`):
- **The prompt's five rows,** with price sources (fill #, observation #, "1m candle close 15:00Z / 15:01Z") and average cost 100.
- **Reprocessing:** `record_fill` again for every fill, every observation replayed, and a later valuation run leave the rows unchanged; 3 fills.
- **Reopening:** rows are identical after reopening the database.
- **Partial sale:** buy 3 @ 100 and 1 @ 104 (average 101), sell 2 @ 105 → realized 8, the remaining 2 keep average 101, unrealized 8.
- **Unavailable:** labelled, with the fill row kept.
- **Forming candle:** creates no valuation row.

### Browser, real clock, disposable DB (`MARKET_DB=$SCRATCHPAD/pnl-demo.sqlite LIVE_FEED=off TEST_FEED=on`), symbol TESTPNL, start $1,000
Orders were placed with the paper panel's buttons. Prices were **test data** posted to `/api/test/observations`, timed so each candle closed at the required price:
- 23:31 — Buy 4; prices 100, then 110.
- 23:32 — Sell 1; prices 110, then 95.
- 23:33 — Close; price 105.

| # | Expected (Event / Cash / Shares / Realized / Unrealized / Total) | On screen | SQLite `pnl_history` | Result |
|---|---|---|---|---|
| 1 | Buy 4 at $100 / 600 / 4 / 0 / 0 / 0 | buy 4 at 100 · $600.00 · 4 · $0.00 · $0.00 · $0.00 (fill #1, obs #1) | fill · 600.0 · 4 · 0.0 · 0.0 · 0.0 · ok | pass |
| 2 | Value at $110 / 600 / 4 / 0 / 40 / 40 | value 4 shares at 110 · 23:32:00 · "1m candle close 2026-11-10T23:31:00Z" · $600.00 · 4 · $0.00 · $40.00 · $40.00 | valuation · 110.0 · 600.0 · 4 · 0.0 · 40.0 · 40.0 | pass |
| 3 | Sell 1 at $110 / 710 / 3 / 10 / 30 / 40 | sell 1 at 110 · $710.00 · 3 · $10.00 · $30.00 · $40.00 (fill #2, obs #3) | fill · 710.0 · 3 · 10.0 · 30.0 · 40.0 | pass |
| 4 | Value at $95 / 710 / 3 / 10 / −15 / −5 | value 3 shares at 95 · 23:33:00 · "1m candle close 2026-11-10T23:32:00Z" · $710.00 · 3 · $10.00 · −$15.00 · −$5.00 | valuation · 95.0 · 710.0 · 3 · 10.0 · −15.0 · −5.0 | pass |
| 5 | Close 3 at $105 / 1,025 / 0 / 25 / 0 / 25 | close 3 at 105 · $1,025.00 · 0 · $25.00 · $0.00 · $25.00 (fill #3, obs #5) | fill · 1025.0 · 0 · 25.0 · 0.0 · 25.0 · no position | pass |

- **Before vs after restart:** the backend was restarted and the page reloaded. The screen showed the same five rows, and the SQLite query output was identical before and after (`diff` empty). 5 P&L rows, 3 fills, observations all `source = test`.
- **Discrepancies:** none. The backend logs show no errors.
- **Not exercised in the browser:** the `unavailable` valuation, which needs an open position without any usable price. It is covered by a unit test.

---

## Prompt 10 · Decision API: frozen snapshot plus model proposal (2026-11-12)

### Provider choice
The prompt describes an OpenAI implementation (`POST /v1/decisions` with a named choice question). The student chose to
use a **Claude API key**. Both providers are implemented behind one provider-neutral request (`build_request`), and
`DECISIONS_PROVIDER` selects one. **Claude is the default** (unset, `claude` or any value other than `openai`) →
`ClaudeDecisions` (Messages API, structured output whose only field is `action` ∈ {buy, sell, hold, wait, close});
`DECISIONS_PROVIDER=openai` → `OpenAIDecisions`. The student uses Claude only; the optional OpenAI path is unit-tested
but has **not** been called live (no OpenAI key).

### CHECK → TAKE ACTION → VERIFY (`anthropic`)
| Step | Command | Expected | Observed |
|---|---|---|---|
| CHECK | `python3 -c "import anthropic"` | not installed | `ModuleNotFoundError` |
| CHECK | `python3 -m pip index versions anthropic` | latest version | 2.8.0 |
| TAKE ACTION (approved by the student) | `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pip install anthropic==2.8.0` | installs into system Python 3.14 site-packages | installed; `backend/requirements.txt` gained `anthropic==2.8.0` |
| VERIFY | `python3 -c "import anthropic; print(anthropic.__version__)"` | `2.8.0` | `anthropic 2.8.0` |

### Unit tests (**test data**, **mocked/controlled providers**, disposable DB)
`python3 -m unittest discover -s backend/tests -t .` → **70 tests, OK** (15 in `test_decisions.py`; rerun after making
Claude the default, with an added check that an unset `DECISIONS_PROVIDER` selects Claude):
- Fixed snapshot: TESTSMA fixture as of 15:05:10Z freezes the 15:04 candle (close 19), SMA2 16 / SMA3 13, crossover up,
  cash 1000, position 0; as of 15:04:40Z it uses the 15:03 candle (replay uses only data received by then).
- A clearly labelled `kind = test` snapshot can be decided but is never eligible for an order.
- Valid hold (open position) and wait (flat) are decisions; hold/sell while flat are `invalid`.
- An incomplete snapshot (SMA warm-up) → `missing_input`, no provider call.
- Malformed answers (value outside the five, no `action` answer, wrong shape, unparsable Claude text) → `malformed_answer`.
- **Controlled provider failures with the real HTTP clients** (OpenAI urllib client and the `anthropic` SDK client):
  a local socket that accepts but never answers → `timeout`; nothing listening → `provider_error` "connection error";
  no key → `provider_error` naming the missing variable, with no request sent.
- Claude request translation: schema enum is exactly the five actions, `additionalProperties: false`, the snapshot JSON
  is in the user message, `fallbacks: "default"`.
- The paper tables are identical before and after a decision.

### Live provider request (**live model call** on **test market data**)
Backend: `MARKET_DB=$SCRATCHPAD/decisions-live.sqlite LIVE_FEED=off python3 -m uvicorn backend.app:app --port 8010`,
the DB created with `write_sma_fixture` (labelled TESTSMA test observations). `.env`: `DECISIONS_PROVIDER=claude`,
`ANTHROPIC_API_KEY` set (only its length, 106, was checked).

1. `POST /api/decisions/snapshots {"symbol":"TESTSMA","short":2,"long":3,"as_of":"2026-11-09T15:05:10Z"}` →
   snapshot 1, `application`, complete, market data "TEST data (labelled fixture, SQLite)", candle 15:04 close 19,
   SMA2 16 > SMA3 13, crossover up, paper cash 1000, position 0, order size 1.
2. `POST /api/decisions {"snapshot_id":1}` → one call to `claude-opus-5-5`:

| Field | API response | SQLite `decisions` row 1 | Match |
|---|---|---|---|
| provider / model / call kind | anthropic / claude-opus-5-5 / live | anthropic / claude-opus-5-5 / live | yes |
| status | decided | decided | yes |
| model proposal | buy | buy | yes |
| validation | valid ("cash covers 1 share(s) at the candle close ($19.00)": cash $1,000.00) | valid | yes |
| eligible for order | true | 1 | yes |
| explanation | identical text (backend facts, then "Model proposal (claude-opus-5-5): buy. Rule check passes.") | identical | yes |
| latency | 4519 ms | 4519 | yes |

- Stored raw response: served model `claude-opus-5-5` (no fallback block), `stop_reason` `end_turn`, text `{"action":"buy"}`,
  861 input / 11 output tokens, request id recorded.
- Paper account untouched: `paper_orders` 0, `paper_fills` 0.
- The key string does not appear in the SQLite file, the uvicorn log or the saved API response (`grep -c` = 0 each).
  The log has no errors. The backend was stopped afterwards (port 8010 free).

**Not exercised:** a live OpenAI request (no OpenAI key); a live timeout or refusal (covered only by controlled tests);
decisions on live Yahoo data; any UI (chart capture comes in Prompt 10A).

---

## Prompt 10A · Ask the decision API about a chart screenshot (2026-11-12)

### CHECK (no install needed)
- Lightweight Charts 5.2.1 (already in `frontend/package.json`) has `IChartApi.takeScreenshot(addTopLayer, includeCrosshair)`
  returning a canvas (`node_modules/lightweight-charts/dist/typings.d.ts:1767`). Used with `addTopLayer = true` so the
  crossover markers (a series primitive) are included.
- The PNG check uses only the Python standard library (`base64`, `struct`, `zlib`). No package or manifest changed.
- Provider: Claude (the default since Prompt 10). Claude has no `/v1/decisions` endpoint, so the provider-neutral
  request (input_text + input_image + input_json in one user message, three named questions) becomes one Claude user
  message `[text, image (base64 PNG), snapshot JSON text, questions text]` and a structured output with three enum fields.

### Unit tests (**test data**, generated PNGs, **provider test double**)
`python3 -m unittest discover -s backend/tests -t .` → **82 tests, OK** (12 new in `test_chart_decisions.py`):
- PNG check: a valid PNG passes with width, height, bytes, sha256; rejected: invalid base64, non-PNG media type,
  wrong signature, damaged chunk (CRC), truncated file, empty, pixel data shorter than the header promises.
- Snapshot at the 15:04 TESTSMA candle: decision time 15:05:00Z, close 19, crossover up, chart = 15:00–15:04 with the
  15:04 up crossover; `chart_candles = 3` shows 15:02–15:04 while SMA3 at 15:02 still uses earlier candles; a forming or
  unknown candle makes the snapshot incomplete.
- Test double: received `input_text, input_image, input_json` in one user message and the three questions; the PNG it
  received, the stored file and the original bytes are identical; the stored request holds `stored:<path> sha256:<…>`
  instead of base64; all checks pass, eligible, `call_kind = mock`, no paper orders.
- Ineligible but `decided`/`valid`: crossover conflict (none vs up), unreadable, missing crossover, missing evidence,
  insufficient evidence, out-of-range crossover value; each names the failed check in the explanation.
- Missing action → `malformed_answer`. Unreadable image, mismatched capture (cutoff) and a snapshot without a chart
  section → `ImageRejected` before any provider call; no decision stored.
- Text-only decisions (Prompt 10) still ask one question and have no image.
- Claude translation with an image: one message, blocks `text, image, text, text`, `media_type image/png`, schema
  properties `action, crossover, evidence`, crossover enum `up, down, none, unreadable`.

### Browser SmokeTest (built-in browser; **test data**; **provider test double**)
Services: `MARKET_DB=$SCRATCHPAD/smoke10a/smoke.sqlite LIVE_FEED=off DECISIONS_PROVIDER=test
DECISIONS_TEST_ANSWERS=action=buy,crossover=up,evidence=sufficient python3 -m uvicorn backend.app:app --port 8000` and
`npx vite` (5173). The DB holds the TESTSMA fixture (all `source = test`) plus one configured indicator, Range2
(`price_range`, window 2). The browser pane was hidden, so the tab was given a 1280×900 viewport for the run.

| # | Step | Expected | Observed | Result |
|---|---|---|---|---|
| 1 | Open `?symbol=TESTSMA&interval=1m`, SMA 2/3, Apply | recorded crossovers listed | "Crossovers: up 15:04Z, down 15:06Z, up 15:07Z" (15:07 has completed on the real clock) | pass |
| 2 | Cutoff 15:04 UTC, 60 candles, **Ask about chart** | preview with symbol, interval, cutoff | "TESTSMA · 1m · cutoff 2026-11-09 15:04 UTC · snapshot #1 · 5 candles 15:00 → 15:04 UTC · decision time 2026-11-09T15:05:00.000Z · market data: TEST data (labelled fixture, SQLite)" | pass |
| 3 | Inspect the captured PNG | chart only, through the cutoff | 1596×800 PNG (device pixel ratio 2): candles, volume, blue SMA2 and orange SMA3, "Cross up" arrow at 15:04, Range2 pane; no legend, controls or page; last candle 15:04 | pass |
| 4 | Test double received a decodable PNG matching the saved capture | same sha256 | received `decodable: true, 1596×800, 70297 bytes, sha256 94f3698c…`; stored file and `image_json` the same sha256; `GET /api/decisions/1/image` → 200 image/png, same bytes | pass |
| 5 | Displayed answers vs SQLite | equal | screen: action buy (✓ cash covers 1 share), crossover up ("Python calculated up: match"), evidence sufficient, "Eligible for automated execution", "MOCK (provider test double)"; `decisions` row 1: test-double/mock, decided, buy, valid, eligible 1, answers `{"action": "buy", "crossover": "up", "evidence": "sufficient"}` | pass |
| 6 | Order count | unchanged | `paper_orders` 0, `paper_fills` 0 | pass |
| 7 | Unreadable image (browser PNG export forced to return a cut-off data URL, then the button clicked) | rejection with what to recapture | panel: "the image is not valid base64 (Incorrect padding) / What to do: Recapture the chart with the Ask about chart button…"; no decision row added (still 1) | pass |
| 8 | API: truncated PNG; capture with interval 5m and cutoff 15:03 for snapshot 1 | 422, nothing stored | "the PNG is truncated (chunk b'IHDR' runs past the end)"; "the capture does not match the snapshot: interval '5m' (snapshot: '1m'); cutoff …" with "Recapture TESTSMA 1m with the cutoff at 2026-11-09T15:04:00Z (snapshot 1)." | pass |

### Live image request (**live model call** on **test market data**)
The backend was restarted on the same disposable DB without `DECISIONS_PROVIDER`, so `.env` selected Claude
(`anthropic`, `claude-opus-5-5`, live). In the browser: cutoff 15:04 → **Ask about chart**.

| Field | Screen | SQLite `decisions` row 2 | Match |
|---|---|---|---|
| snapshot / decision | snapshot #3, decision #2 | snapshot_id 3, id 2 | yes |
| model call | "live (anthropic, claude-opus-5-5, 2868 ms)" | anthropic / claude-opus-5-5 / live / 2868 | yes |
| action | buy, ✓ cash covers 1 share(s) at the candle close ($19.00) | proposal buy, validation valid | yes |
| crossover | up, "Python calculated up: match" | answers crossover up | yes |
| evidence | sufficient | answers evidence sufficient | yes |
| eligibility | "Eligible for automated execution" | eligible_for_order 1 | yes |

- Claude's actual reply text: `{"action":"buy","crossover":"up","evidence":"sufficient"}`, `stop_reason end_turn`, no
  fallback block, 3015 input / 28 output tokens.
- The capture was byte-identical to the test-double run (same sha256 `94f3698c…`): the frozen snapshot renders
  deterministically. Stored request: `stored:captures/snapshot-3-94f3698cbdd82a78.png sha256:…`.
- Paper account untouched (`paper_orders` 0, `paper_fills` 0). The key does not appear in the DB or any log; the
  backend log has no errors. Backend, Vite and the browser tab were stopped/closed; ports 8000 and 5173 free.

**Not exercised / notes:**
- The SmokeTest was driven step by step in the built-in browser; the one-command SmokeTest is Prompt 14.
- A live OpenAI image request (no OpenAI key); replay-mode use of the panel (it passes the replay time as `as_of`, but
  was not run in replay).
- The chart shows the dashboard's own "Cross up" markers, so the model can read the crossover from the marker as
  well as from the lines.

---

## Prompt 11 · Second-agent review of a proposed decision (2026-11-12)

### CHECK
No package needed: the reviewer reuses the provider client (`ClaudeDecisions`, `OpenAIDecisions` or the test
double), the provider-neutral request/response format and the `decisions` row. Change: the Claude client now uses
`max_retries = 0` (was 1), so each role makes at most one HTTP call.

### Unit tests (**test data**, **provider test double** with fixed replies)
`python3 -m unittest discover -s backend/tests -t .` → **90 tests, OK** (8 new in `test_review.py`):
- **Case 1, agreed eligible proposal** (`action=buy, verdict=accept`, $1,000): exactly 2 calls; the analyst was asked
  `action`, the reviewer `verdict` with different instructions; the reviewer received the analyst's choice `buy`, the
  backend facts (order cost 19.0) and a snapshot identical to the analyst's; final check eligible; evidence
  "cash $1,000.00 ≥ cost of 1 share(s) at close $19.00 = $19.00"; SQLite row has the snapshot ID, both model names,
  verdict and reviewer status; 0 orders.
- **Case 2, reviewer rejection** (`reject_unsupported_signal`): account rules valid, ineligible; the only failed gate
  is "reviewer accepted".
- **Case 3, unaffordable buy accepted by both** ($10 test account, close $19): validation invalid, ineligible; the only
  failed gate is the cash rule; evidence "cash $10.00 < cost of 1 share(s) at close $19.00 = $19.00".
- Reviewer timeout (1 call), out-of-list verdict and missing verdict → ineligible.
- Strategy rule enforced when both accept: `wait` on an up crossover while flat → ineligible.
- No proposal (analyst gave no action) → reviewer `not_run`, only 1 call.
- Chart question: the reviewer got the identical image part and the analyst's named answers; the stored reviewer
  request holds the image as a file reference.
- `review=False` keeps Prompt 10 behaviour (one call, no review sections).

### Fixed replies through the HTTP API (**test data**, **mocked provider**)
Disposable DB with the TESTSMA fixture; one backend start per case with `DECISIONS_PROVIDER=test`; snapshot as of
2026-11-09T15:05:10Z (candle 15:04, close 19, SMA2 16 > SMA3 13, crossover up); `POST /api/decisions {"snapshot_id"}`.

| Case | Settings | Saved row (analyst / reviewer / final) | Failed gates | Result |
|---|---|---|---|---|
| 1 agreed eligible | `action=buy,verdict=accept`, account `paper` $1,000 | decision 1, snapshot 1: buy valid / accept reviewed / eligible 1 | none | pass |
| 2 reviewer rejection | `action=buy,verdict=reject_unsupported_signal`, `paper` $1,000 | decision 2, snapshot 2: buy valid / reject_unsupported_signal / eligible 0 | reviewer accepted | pass |
| 3 unaffordable buy, both accept | `action=buy,verdict=accept`, account `poor` $10 | decision 3, snapshot 3: buy invalid / accept reviewed / eligible 0 | account rule: cash covers 1 share(s) at the candle close ($19.00) | pass |

Evidence saved for case 3: "cash $10.00 < cost of 1 share(s) at close $19.00 = $19.00". All rows `call_kind = mock`,
model `test-double`. `paper_orders` 0, `paper_fills` 0.

### Live two-call example (**live model calls** on **test market data**)
Backend on the same disposable DB with Claude from `.env`; in the browser (1280×900 viewport, pane hidden):
TESTSMA, SMA 2/3, cutoff 15:04, **Ask about chart**.

| | Analyst (call 1) | Reviewer (call 2) |
|---|---|---|
| provider / model / kind | anthropic / claude-opus-5-5 / live | anthropic / claude-opus-5-5 / live |
| actual reply text | `{"action":"buy","crossover":"up","evidence":"sufficient"}` | `{"verdict":"accept"}` |
| stop / tokens in-out / latency | end_turn / 3015–28 / 2644 ms | end_turn / 2936–13 / 2577 ms |
| snapshot received | identical to saved snapshot 4 | identical to saved snapshot 4 |
| image | stored capture (reference) | the same reference |

- Saved snapshot 4: candle 2026-11-09T15:04:00Z close 19, SMA2 16 / SMA3 13, crossover up, cash 1000, position 0.
  Both outputs agree with it: buy on an up crossover, affordable; crossover answer up = Python's.
- The reviewer received the analyst's `{'proposal': 'buy', 'named_answers': {'action': 'buy', 'crossover': 'up',
  'evidence': 'sufficient'}}`.
- Screen: "Verdict accept · model call: live (anthropic, claude-opus-5-5, 2577 ms)", evidence lines, final-check table
  with all 11 gates passing, "Eligible for automated execution". SQLite decision 4: verdict accept, reviewer_status
  reviewed, final eligible true, eligible_for_order 1. No fallback blocks. 0 orders, 0 fills.
- The key does not appear in the DB or logs. Backend, Vite and the browser tab were stopped/closed.

**Not exercised:** a live rejection or live timeout (covered by mocked tests); a live OpenAI review.

---

## Prompt 12 · Apply a reviewed decision to the paper account (2026-11-13)

### Trace before the change
One saved decision: completed candle → `create_snapshot(candle_time)` (crossover, cash, position) → analyst proposal
→ reviewer verdict → `final_check` (Prompt 11). Simulator: `paper.execute()` creates a pending order;
`paper.process_observation()` fills it at the next observed price in one transaction (cash, position, order status,
`paper_fills`, `pnl.record_fill`). No package needed.

### Unit tests (**test data** TESTAUTO with explicit times, **provider test double**)
`python3 -m unittest discover -s backend/tests -t .` → **102 tests, OK** (12 new in `test_automation.py`):
- Approved buy on the 15:04 up crossover → order pending with the decision_id → next price 20 fills it: `paper-test`
  cash $980, 1 share, fill `decision_id` = the decision, one P&L fill row, run `filled`; trace crossover up → buy →
  accept → eligible → fill 20 → $980 / 1; the main `paper` account unchanged.
- Same decision applied again, then the database reopened (restart), the candle claimed again and the decision applied
  again → `duplicate`, still 1 order, 1 fill, $980, 1 share.
- Vetoed proposal (reject_unsupported_signal) → run `skipped` ("reviewer accepted" failed), no order, $1,000 / 0.
- Stop while the reviewer reply is pending → run `stopped`, decision saved (eligible) but no order.
- Stop with a pending order → order rejected "automation stopped before the fill"; a new generation also blocks it.
- Fill 360 s after the candle completed → rejected "decision too old at fill" (limit 300 s).
- Approved wait → `no_order`, no order row.
- Claiming while stopped, or a candle that completed before Start → refused.
- Chart unavailable (fail_run) or unreadable image → `failed_analysis`, nothing executed.
- A manual (Ask about chart) decision → `apply_decision` refuses: no order.

### Browser SmokeTest, attempt 1 (real clock, **test prices**, **mocked provider**) — FAILED
Services: `MARKET_DB=$SCRATCHPAD/smoke12/smoke.sqlite LIVE_FEED=off TEST_FEED=on DECISIONS_PROVIDER=test
DECISIONS_TEST_ANSWERS=action=buy,crossover=up,evidence=sufficient,verdict=accept` + Vite; a feeder posted TESTAUTO
test prices 13, 10, 7, 13, 19 at hh:mm:03 of consecutive minutes from 03:28, then 21 at 03:33:30, 5 at 03:34:05.
- Start was pressed at 03:27:31, before the first test price existed, so automation chose the main `paper` account.
  Stopped immediately (no run had been created). **Fix:** `claim_run` routes again when test data is present.
- After restarting and pressing Start at 03:28:09 (`paper-test`): the 03:30 run waited ~3 min in the chart capture.
  The browser pane was hidden, so `requestAnimationFrame` did not run and the capture (and the chart's auto-size)
  never completed; it finished at 03:33:53 when a script I ran made the page draw a frame. Because the panel
  processed only the newest completed candle, the 03:31 candle was never analysed, and the buy order for the 03:32
  crossover was submitted at 03:33:54, after the 21 price, so it filled at 5 (03:34:05) instead: cash $995, 1 share.
  Fill rules themselves behaved correctly (next price after submission, decision_id on the fill, age 65 s).
- Evidence kept in `$SCRATCHPAD/smoke12/attempt1/`.

**AutoLoop cycle 1:** the capture chart now has a fixed 800×400 size (laid out without waiting for a frame), each
frame wait falls back to 50 ms, the capture has a 15 s limit (a timeout records a failed analysis), and the panel
processes every unhandled candle completed since Start, oldest first.

### Browser SmokeTest, attempt 2 (real clock, **test prices**, **mocked provider**) — PASSED
Fresh disposable DB; the same feeder from 03:36:03; Start at 03:36:19 after the first price (account `paper-test`,
starting cash $1,000; main account `paper` $1,000). The pane stayed hidden and was not touched while candles closed.

| Candle (UTC) | Crossover (Python) | Analyst / reviewer (mock) | Outcome | Account after |
|---|---|---|---|---|
| 03:36, 03:37 | – | – | failed analysis: SMA warm-up | $1,000 / 0 |
| 03:38 | none | buy / accept | skipped: strategy allows wait; chart crossover conflict (captured in 43 ms) | $1,000 / 0 |
| 03:39 | none | buy / accept | skipped (same) | $1,000 / 0 |
| 03:40 | **up** | buy / accept | eligible → order #1 submitted 03:41:02 → **filled at 21** (observation #6, 03:41:30) | **$979 / 1** |
| 03:41 | none (position 1) | buy / accept | skipped: strategy allows hold | $979 / 1 |
| 03:42 | **down** (position 1) | close / **reject_unsupported_signal** | **vetoed**: skipped, no order | **$979 / 1** |

- Expected after the approved decision: 1 fill, cash 1000 − 21 = $979, position 1. Observed in SQLite and on screen:
  `paper_fills` 1 (order 1, price 21, cash after 979.0, position 1, decision_id 3), `pnl_history` fill row
  (979.0, 1), `paper-test` $979.00 / 1 share; `paper` still $1,000.00.
- **Same approved decision twice:** `POST /api/automation/apply {"decision_id": 3}` ×2 → both "decision 3 was already
  handled (filled)", order 1. Expected and observed: 1 order, 1 fill, $979, 1 share.
- **Restart:** the backend was restarted (with vetoing replies) and the page reloaded at 03:42:15–03:42:27:
  automation still enabled from SQLite (generation 1), 6 runs, none repeated (max one run per candle).
- **Vetoed proposal:** decision 5 (close on the down crossover) rejected by the reviewer → skipped; account unchanged
  ($979 / 1, 1 fill).
- **Trace on screen** for decision 3: "Crossover: candle 2026-11-13 03:40 UTC closed 19; SMA2 16 vs SMA3 13: up ·
  account then: $1,000.00, 0 shares" → "Analyst (MOCK, test-double): buy" → "Reviewer (MOCK, test-double): accept ·
  … cash $1,000.00 ≥ cost of 1 share(s) at close $19.00 = $19.00" → "Python final check: eligible" → "Execution:
  order #1 buy 1 · filled · filled at 21 (observation #6, 2026-11-13T03:41:30.097Z) · cash after $979.00, position 1"
  → "P&L row: buy 1 at 21" → "Account now (paper-test): $979.00, 1 TESTAUTO shares, 1 fills". Decision 5: "down …
  close … reject_unsupported_signal … not eligible (reviewer accepted) … no order · skipped". Both match SQLite.
- The captured PNG for 03:38 shows the candles, both SMA lines and volume (rendered while the pane was hidden).
- **Stop** → "Stopped", `automation_state.enabled = 0`. Services stopped; ports 8000 and 5173 free.
- Observed: two `GET` requests at backend start-up on the brand-new DB failed with "database is locked" in
  `recorder.connect` (`PRAGMA journal_mode=WAL`, two connections at once); the next poll succeeded. Pre-existing,
  not changed here.

All provider responses in this prompt were **mocked** (test double); no live model call was needed or made.

**Not exercised in the browser:** Stop while a model reply is pending (unit test only), a decision too old at fill
(unit test only), automation on live Yahoo data, sell/close fills by automation.

---

## Prompt 13 · Discuss a recorded decision by voice (2026-11-13)

### CHECK and the provider decision
- The prompt specifies OpenAI GPT-Live over WebRTC. The student uses Claude only. The Claude features overview
  (`platform.claude.com/docs/en/build-with-claude/overview.md`, fetched 2026-11-13) lists no audio input/output or
  realtime/WebRTC feature. The student chose **Claude + the browser's speech** (Web Speech API).
- No package was installed: `anthropic` 2.8.0 is already present; `SpeechRecognition`, `speechSynthesis`,
  `getUserMedia` are built into Chrome. No manifest changed.
- **Deviations recorded:** no audio is streamed to a model over WebRTC; each turn is speech → text (browser) →
  Claude → speech (browser). Chrome's speech recognition sends the audio to Google's speech service. Safari support
  is partial, Firefox has none; the panel says so when recognition is missing.

### Unit tests (**test data**, **voice test double**, no audio)
`python3 -m unittest discover -s backend/tests -t .` → **110 tests, OK** (8 new in `test_voice.py`):
- Record of a filled automation decision contains crossover up, proposal buy, verdict accept, "mocked test double",
  TEST, "Fill: 1 share(s) at 20.0", cash after $980.00, position 1, final check eligible, account now.
- Vetoed decision: "No paper order was placed for this decision, so no simulated trade happened." and the failed gate.
- Manual (Ask about chart) decision: "manual question … no simulated trade happened", no reviewer.
- Two questions in one session: the second call's history is user/assistant/user with the same frozen summary;
  4 turns stored; asking after End is refused; orders, fills, decisions and P&L rows unchanged (read-only).
- A failed reply is stored and left out of the next call's history. The instructions require naming missing evidence.
- No API key → error; unknown decision → error.

### Browser checks in the built-in browser (**MOCKED** microphone, speech recognition and speech output)
DB: a copy of the Prompt 12 SmokeTest DB (decision 3: buy, accept, filled at 21 → $979 / 1 share; decision 5: close,
reject_unsupported_signal, skipped). Backend `DECISIONS_PROVIDER=test` (voice test double), `LIVE_FEED=off`.
| Check | Mocks | Expected | Observed | Result |
|---|---|---|---|---|
| Flow, decision 3 | fake mic stream (AudioContext), fake SpeechRecognition "hearing" the two questions, stubbed `speak` | mic shown active, two replies in one session, End releases the mic | "● Microphone active"; both replies spoken (second reply "turns so far 3"); after End: "Microphone off (released)", track `ended`; session 1 ended, 4 turns, call_kind mock | pass |
| Denied microphone | `getUserMedia` rejects `NotAllowedError` | message, no session, nothing held | "Microphone access was denied. Allow the microphone for this page and press Start voice again."; idle; no new session | pass |
| Failed connection | fake mic granted; backend stopped | mic released, message | "Could not start the voice session (backend unreachable or rejected): HTTP 502"; "Microphone off (released)", track `ended` | pass |

### Live session (**live Claude**, real microphone and speaker, the student's Google Chrome on localhost)
Backend with Claude (`anthropic`, `claude-opus-5-5`, live) on the same DB. The agent decisions in this record were made
by the **test double** on **test prices**; Claude only described them. Transcripts from `voice_turns`; the reply text
is exactly what `speechSynthesis` read aloud.

**Decision 3** (record: up crossover; analyst buy; reviewer accept; eligible; order 1 filled at 21; $979, 1 share).
- Session 2: recognition heard noise ("nope something oh to who"); reply: "Sorry, I didn't catch that question. …
  Could you ask again?" The student ended the session and started session 3.
- Session 3, Q "what did the agents decide for the symbol" → "For TESTAUTO, which used test data, the analyst agent
  proposed a buy because the short moving average had crossed above the long one, and the reviewer agent accepted
  that proposal. Python then confirmed it was eligible, and the paper account bought one share at twenty-one dollars,
  leaving nine hundred seventy-nine dollars in cash." (2743 ms) — **matches** the record.
- Q "what happened to the paper account" → "… the paper account started with one thousand dollars and no shares, then
  a paper order for one TESTAUTO share filled at twenty-one dollars, the next observed price rather than the
  nineteen-dollar candle close. That left nine hundred seventy-nine dollars in cash and one share, with realized and
  total profit and loss both at zero right after the fill." (2796 ms) — **matches** (P&L fill row: realized 0, total 0).

**Decision 5** (record: down crossover, SMA2 13 < SMA3 15; analyst close; reviewer reject_unsupported_signal; no order).
- Q "what do the agents decide for this symbol" → "On this test data, the analyst agent proposed closing the one share
  of TESTAUTO because the short average of thirteen had crossed below the long average of fifteen. The reviewer agent
  rejected that as an unsupported signal, so Python's final check failed for lack of reviewer acceptance. No paper
  order was placed, and the account still holds one share with nine hundred seventy-nine dollars in cash." — **matches**;
  the student reported hearing this text.
- Q "what happened to the paper account" → "Nothing changed in the paper account because of this decision, since no
  paper order was placed and no simulated trade happened. The account still shows nine hundred seventy-nine dollars in
  cash and one share of TESTAUTO. It has one fill in total, but the record says that count can include other
  decisions, so it isn't from this one." — **accurately describes the no-trade outcome**; the student reported hearing it.

All four live sessions ended with "ended by the student" (End voice). No errors in the backend log; the key does not
appear in the DB or logs. Services stopped.

**Not confirmed / not exercised:** the student's report of Chrome's microphone indicator after End (release was
verified with the mocked track state); a real permission denial in Chrome (mocked only); the speech-recognition
`network` error path.
