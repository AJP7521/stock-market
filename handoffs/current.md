# Current state: 2026-11-10

**Last prompt:** 09A · Calculate moving averages (complete, not committed). Prompts 1–9 are committed (latest `5e95ebc`).

## Files created or changed (Prompt 9A)
- New: `backend/indicators.py`, `backend/fixtures.py`, `backend/tests/test_indicators.py` (6 tests)
- Updated: `backend/app.py` (`/api/indicators/sma`, SMA windows on `/api/replay`), `backend/replay.py` (per-step SMA),
  `frontend/src/App.vue` (SMA controls), `frontend/src/CandleChart.vue` (lines, markers, legend), `frontend/src/chartAdapter.js`
  (`toSmaData`), `README.md`, `docs/design-pipeline.md`, `docs/verification.md`
- Local, not in Git: `backend/data/test-sma.sqlite` (TESTSMA fixture; recreate with `python3 -m backend.fixtures sma`)

## Checks performed
33/33 unit tests pass. The hand calculation (13,10,7,13,19,7,1; windows 2/3) matches in a full run, in one-candle-at-a-time
runs and in replay steps. A forming candle never signals, and exact arithmetic avoids a float false signal. In the browser
(fixture DB, live feed off) the replay shows two markers, up 15:04Z and down 15:06Z, and the hover values match. Invalid
windows show readable errors. Services were stopped.

## Outstanding questions
- The student needs to review and approve the Prompt 9A commit.
- SMA on live AAPL was not checked in the browser this time (market data path unchanged; same endpoint).

## Next task
Prompt 09B · Indicator framework: add types in Python, named instances from Vue, rolling price range, settings in SQLite.
