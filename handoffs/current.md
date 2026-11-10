# Current state: 2026-11-10

**Last prompt:** 09 · Replay recorded observations (complete, not committed). Prompts 1–8 are committed (latest `370ef9a`).

## Files created or changed (Prompt 9)
- New: `backend/replay.py` (read-only replay), `backend/tests/test_replay.py` (5 tests)
- Updated: `backend/app.py` (`/api/replay`, `/api/replay/sessions`, `LIVE_FEED=off`, `live_feed` in health),
  `frontend/src/App.vue` (Live/Replay modes), `frontend/src/CandleChart.vue` (fit on new candle), `README.md`,
  `docs/design-pipeline.md`, `docs/verification.md`

## Checks performed
27/27 unit tests pass. With LIVE_FEED=off, a browser replay of AAPL 19:18–19:31Z (115 recorded live observations) showed
19:29 close 348.42 and volume 20591, matching SQLite. A second run was identical (API hashes equal), stored counts stayed
115/115 and the DB hash was unchanged. An empty selection shows a message. Services were stopped.

## Outstanding questions
- The student needs to review and approve the Prompt 9 commit.
- Late arrivals, revisions and repeats in replay are verified only with test data; the real capture has none.

## Next task
Prompt 09A · Two simple moving averages with crossover markers (Python calculates, Vue draws).
