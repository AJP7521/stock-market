# Live Market Workstation

A single local, AI-assisted market workstation and paper-trading bot. It is built
as a course exercise: the student directs a coding agent, inspects the generated
code, tests the running application, and explains the design decisions.

## Goals

The finished workstation should let a student:

- load one-minute candles from a CSV file and from a live Yahoo Finance feed,
  store observations in SQLite, and derive 5-minute, 15-minute, hourly and daily candles
- view candles, volume and indicators (SMAs, rolling range) on an interactive chart,
  live or in replay
- trade a local, long-only **paper** account and read its realized/unrealized P&L
- ask an AI analyst about a chart snapshot, have a second agent review the proposal,
  and optionally apply approved decisions to the paper account
- discuss a recorded decision by voice
- run the whole workstation through one repeatable SmokeTest

No real money or brokerage account is involved at any stage.

## Technology stack

| Layer | Technology | Status |
|---|---|---|
| Backend logic | Python 3.14 (system interpreter, no virtual environment) | available |
| Transport | FastAPI 0.162.4 (HTTP + WebSocket), served by uvicorn 0.54.0 | installed (`backend/requirements.txt`) |
| Storage | SQLite via Python's built-in `sqlite3` | available (SQLite 3.50.4) |
| Frontend | Vue 3.5.45, built with Vite 8.3.7 and @vitejs/plugin-vue 6.0.10 | installed (`frontend/package.json`, lockfile) |
| Charting | TradingView Lightweight Charts 5.2.1 | installed (`frontend/package.json`, lockfile) |
| Market data | yfinance 1.8.0 `AsyncWebSocket` (websockets 17.4, protobuf 7.36.2) | installed (`backend/requirements.txt`) |
| AI provider | OpenAI API (key kept in the backend environment) | **not installed** – Prompt 10 |

## Prerequisites found on this computer (2026-11-10)

| Tool | Version / location |
|---|---|
| OS | macOS 27.3 (build 26C5035r), Apple Silicon (arm64) |
| Python | 3.14.7 at `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3` |
| pip | 26.2.1 (installs into the Python 3.14 framework site-packages) |
| Node.js | v24.20.0 at `/usr/local/bin/node` |
| npm | 11.19.0 (pnpm, yarn, bun, uv and conda are not installed) |
| Git | 2.54.0 (Apple Git-157) |

Third-party packages are installed only after the student approves them (see
`AGENTS.md`, *Tools and packages*).

## Folder map

```
stock-market/
|-- README.md                 Project purpose, prerequisites, and setup
|-- AGENTS.md                 Standing instructions for the coding agent
|-- .gitignore
|-- backend/                  Python application logic and FastAPI routes
|   |-- app.py                FastAPI app (GET /api/health, GET /api/history?symbol=&timeframe=&as_of=)
|   |-- history.py            CSV loader for one-minute candles
|   |-- yahoo_stream.py       Yahoo Finance stream receiver (time-limited check)
|   |-- recorder.py           SQLite recorder for stream observations
|   |-- candles.py            One-minute candles from stored observations
|   |-- timeframes.py         5m/15m/1h/1d candles from one-minute candles
|   |-- market_calendar.py    NYSE regular sessions (holidays, early closes, DST)
|   |-- tests/                unittest checks (see Tests below)
|   |-- data/market.sqlite    Local database (created by init; not in Git)
|   `-- requirements.txt      Pinned Python dependencies
|-- frontend/                 Vue components, user input, and presentation
|   |-- package.json          npm manifest (package-lock.json pins exact versions)
|   |-- vite.config.js        Dev server on 5173, forwards /api to 127.0.0.1:8000
|   |-- index.html
|   `-- src/                  main.js, App.vue, CandleChart.vue, chartAdapter.js
|-- docs/
|   |-- design-pipeline.md    File responsibilities and how data moves
|   `-- verification.md       Repeatable checks and observed results
|-- prompts/                  Numbered course prompts (01 … 14) and data/sample-candles.csv
`-- handoffs/
    |-- create-handoff.md     Instructions for ending and resuming a session
    `-- current.md            Current project state, written at handoff
```

## Setup (once)

From the project root:

```bash
python3 -m pip install -r backend/requirements.txt
```

```bash
cd frontend && npm install
```

## Run instructions (tested 2026-11-10)

Start each service in its own terminal, from the project root.

Backend (FastAPI on http://127.0.0.1:8000):

```bash
python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Frontend (Vite dev server on http://127.0.0.1:5173, which forwards `/api` to the backend):

```bash
cd frontend && npm run dev
```

Open http://127.0.0.1:5173/. The page should show `Backend health: ok` and a candlestick chart of the
synthetic sample. Hover over a candle to see its date, time (UTC), prices and volume.

Quick checks:

```bash
curl http://127.0.0.1:8000/api/health
```

```bash
curl http://127.0.0.1:5173/api/health
```

Both should return `{"status":"ok","service":"backend"}`.

One-minute sample candles (synthetic) from `prompts/data/sample-candles.csv`:

```bash
curl http://127.0.0.1:8000/api/history
```

Other timeframes (`1m` default, `5m`, `15m`, `1h`, `1d`), recorded tickers, and an as-of time for replay:

```bash
curl "http://127.0.0.1:8000/api/history?symbol=SAMPLE&timeframe=5m"
```

```bash
curl "http://127.0.0.1:8000/api/history?symbol=AAPL&timeframe=1d&as_of=2026-11-10T19:30:00Z"
```

The response is `{symbol, timeframe, as_of, source, candles, status, excluded_minutes}`. `candles[i]` has the six fields;
`status[i]` reports completion, coverage and whether volume is known.

Live Yahoo Finance check (no login; prints the first update, then disconnects, or reports receipt as unverified):

```bash
python3 -m backend.yahoo_stream AAPL --timeout 60
```

## Market database (SQLite)

Path: `backend/data/market.sqlite` by default. Override it with `--db PATH` or the `MARKET_DB` environment variable.

Initialize (creates the file and tables; safe to rerun):

```bash
python3 -m backend.recorder init
```

Record live observations for a fixed time, then inspect them:

```bash
python3 -m backend.recorder record AAPL --seconds 30
```

```bash
python3 -m backend.recorder show --ticker AAPL
```

`observations` holds one row per distinct message, and `receipts` logs every arrival, including exact repeats.
Count `observations` to avoid counting a repeat twice.

Build one-minute candles from the stored observations (optionally as of a given UTC time):

```bash
python3 -m backend.candles AAPL
```

## Tests

```bash
python3 -m unittest discover -s backend/tests -t . -v
```

Stop: press `Ctrl+C` in each terminal, or stop whatever is listening on the two ports:

```bash
kill $(lsof -tiTCP:8000 -sTCP:LISTEN) $(lsof -tiTCP:5173 -sTCP:LISTEN)
```

## Credentials

Do not put API keys in source code, prompts, logs or Git. Keep them in the
backend's environment, for example in a local `.env` file. `.gitignore` already
excludes that file.
