# Project report: Live Market Workstation

**Git repository:** https://github.com/AJP7521/stock-market (public, branch `main`)

```bash
git clone https://github.com/AJP7521/stock-market.git
```

## Demo video

[Watch the demo video (`docs/media/assignment-1.mov`, 1 min 37 s, 10.6 MB)](docs/media/assignment-1.mov)

A screen recording of the workstation. All trading shown is simulated (paper account).

[Watch the live chatbot video (`docs/media/live-chatbot.mov`, 1 min 5 s, 31.1 MB)](docs/media/live-chatbot.mov)

A screen recording of the trading assistant answering live with Claude on live Yahoo data for AAPL (paper account,
simulated; not financial advice).

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
- chat with a trading assistant in a side panel that answers any question and, when asked what to do, recommends a
  checked buy / sell / close / hold / wait action for the paper account
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
| Assistant tests | `python3 -m unittest backend.tests.test_assistant` | 7 tests, OK (rerun 2026-11-24) | test data, stubbed client |
| Assistant in SmokeTest | `python3 -m backend.smoke` | PASS 102/102, including 9 assistant checks | test data, provider test double |
| Live assistant chatbot | Prompt 18, side panel | WAIT, Python check passed, no order (2026-11-20 and 2026-11-25) | live model, **live Yahoo data** |

The SmokeTest uses an isolated temporary database, the labelled TESTSMA fixture, a test clock and a provider test
double, so its results repeat. It starts FastAPI and Vite, drives headless Chrome through replay + SMA, Ask about
chart, and paper automation (buy 4 @ $100, sell 1 @ $110, close 3 @ $105 → $1,025 cash, $25 realized), then stops
every process and browser it started.

## Trading assistant chatbot (Prompt 18)

An **"Ask assistant"** button in the top bar opens a chat side panel (`frontend/src/AssistantPanel.vue`). The student
can ask anything: how the app works, what an indicator means, general trading questions, or "should I buy or sell?".

- **How it answers.** `backend/assistant.py` runs Claude (`claude-opus-5-5`) in a tool-use loop of at most 6 rounds.
  The tools are read-only and call the app's own Python: market state (candles, SMAs, crossovers, data age, feed
  status), the paper account, the SMA crossover rule's current action, a backtest, and recent AI decisions. The model
  never calculates prices or P&L itself. It ends every answer with a `give_answer` call (strict schema).
- **Recommendations.** For "what should I do" questions the answer carries an action (buy, sell, close, hold or
  wait), a quantity, a confidence labelled as the model's own estimate, reasons with evidence, the case against, and
  what would change it. General questions get no recommendation.
- **Python check.** Every recommendation is checked against the account: cash, shares held (long only), whole
  quantity, a completed candle, and data younger than 300 s. Test data is flagged.
- **Nothing trades from the chat.** An order is placed only when the student clicks **"Place this paper order"**.
  The check is repeated at click time, and a second click returns the same order.
- **Stored.** `assistant_sessions` and `assistant_turns` hold each question, answer, tool call and result, the
  recommendation, the Python check and any order ID.

**Live answers.** Start the `backend` launch configuration (real database, live feed) with `ANTHROPIC_API_KEY` in
`.env`. The `backend-assistant-demo` configuration uses a labelled test double instead
(`DECISIONS_PROVIDER=test`).

Live check, 2026-11-25 about 02:57 UTC (after US market hours), **live Claude, live Yahoo data**, AAPL 1m, SMA 5/20:
"Should I buy or sell AAPL?" was answered in 29.5 s with 5 tool calls. The answer was **WAIT**, with 65% confidence
(the model's own estimate):

- **Sell:** not possible; the account holds 0 shares and $1,000 cash and is long only.
- **Buy:** not yet.
  - SMA5 (351.64) was above SMA20 (351.32), but there was no fresh crossover.
  - The 1-minute candles had gaps of several days, came from outside trading hours and had no volume.
  - The backtest gave +0.46% for the rule against +0.97% for buy-and-hold, with only 2 trades.

It also gave the case for buying. It said a fresh upward crossover during regular hours, with real volume, would
change its answer. The Python check reported "no order needed", and no order was placed. This answer is shown in the
live chatbot video. An earlier live check (2026-11-20) also answered WAIT, and it correctly gave no recommendation for
a general question about stop-loss orders.

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
