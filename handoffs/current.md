# Current state: 2026-11-10

**Last prompt:** 09B · Create chart indicators (complete, not committed). Prompts 1–9A are committed (latest `706a938`).

## Files created or changed (Prompt 9B)
- New: `backend/indicator_store.py`, `backend/tests/test_indicator_types.py` (7 tests), `frontend/src/IndicatorPanel.vue`
- Updated: `backend/indicators.py` (indicator interface, sma and price_range types; crossover reuses `_sma_exact`),
  `backend/app.py` (indicator types/configs/values endpoints; test-data source label; configs in replay),
  `backend/replay.py` (per-step indicator values), `backend/fixtures.py` (`range` fixture), `frontend/src/App.vue`
  (panel, values table, URL keeps symbol and interval), `frontend/src/CandleChart.vue` (indicator lines, second pane, legend),
  `frontend/src/chartAdapter.js` (`toIndicatorLines`), `README.md`, `docs/design-pipeline.md`, `docs/verification.md`
- Local, not in Git: `backend/data/test-range.sqlite` (TESTRANGE fixture with the two stored configs)

## Checks performed
40/40 unit tests pass. Browser against the fixture: Range 2 = 5 and Range 3 = 6 at the third candle; both return after a
page reload; window 0 gives "Window (candles) must be at least 1, got 0" and the valid ones stay. SQLite holds exactly the
two configs. Services were stopped.

## Outstanding questions
- The student needs to review and approve the Prompt 9B commit.
- The Prompt 9A SMA pair (short/long controls) is still separate UI; it now shares the SMA calculation with the `sma` type.

## Next task
Prompt 09C · Paper trading: long-only account in SQLite, buy/sell/close, one execution function, next-price fills.
