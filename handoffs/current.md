# Current state: 2026-11-13

**Last prompt:** 12 · Apply reviewed decisions to the paper account (complete, not committed). Prompts 1–11 are
committed (latest `f778a41`).

## Files created or changed (Prompt 12)
- New: `backend/automation.py` (state, runs, apply, pre-fill checks, trace), `backend/tests/test_automation.py`
  (12 tests), `frontend/src/AutomationPanel.vue`
- Updated: `backend/paper.py` (decision_id on orders and fills, FILL_CHECKS / ORDER_DECIDED hooks),
  `backend/app.py` (`/api/automation/*`, PAPER_TEST_ACCOUNT), `frontend/src/CandleChart.vue` (fixed-size capture,
  frame wait with fallback), `frontend/src/AskChartPanel.vue` (fixed-size capture), `frontend/src/App.vue`,
  `README.md`, `docs/design-pipeline.md`, `docs/verification.md`
- No packages installed or manifests changed.

## Checks performed
102/102 unit tests pass. Browser SmokeTest attempt 1 failed (capture stalled with the pane hidden; Start before test
data chose the main account); fixed in AutoLoop cycle 1. Attempt 2 passed: approved buy on the up crossover filled
once at 21 ($1,000 → $979, 1 share), the same decision applied twice more gave no new fill, restart + reload repeated
no candle, the vetoed close left the account unchanged, traces match SQLite. All responses mocked. Services stopped.

## Outstanding questions
- The student needs to review and approve the Prompt 12 commit.
- Pre-existing: "database is locked" on two simultaneous first connections to a brand-new DB (`recorder.connect`).

## Next task
Prompt 13 · Add live speech.
