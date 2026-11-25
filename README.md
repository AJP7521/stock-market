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
- backtest the SMA crossover rule on stored candles (no look-ahead) and read its trades, performance and limits
- ask a trading assistant anything, and get a checked buy/sell/hold recommendation for the paper account
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
| Browser tests | Playwright 1.65.1 (Python) driving Google Chrome; SmokeTest `python3 -m backend.smoke` | installed (`backend/requirements-dev.txt`) |
| Speech | Browser Web Speech API (`SpeechRecognition`, `speechSynthesis`) in Google Chrome; no package | built in (Prompt 13) |
| AI provider | Claude API via `anthropic` 2.8.0 (model `claude-opus-5-5`), the default; optionally OpenAI `POST /v1/decisions` (standard library) with `DECISIONS_PROVIDER=openai`; key in the ignored `.env` | installed (`backend/requirements.txt`) |

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
|   |-- app.py                FastAPI app: /api/health, /api/history, WS /api/ws/candles, /api/live/{symbol}
|   |-- history.py            CSV loader for one-minute candles
|   |-- yahoo_stream.py       Yahoo Finance stream receiver (time-limited check)
|   |-- recorder.py           SQLite recorder for stream observations
|   |-- live.py               Live Yahoo feed inside FastAPI (records + notifies WebSocket clients)
|   |-- replay.py             Read-only replay of recorded observations
|   |-- indicators.py         Indicator interface (types: sma, price_range) + SMA crossover
|   |-- indicator_store.py    Named indicator instances stored in SQLite
|   |-- paper.py              Long-only paper account: one execution function, next-price fills
|   |-- pnl.py                P&L history: weighted average cost, fills and candle valuations
|   |-- backtest.py           SMA crossover backtest: next-open fills, stored runs, interpretation
|   |-- assistant.py          Trading assistant chatbot: Claude + read-only tools, checked recommendations
|   |-- fixtures.py           Labelled test fixtures in a separate SQLite file
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
|   `-- src/                  main.js, style.css, App.vue, CandleChart.vue, the panels (*Panel.vue), chartAdapter.js
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

Open http://127.0.0.1:5173/. The page opens on **AAPL, 1-minute**, streaming live from Yahoo Finance during market
hours. Every update is recorded in `backend/data/market.sqlite`. Use the interval tabs above the chart (1m, 5m, 15m, 1h, 1d) to
switch timeframes, or enter `SAMPLE` and press Load to see the synthetic CSV. The status bar shows the source, the age
of the latest source observation, the feed state, the browser's own connection to FastAPI, and whether the latest
candle is still forming. Hover over a candle to see its date, time (UTC), prices and volume.

### Replay a recorded session (live feed off)

Start the backend without any Yahoo connection, start the frontend as usual, and click **Replay**:

```bash
LIVE_FEED=off python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Choose a recorded symbol and a UTC range (prefilled from the recording), then click **Start replay**. Observations are
replayed in their original arrival order, without changing the database. The same is available as JSON:

```bash
curl "http://127.0.0.1:8000/api/replay?symbol=AAPL&start=2026-11-10T19:18:00Z&end=2026-11-10T19:31:00Z&timeframe=1m"
```

### Moving averages

Use the **SMAs** controls (Short, Long, Apply) above the chart. Python calculates both averages from completed closes and
the crossovers. The chart draws the lines and marks each crossover with an arrow. The same values are available as JSON:

```bash
curl "http://127.0.0.1:8000/api/indicators/sma?symbol=AAPL&timeframe=1m&short=5&long=20"
```

### Paper trading (simulated, long-only)

The **Paper account** panel shows cash, shares and order history, with **Buy**, **Sell**, **Close position**, **Hold** and
**Wait**. An order is filled at the next observed price after you submit it, with zero fees. Hold and wait create no
order. The account starts with `PAPER_STARTING_CASH` (default 1000) the first time it is opened.

Below the orders, **Profit and loss history** adds a row after every fill and whenever a completed one-minute candle
gives a new price for an open position. It shows cash, shares, average cost, realized, unrealized and total P&L, and the
time and source of the price used. The same rows are available as JSON:

```bash
curl "http://127.0.0.1:8000/api/paper/pnl?symbol=TESTPAPER"
```

To practise with known prices instead of the live feed, use a disposable database and turn on the test price feed:

```bash
MARKET_DB=/tmp/paper-demo.sqlite LIVE_FEED=off TEST_FEED=on python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

```bash
curl -X POST http://127.0.0.1:8000/api/test/observations -H 'Content-Type: application/json' -d '{"symbol":"TESTPAPER","price":100}'
```

### Backtest (SMA crossover rule, simulated)

The **Backtest · SMA crossover** panel (below the chart, live and replay) runs a fixed rule on the candles stored for
the chart's symbol and timeframe: an upward crossover while flat buys, a downward crossover while holding sells the
whole position. Set Short, Long and Starting cash and click **Run backtest**. Python walks the completed candles in
time order; at each candle the decision sees only the candles completed so far, and the order fills at the **next
candle's open**. Each run is saved in SQLite and can be reopened from **Earlier runs**.

The panel shows:
- final equity, return, **buy and hold** over the same candles, max drawdown, round trips won, realized and unrealized
  P&L
- the equity at each close with buy and sell markers
- every order and fill (signal candle, fill candle, quantity, price, cash, shares, realized P&L after)
- the round trips
- **What this result establishes** and **What it leaves uncertain**, written from the run's own numbers

The data source is shown with every run; test and synthetic data are labelled. To try it without the live feed:

```bash
python3 -m backend.fixtures backtest
```

```bash
python3 -m backend.fixtures synthetic --db backend/data/test-backtest.sqlite --keep
```

```bash
MARKET_DB=backend/data/test-backtest.sqlite LIVE_FEED=off python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:5173/?symbol=TESTBT&interval=1m` (18 hand-calculated candles; SMA 2/4 gives
$941.50, −5.85%, against +20% for buy and hold) or `?symbol=SYNTHBT&interval=5m` (two synthetic sessions). The same
run as JSON:

```bash
curl -X POST http://127.0.0.1:8000/api/backtests -H 'Content-Type: application/json' -d '{"symbol":"TESTBT","timeframe":"1m","short":2,"long":4,"starting_cash":1000}'
```

`GET /api/backtests?symbol=TESTBT` lists the saved runs; `GET /api/backtests/{id}` returns one with its orders,
equity curve, round trips and interpretation.

**What a backtest establishes:** how this exact rule would have traded these exact candles, with no look-ahead,
under the assumptions below, and how that compares with buy and hold. **What it leaves uncertain:** whether the
result is more than chance (few trades), whether it holds for other symbols, periods or windows (the windows were
chosen, not tested on held-back data), how real fees, slippage and spreads would change it, and what an open final
position will be worth. Test and synthetic data say nothing about real markets. Past simulated results do not predict
future results.

### Trading assistant (side panel)

Click **Ask assistant** in the top bar to open the chat on the right. Ask anything: how the app works, what an
indicator means, a general trading question, or "Should I buy or sell now?". Answers about the chart use the app's own
data through read-only tools (market state, paper account, the crossover rule's current action, a backtest, recent AI
decisions); expand **Data used** under an answer to see every tool call and result.

When you ask what to do, the answer includes a **recommendation for the paper account**:
- an action (buy, sell, close, hold or wait) and a quantity
- a confidence, which is the model's own estimate
- the reasons with their evidence, the case against, and what would change it

Python then checks the recommendation against the account and shows each check: enough cash, shares held (long only),
a whole quantity, a completed candle, and data younger than `ASSISTANT_STALE_S` (300 s). **Nothing trades from the
chat.** **Place this paper order** re-checks at that moment and sends the order through the normal paper execution;
it fills at the next observed price like any manual order. Clicking it again returns the same order. **New chat**
starts over; changing the symbol or timeframe also starts a new chat.

It uses Claude with `ANTHROPIC_API_KEY` from `.env` (model `ASSISTANT_MODEL`, default `claude-opus-5-5`).
`DECISIONS_PROVIDER=test` swaps in a labelled test double that calls the real tools and recommends the crossover
rule's action. To try it with test data and no model or Yahoo connection, start the `backend-assistant-demo` launch
configuration (`backend/data/test-assistant.sqlite`, made with `python3 -m backend.fixtures backtest --db
backend/data/test-assistant.sqlite`). Then set the test clock so the TESTBT data is fresh:

```bash
curl -X POST http://127.0.0.1:8000/api/test/clock -H 'Content-Type: application/json' -d '{"time":"2026-11-09T15:17:20Z"}'
```

Open `?symbol=TESTBT&interval=1m` with SMA 2/4. This is a simulation for a course, not financial advice.

### Simulation assumptions (paper account and backtest)

| | Paper account (manual and automation) | Backtest |
|---|---|---|
| Who decides | you (buttons, or **Place this paper order** on an assistant recommendation), the reviewed AI decision, or the fixed SMA crossover rule (automation rule mode) | fixed SMA crossover rule |
| Information used | the price at the moment of the order | only candles completed by the decision time |
| Fill price | next observed price after the order | open of the next completed candle (an overnight gap counts) |
| Quantity | whole shares you enter (the automation order size); close = whole position (rule mode closes on a downward crossover) | as many whole shares as cash allows; sell = whole position |
| Unfillable | rejected at fill if cash or shares no longer suffice | buy rejected if cash buys no share; a signal on the last candle stays "not filled" |
| Costs | zero fees and commissions | zero fees and commissions |
| Not modelled | slippage, bid/ask spread, partial fills, market impact, short selling, margin, dividends, splits, interest on cash | the same |
| P&L | weighted average cost (`pnl.apply_fill`); realized on sales, unrealized = shares × price − cost basis | the same function; open position valued at the last close, not sold |
| Valuation price | fill price, then each completed one-minute candle's close | each candle's close |
| Money | integer micro-dollars (1e-6 USD) | the same |

Market hours are not enforced for test data. All money and trades are simulated; nothing reaches a broker.

### Decisions (model proposal about one completed candle)

`POST /api/decisions` asks a model which action (buy, sell, hold, wait, close) fits one frozen snapshot of the last
completed candle, its two SMAs and crossover, and the paper account. The answer is checked against the long-only rules
and saved in SQLite with a factual explanation. **It never places an order**: the paper account is untouched.

Put `ANTHROPIC_API_KEY` in `.env` (copy `.env.example`); Claude is the default provider. To use OpenAI instead, set
`DECISIONS_PROVIDER=openai` and `OPENAI_API_KEY`. Freeze a snapshot first (no model call), then ask about it:

```bash
curl -X POST http://127.0.0.1:8000/api/decisions/snapshots -H 'Content-Type: application/json' -d '{"symbol":"TESTSMA","short":2,"long":3,"as_of":"2026-11-09T15:05:10Z"}'
```

```bash
curl -X POST http://127.0.0.1:8000/api/decisions -H 'Content-Type: application/json' -d '{"snapshot_id":1}'
```

`GET /api/decisions/{id}` and `GET /api/decisions/snapshots/{id}` read them back. Statuses: `decided`,
`malformed_answer`, `timeout`, `provider_error`, `missing_input`. A live model can be asked about **test** market data;
the response labels the market data source and the provider call separately.

### Talk about a decision (voice)

The **Talk about a decision (voice)** panel lists recent decisions. Choose one and press **Start voice**: the browser
asks for the microphone ("● Microphone active" while it is open), you ask a question aloud, and the answer is read
back; ask follow-ups in the same session. **End voice** (or a denied microphone, a lost backend or a speech error)
stops recognition and speech, releases the microphone and ends the session.

The Claude API has no audio, so speech stays in the browser (Web Speech API): `SpeechRecognition` turns speech into
text and `speechSynthesis` reads the reply. **Use Google Chrome**; Chrome sends the audio to Google's speech service.
This differs from the prompt's WebRTC design (no audio is streamed to the model; each turn is speech → text → Claude →
speech). FastAPI builds a read-only summary of the decision from SQLite (snapshot, analyst, reviewer, final check,
paper order, fill, P&L, account) and Claude answers only from it, naming the missing evidence when a question goes
beyond it. Sessions and every turn are stored in `voice_sessions` and `voice_turns`. `DECISIONS_PROVIDER=test`
replaces Claude with a labelled test double. A text box lets you type a question during a session.

### Paper automation (reviewed decisions → paper account)

The **Paper automation** panel (live mode) has **Start** and **Stop** for the current symbol, interval and SMA
strategy. While it runs, every newly completed candle is analysed **once**: the backend claims the candle (unique per
account, strategy, symbol, interval and candle in SQLite, so a reload or backend restart never repeats it) and freezes
its snapshot; the panel renders that snapshot off screen and captures a fresh PNG with the same capture as Ask about
chart; the analyst and reviewer decide. If the chart or image is unavailable the run is recorded as a failed analysis
and nothing is executed.

Only an **eligible, reviewer-approved** decision for the run's account and symbol becomes an order: buy and sell use
`PAPER_ORDER_SIZE`, close exits the whole position, hold and wait record no order. The order fills at the next observed
price, as for manual orders. Immediately before the fill Python rechecks cash or shares, that the decision is not
already filled, that it is younger than `DECISION_MAX_AGE_S` (default 300 s after the candle completed) and that
the same automation run is still enabled. The fill, balances, P&L row and decision ID are written in one transaction;
otherwise the reason is recorded. **Stop** disables new executions: a model reply still pending is saved but not
executed, and pending automation orders are rejected. Market data labelled **test** trades only in the disposable
`PAPER_TEST_ACCOUNT` (default `paper-test`, `PAPER_TEST_STARTING_CASH` 1000). A manual chart question never creates an
order.

**Rule mode (Prompt 17A).** Before Start, choose **AI (analyst + reviewer)**, the default described above, or **Rule
(SMA crossover)**. In rule mode, the same claim and frozen snapshot are used, but Python decides from the snapshot
alone, with no chart image and no model call. An upward crossover with no shares buys the order size; a downward
crossover with shares held closes the whole position; anything else is hold or wait (no order). The order fills at
the next observed price with the same pre-fill checks (age, still running, cash and shares). The runs table shows
the mode and the rule's action; rule runs have no decision ID, so there is no Trace. The backtest uses the same
crossover rule but buys with all available cash and fills at the next candle's open.

Each run's **Trace** button follows one decision ID: crossover → analyst proposal → reviewer verdict → Python final
check → order and fill (or skip reason) → account state. The same data is at `GET /api/automation/trace/{decision_id}`.

### Agent review (analyst + reviewer)

`POST /api/decisions` makes **two separate model calls** by default (`"review": false` turns the reviewer off). The
**analyst** proposes buy, sell, hold, wait or close. The **reviewer** has its own instructions and receives the same
frozen snapshot, the analyst's actual choice and named answers, the backend facts (including the cost of one order at
the candle close) and, for a chart question, the same PNG. It answers one verdict: `accept`,
`reject_insufficient_cash`, `reject_insufficient_position` or `reject_unsupported_signal`. Each role gets one call
with the `DECISIONS_TIMEOUT_S` timeout and no retries.

Python then runs a **final check**: the analyst's valid action, the account rules, the strategy rules (an up crossover
allows buy; a down crossover allows close or sell of an open position; otherwise hold or wait), the chart checks, a
valid reviewer reply and an `accept` verdict. A rejection, timeout or invalid reply makes the proposal ineligible, and
an accepted proposal that breaks a rule is still ineligible. The response's `review` and `final_check` sections, and
the Ask about chart panel, show the verdict, the observed values behind it and every gate. Nothing is ordered.

To try the review with fixed replies, start the backend with `DECISIONS_PROVIDER=test` and, for example,
`DECISIONS_TEST_ANSWERS=action=buy,verdict=reject_unsupported_signal`.

### Ask about chart (chart image + three questions)

Below the chart, **Ask about chart** freezes a decision snapshot at the selected **completed** candle (the cutoff),
renders that snapshot's candles, volume, SMA lines and configured indicator lines in a separate chart, captures it as a
PNG with Lightweight Charts' `takeScreenshot`, and shows the captured image with its symbol, interval, cutoff and
snapshot number. Live updates to the main chart do not change it. The PNG and snapshot ID go to `POST /api/decisions`,
which checks that the PNG is readable and matches the snapshot, stores it beside the database
(`captures/snapshot-<id>-<sha>.png`, served at `GET /api/decisions/{id}/image`), and asks three questions in one
request: **action**, **crossover** (up / down / none / unreadable) and **evidence** (sufficient / insufficient).

The panel shows what was sent, the three answers and the backend checks: the action against the long-only rules and
the visual crossover against the crossover Python calculated. An unreadable chart, a missing answer, a conflicting
crossover or insufficient evidence makes the decision **not eligible for automated execution**. An unreadable image or
a capture of a different snapshot is rejected (HTTP 422) with what to recapture, and nothing is stored. No order is
ever placed from this panel.

To try it without a model call, start the backend with the labelled provider test double and the TESTSMA fixture
(crossover up at 15:04 with SMA 2/3):

```bash
python3 -m backend.fixtures sma --db /tmp/ask-demo.sqlite
```

```bash
MARKET_DB=/tmp/ask-demo.sqlite LIVE_FEED=off DECISIONS_PROVIDER=test python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:5173/?symbol=TESTSMA&interval=1m`, set the SMAs to 2 and 3, pick the 15:04 cutoff and click
**Ask about chart**. The answers are marked **MOCK (provider test double)**; the test double's default answers include
no verdict, so add `DECISIONS_TEST_ANSWERS=action=buy,crossover=up,evidence=sufficient,verdict=accept` to see an
accepted review. Leave out `DECISIONS_PROVIDER=test` to
ask Claude.

### Chart-only decision (image → long, short, hold, exit or wait)

The **Chart-only decision** panel sends Claude **only a picture of the chart**, not the candle data, and asks one
question, `chart_signal` (Prompt 16E). It compares the newer of the last two completed closes with the preceding
close, together with the **position context**:

| Position | Newer close vs preceding | Answer |
|---|---|---|
| explicitly empty | higher / lower | **long** / **short** |
| open long | higher / lower | **hold** / **exit** |
| open short (never happens: the simulator is long-only) | lower / higher | **hold** / **exit** |
| any | equal | **wait** |
| missing or unknown, or the chart is stale or unclear | any | **wait** (takes priority) |

The position context is application-defined, not a Decision API field: it comes from the main paper account's
shares at the decision time (0 → explicitly empty, more than 0 → open long, account missing → unknown) and is sent as
"position exists" and "direction" only, never the share count or cash. Pick a completed cutoff candle and click
**Send chart image**. The panel freezes a
snapshot, draws it off screen, captures the PNG and posts it to `POST /api/image-decisions`. The backend checks the
PNG, stores it and sends the model the image, fixed instructions and one context line: the capture time (the backend
clock when the image arrives), the interval, and whether the chart is stale (the cutoff candle completed more than one
interval before the capture time). The symbol, prices, opens and closes, SMA values and the paper account stay on the
server. Python applies the same rule to the snapshot's last two completed candles and the panel shows its answer
next to Claude's as a **match** or a **CONFLICT**.

Claude also gives its **own estimate** of the probability of each answer (the Claude API returns no measured
probabilities). The backend stores them in the shape `probabilities: [{value, probability}]`, checks that there is one
per answer, each from 0 to 1, adding up to 1 (within 0.02), and the panel shows them as bars marked
**model-estimated**. The decision label is the answer with its estimate, e.g. **SHORT · 55%**, and the chart marker
reads `Claude: short 55%`. Missing or invalid estimates are shown with the reason and never as a percentage.

The answer is a chart-reading exercise. **No trade is placed**: the simulator stays long-only, the paper
account is never written, and these results are never eligible for execution. Each decided answer also appears as a
marker on the main chart at its candle (long ▲ green below, short ▼ red above, hold ■ blue below, exit ■ amber
above, wait ● grey above), reloaded from SQLite
(`GET /api/image-decisions?symbol=…&timeframe=…`). A flowchart in the panel shows which part (Vue, FastAPI, SQLite,
Claude) does each step and lights up the active step. Results are stored in the `image_decisions` table; the PNG is
served at `GET /api/image-decisions/{id}/image`.

With the provider test double (`DECISIONS_PROVIDER=test`, as in the Ask about chart example above), the default
answer is `chart_signal=long` and the result is labelled **MOCK (provider test double)**.

#### Compare chart_signal scenarios (test data)

```bash
python3 -m backend.compare_chart_signal
```

```bash
python3 -m backend.compare_chart_signal --live
```

Runs 16 scenarios (clear up, clear down, a red candle that closes higher, a green candle that closes level × an
explicitly empty, long, short or unknown position) through the real panel on a temporary database with the labelled
TESTSIG fixture. The position comes from a test-only override (`POST /api/test/position`, `TEST_FEED=on` only), never
from your paper account. For each scenario it checks that the question sent equals the approved JSON in
`prompts/16e-position-aware-chart-signal.md`, that the context names the position, and that the answer, label,
position and Python's answer on screen equal SQLite. The first form uses the provider test double; `--live` asks
Claude (about 16 calls) and records whether each answer matches the rule. `--evidence up,down,mixed-higher,mixed-equal`
runs a subset. Reports and one screenshot per scenario go to `test-results/compare-<time>/`. Ports 8000 and 5173
must be free. On macOS, prefix it with `caffeinate -dimsu` so the computer does not sleep during the run.

### Configurable indicators

The **Indicators** panel lists the indicator types Python provides (simple moving average, rolling price range). Give an
instance a name and settings and click **Add indicator**. Python validates the settings and stores them in SQLite, so the
same chart configuration returns when you reload (the URL keeps `?symbol=…&interval=…`). Values appear on the chart, in
the hover legend and in the table below the chart.

To check the rolling-range fixture (TESTRANGE, labelled test data in its own database):

```bash
python3 -m backend.fixtures range
```

```bash
MARKET_DB=backend/data/test-range.sqlite LIVE_FEED=off python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

To check the SMA crossover fixture (labelled test data in its own database), write it, start the backend against it
with the live feed off, then replay symbol `TESTSMA` with Short 2 and Long 3:

```bash
python3 -m backend.fixtures sma
```

```bash
MARKET_DB=backend/data/test-sma.sqlite LIVE_FEED=off python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Test controls for the upstream feed, while a page is watching the symbol:

```bash
curl -X POST http://127.0.0.1:8000/api/live/AAPL/disconnect
```

```bash
curl -X POST http://127.0.0.1:8000/api/live/AAPL/connect
```

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
Count `observations` to avoid counting a repeat twice. Every table in the database (market data, indicators, paper
account and P&L, decisions, automation, voice) is described in `docs/design-pipeline.md`, section "SQLite schema".

### Duplicates, revisions and out-of-order observations

Each Yahoo message is stored as received, and nothing stored is ever updated or deleted.

| Case | How it is stored | How candles and replay treat it | Tests |
|---|---|---|---|
| **Exact duplicate** (same ticker, identical decoded content) | no new observation (`UNIQUE (ticker, content_hash)`); a new `receipts` row with `is_repeat = 1`; `receipt_count` goes up | counted once; in replay the repeat is a step that changes nothing | `test_recorder` (repeat), `test_replay` (repeat step) |
| **Changed message with the same source time** | a new observation; the earlier row is kept | both are used (Yahoo has no correction field and one-second times, so several trades can share a time) | `test_recorder` |
| **Identified revision** (a caller passes `revises_id`) | a new observation pointing at the original; the original is unchanged | the revision replaces the original once the revision has been received | `test_recorder`, `test_candles` (correction), `test_replay` (revision at its arrival) |
| **Out-of-order / late arrival** (received after newer observations) | `source_time_ms` (when it happened) and `receipts.seq` / `received_at_ms` (when it arrived) are kept separately | placed in its candle by source time; a candle already shown is recomputed; in replay it appears only from its arrival, so earlier steps are not rewritten | `test_recorder` (ordering), `test_candles` (late middle observation), `test_replay` (late arrival) |

**Candle completeness** is not a stored flag: candles are recomputed from the stored observations for an as-of time
("now" when live, the replay time in replay). A candle is complete once the as-of time reaches its end; because the
inputs are preserved, the same as-of time always gives the same candles and the same complete/forming status. The
status bar shows it for the latest candle, and `/api/history` returns it for every candle.

Build one-minute candles from the stored observations (optionally as of a given UTC time):

```bash
python3 -m backend.candles AAPL
```

## Tests

```bash
python3 -m unittest discover -s backend/tests -t . -v
```

### SmokeTest (one command)

Install the test-only dependency once (Playwright for Python; it drives your installed Google Chrome):

```bash
python3 -m pip install -r backend/requirements-dev.txt
```

Run it (ports 8000 and 5173 must be free):

```bash
python3 -m backend.smoke
```

It creates a fresh temporary database with the labelled TESTSMA fixture (check 4 adds TESTBT), starts FastAPI (`LIVE_FEED=off`,
`DECISIONS_PROVIDER=test`, `TEST_FEED=on`, no API keys) and Vite, waits for both, and drives headless Chrome through:
(1) replaying the recorded session, adding an SMA, changing its window and comparing with a hand calculation;
(2) Ask about chart: a decodable PNG with the right symbol and candle at the model boundary, known answers on screen,
no fill, then a provider failure on screen; (2b) the chart-only decision: only the context text and the PNG reach the model,
the answer on screen matches SQLite, Python's expected answer and the marker list, no order, fill or decision is added, then a timeout on
screen; (3) paper automation on a test clock: buy 4 @ $100, sell 1 @ $110, close
3 @ $105 → $1,025, 0 shares, $25 realized, $0 unrealized, with a veto, a duplicate and a candle after Stop adding no
fill or order; (3b) rule-mode automation on TESTRULE: the up crossover buys 2 @ $100 and the down crossover closes
2 @ $50, with no chart capture and no model decision; (4) a backtest of the labelled TESTBT fixture with SMA 2/4: the summary tiles, the five orders and
fills, and the stored `backtest_runs` / `backtest_fills` rows match a hand calculation ($941.50, −5.85%, buy and hold
+20%, drawdown 23.26%), and the paper account is untouched; (5) the assistant side panel with the test double: "Should I
buy or sell now?" gives BUY 1 that can be placed and no order until Place, Place creates one assistant order and a
second click returns the same one, and a general question gets no recommendation card. It exits 1 if any expected result is missing, writes `test-results/smoke-<time>/report.md` (plus a
screenshot, logs and the database on failure), and stops the browser and every process it started, also on failure.
`SMOKE_HEADED=1` shows the browser; `SMOKE_BROWSER_CHANNEL=` (empty) uses Playwright's own Chromium instead of Chrome.
GitHub Actions runs the same command on every push (`.github/workflows/smoke.yml`), offline, without credentials.

The test clock (`POST /api/test/clock`) and runtime test-double answers (`POST /api/test/provider`) exist only when
the backend runs with `TEST_FEED=on`; they are never active in normal use.

Live checks stay separate, documented observations in `docs/verification.md`: live Yahoo data (Prompts 5–8), live
Claude image decisions (Prompts 10A–11) and the voice conversation (Prompt 13).

Stop: press `Ctrl+C` in each terminal, or stop whatever is listening on the two ports:

```bash
kill $(lsof -tiTCP:8000 -sTCP:LISTEN) $(lsof -tiTCP:5173 -sTCP:LISTEN)
```

## Credentials

Do not put API keys in source code, prompts, logs or Git. Keep them in the
backend's environment, for example in a local `.env` file. `.gitignore` already
excludes that file. `backend/settings.py` loads `.env` at startup without overriding variables that are already set,
and never logs the values. `.env.example` lists the variable names with empty values.
