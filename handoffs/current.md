# Current state: 2026-11-10

**Last prompt:** 07A · Derive higher timeframes (complete, not committed). Prompts 1–7 are committed (latest `3344b73`).

## Files created or changed (Prompt 7A)
- New: `backend/market_calendar.py` (stdlib NYSE calendar), `backend/timeframes.py` (aggregate with coverage),
  `backend/tests/test_timeframes.py` (14 tests)
- Updated: `backend/app.py` (`/api/history` takes symbol, timeframe and as_of, and returns `{candles, status, ...}`),
  `frontend/src/App.vue` (reads `.candles`), `README.md`, `docs/design-pipeline.md`, `docs/verification.md`

## Checks performed
21/21 unit tests pass. 5m sample = 100/105/99/104/3550 with 3/5 coverage; 1d at 14:30Z with 3/390 coverage. The 14:35
test minute starts a 2nd 5m candle in the same day, and a next-session minute starts a new day. 2026 holidays, early
closes and DST were checked. HTTP checks through the proxy pass, the chart still renders, and services are stopped.

## Outstanding questions
- The student needs to review and approve the Prompt 7A commit.
- Calendar choice: stdlib rules, not a package. Unannounced special closures must be added to `SPECIAL_CLOSURES`.
- The frontend chart cannot draw a `null` volume yet (live minutes can have unknown volume). Handle this in Prompt 8.

## Next task
Prompt 08 · Live updates on the chart: timeframe selector and a FastAPI WebSocket pushing candle updates.
