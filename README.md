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
| Transport | FastAPI (HTTP + WebSocket), served by an ASGI server (uvicorn) | **not installed** – Prompt 2 |
| Storage | SQLite via Python's built-in `sqlite3` | available (SQLite 3.50.4) |
| Frontend | Vue, built with Vite | **not installed** – Prompt 2 |
| Charting | TradingView Lightweight Charts | **not installed** – Prompt 4 |
| Market data | `yfinance` WebSocket client | **not installed** – Prompt 5 |
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
|-- frontend/                 Vue components, user input, and presentation
|-- docs/
|   |-- design-pipeline.md    File responsibilities and how data moves
|   `-- verification.md       Repeatable checks and observed results
|-- prompts/                  Numbered course prompts (01 … 14) and data/sample-candles.csv
`-- handoffs/
    |-- create-handoff.md     Instructions for ending and resuming a session
    `-- current.md            Current project state, written at handoff
```

## Run instructions

There is nothing to run yet. Startup, stop and test commands will be added here
once they have been run and checked (Prompt 2 onward).

## Credentials

Do not put API keys in source code, prompts, logs or Git. Keep them in the
backend's environment, for example in a local `.env` file. `.gitignore` already
excludes that file.
