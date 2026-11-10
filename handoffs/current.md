# Current state: 2026-11-10

**Last prompt:** 08 · Display live updates (complete, not committed). Prompts 1–7A are committed (latest `8e9a114`).

## Files created or changed (Prompt 8)
- New: `backend/live.py` (SymbolFeed/LiveHub; yfinance subscribe and decode with our own receive loop; feed states)
- Updated: `backend/app.py` (WS `/api/ws/candles`, `/api/live/{symbol}` status and disconnect/connect test controls,
  shared `candle_series`), `backend/candles.py` (baseline must come from the preceding minute), `backend/tests/test_candles.py`
  (+ gap test), `frontend/src/App.vue` (symbol, interval, status bar, WebSocket), `frontend/src/CandleChart.vue`
  (`upsert` in place), `frontend/src/chartAdapter.js` (`toBars`; unknown volume = no bar), `README.md`,
  `docs/design-pipeline.md`, `docs/verification.md`
- No dependencies added.

## Checks performed
22/22 unit tests pass. Live: observation 67 (19:29:11Z, 348.33) traced from SQLite to the on-screen 19:29 candle
(348.355/348.355/348.33/348.33/7234, identical). 5m switch matches /api/history. Upstream pause → feed `paused` while the
browser socket stays `open`; reconnect → `live`. SAMPLE shows `offline` and releases the AAPL feed. Services were stopped.

## Outstanding questions
- The student needs to review and approve the Prompt 8 commit.
- Not exercised yet: a real upstream drop (`disconnected` + 5 s retry) and the `quiet` state.
- The first minute after any gap in recording shows unknown volume (by design).

## Next task
Prompt 09 · Replay a recorded session from SQLite with the live feed off.
