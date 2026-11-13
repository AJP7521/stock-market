# Project report: Live Market Workstation

**Git repository:** https://github.com/AJP7521/stock-market (public, branch `main`)

```bash
git clone https://github.com/AJP7521/stock-market.git
```

## Demo video

[Watch the demo video (`docs/media/assignment-1.mov`, 1 min 37 s, 10.6 MB)](docs/media/assignment-1.mov)

A screen recording of the workstation. All trading shown is simulated (paper account).

## Summary

A single local, AI-assisted market workstation and paper-trading bot, built as a course exercise in which the student
directs a coding agent, inspects the generated code, tests the running application and explains the design decisions.
**All trading is simulated** (a local long-only paper account). No real money or brokerage account is involved.

The workstation can:

- load one-minute candles from a synthetic CSV and from a live Yahoo Finance feed, store every observation in SQLite,
  and derive 5-minute, 15-minute, hourly and daily candles aligned to the NYSE session
- show candles, volume and indicators (short/long SMAs with crossovers, rolling price range) on an interactive chart,
  live or as a read-only replay of a recorded session
- trade a long-only paper account (next-price fills, zero fees) and show realized and unrealized P&L history
- ask an AI analyst about a captured chart image, have a second agent review the proposal, run a Python final check,
  and optionally apply approved decisions to the paper account
- discuss a recorded decision by voice
- run the whole workstation through one repeatable SmokeTest

## Technology stack

| Layer | Technology |
|---|---|
| Backend | Python 3.14, FastAPI 0.162.4 (HTTP + WebSocket), uvicorn 0.54.0 |
| Storage | SQLite (Python `sqlite3`) |
| Frontend | Vue 3.5.45, Vite 8.3.7, TradingView Lightweight Charts 5.2.1 |
| Market data | yfinance 1.8.0 `AsyncWebSocket` |
| AI provider | Claude API (`anthropic` 2.8.0, model `claude-opus-5-5`) by default; OpenAI optional |
| Speech | Browser Web Speech API in Google Chrome |
| Browser tests | Playwright 1.65.1 (Python) driving Google Chrome |
| CI | GitHub Actions (`.github/workflows/smoke.yml`) |

## Development history (one commit per prompt)

| Prompt | Feature | Commit |
|---|---|---|
| 01 | Project structure and documentation | `cf96f71` |
| 02 | Connect Vue dev server to FastAPI health endpoint | `1be70f6` |
| 03 | Serve one-minute sample candles from CSV | `8f6de53` |
| 04 | Candles and volume with Lightweight Charts | `44e927c` |
| 05 | Live Yahoo Finance updates via WebSocket | `a5eecb0` |
| 06 | Record observations in SQLite with repeat detection | `538be4d` |
| 07 | Form one-minute candles from stored observations | `3344b73` |
| 07A | Derive 5m/15m/1h/1d candles (NYSE session) | `8e9a114` |
| 08 | Stream live candle updates over a FastAPI WebSocket | `370ef9a` |
| 09 | Replay recorded sessions read-only from SQLite | `5e95ebc` |
| 09A | Short/long SMA lines with crossover markers | `706a938` |
| 09B | Configurable chart indicators, rolling price range | `780af57` |
| 09C | Long-only paper trading account | `1556216` |
| 09D | Realized and unrealized P&L history | `0b1a51d` |
| 10 | Decision API with Claude as default provider | `338df1b` |
| 10A | Ask about chart: chart capture with three questions | `84eff64` |
| 11 | Second-agent review with a Python final check | `f778a41` |
| 12 | Paper automation from reviewed decisions | `ab49e95` |
| 13 | Voice conversation about a recorded decision | `8e646c6`, `290e1f0` |
| 14 | One-command workstation SmokeTest with CI | `6b42471` |
| — | Handoff update; SQLite schema docs and duplicate/revision/late-observation tests | `8e6de54`, `698e1ac` |

The prompts are saved in `prompts/` (01–14, with 07a, 09a–09d and 10a).

## Verification (latest recorded results)

Full details, including failed attempts and AutoLoop cycles, are in `docs/verification.md`.

| Check | Command | Result | Data |
|---|---|---|---|
| Unit tests | `python3 -m unittest discover -s backend/tests -t .` | 118 tests, OK | test data |
| SmokeTest | `python3 -m backend.smoke` | PASS 48/48, twice in a row (runs 1–2 failed on test-side issues, fixed by AutoLoop) | test data, provider test double |
| GitHub Actions | push to `main` | success: unit tests OK, SmokeTest 48/48 | test data, offline, no credentials |
| Live Claude decision | Prompt 10 | buy on TESTSMA, matched SQLite | live model, **test market data** |
| Live Claude image + review | Prompts 10A, 11 | buy / up / sufficient; reviewer accept; matched SQLite | live model, **test market data** |
| Live voice session | Prompt 13, Google Chrome | answers matched SQLite; microphone released after End voice | live model, recorded decisions |
| Live Yahoo feed | Prompts 5–8 | observations recorded and charted | **live provider** |

The SmokeTest uses an isolated temporary database, the labelled TESTSMA fixture, a test clock and a provider test
double, so its results repeat. It starts FastAPI and Vite, drives headless Chrome through replay + SMA, Ask about
chart, and paper automation (buy 4 @ $100, sell 1 @ $110, close 3 @ $105 → $1,025 cash, $25 realized), then stops
every process and browser it started.

## Design decisions

- **Claude is the default provider**; OpenAI is optional and has never been called live.
- **Voice** uses the browser's Web Speech API instead of the prompt's WebRTC design, because the Claude API has no
  audio. Chrome sends speech audio to Google. Recorded as a deviation.
- **Automation on test data** trades only in the disposable `paper-test` account.
- **Stored observations are never updated or deleted**; duplicates, revisions and late arrivals are kept and
  handled at read time (see `README.md`).
- **Credentials** stay in an ignored `.env`; `.env.example` lists the variable names only.

## Known limitations and open items

- Re-run the live Yahoo path check (observation → SQLite → chart, feed status, disconnect/reconnect) during market
  hours (9:30–16:00 ET).
- Two simultaneous first connections to a brand-new database can fail once with "database is locked" (seen once at
  start-up; did not affect any check).
- Not exercised live: an OpenAI call, a real microphone-permission denial (mocked only), automation on live Yahoo
  data, live provider timeouts or refusals (mocked only).

## Further documentation

- `README.md` — setup, run instructions, API examples, tests
- `docs/design-pipeline.md` — components, data flow and the SQLite schema
- `docs/verification.md` — every check with its command, expected and observed result
- `handoffs/current.md` — current project state
