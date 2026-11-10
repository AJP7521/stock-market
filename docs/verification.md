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
