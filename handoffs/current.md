# Current state: 2026-11-12

**Last prompt:** 10A · Ask about chart (complete, not committed). Prompts 1–10 are committed (latest `338df1b`).

## Files created or changed (Prompt 10A)
- New: `backend/chart_images.py` (PNG check and storage), `backend/tests/test_chart_decisions.py` (12 tests),
  `frontend/src/AskChartPanel.vue`
- Updated: `backend/decisions.py` (snapshot at a selected candle with a chart section; image + three questions;
  chart checks; test-double provider), `backend/app.py` (image fields, `GET /api/decisions/{id}/image`),
  `backend/indicators.py` (`configured_values` moved here from `app.py`), `backend/tests/test_decisions.py`,
  `frontend/src/CandleChart.vue` (`capture()`), `frontend/src/App.vue`, `.gitignore` (`captures/`), `.env.example`,
  `README.md`, `docs/design-pipeline.md`, `docs/verification.md`
- No packages installed or manifests changed.

## Checks performed
82/82 unit tests pass. Browser SmokeTest (TESTSMA test data, provider test double): preview and cutoff correct, the
test double received a decodable PNG identical to the stored capture, answers on screen = SQLite, 0 orders, unreadable
image and mismatched capture rejected with recapture text. One live Claude image request: buy / up / sufficient,
eligible, matched SQLite. All services stopped.

## Outstanding questions
- The student needs to review and approve the Prompt 10A commit.
- Replay-mode use of the panel and a live OpenAI request were not exercised.

## Next task
Prompt 11 · Add agent review.
