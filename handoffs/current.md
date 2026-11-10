# Current state: 2026-11-10

**Last prompt:** 09C · Paper trading (complete, not committed). Prompts 1–9B are committed (latest `780af57`).

## Files created or changed (Prompt 9C)
- New: `backend/paper.py`, `backend/tests/test_paper.py` (9 tests), `frontend/src/PaperPanel.vue`
- Updated: `backend/app.py` (`/api/paper/account`, `/api/paper/orders`, `TEST_FEED` + `/api/test/observations`,
  source label for symbols without observations), `backend/live.py` (live observations fill pending orders),
  `frontend/src/App.vue` (panel, "TEST price feed on" badge), `README.md`, `docs/design-pipeline.md`, `docs/verification.md`

## Checks performed
49/49 unit tests pass. Browser run on a disposable DB with the test feed: buy 4 @ 100 → $600 / 4; sell 1 @ 110 → $710 / 3;
close @ 105 → $1,025 / 0. A retry returned the original order; a sale from the empty account was rejected with a reason;
after a backend restart exactly 3 fills remain. SQLite matches the dashboard. Services were stopped.

## Outstanding questions
- The student needs to review and approve the Prompt 9C commit.
- Not exercised: a fill from a live Yahoo price; trading during replay (Prompt 12).

## Next task
Prompt 09D · P&L history: weighted average cost; realized/unrealized rows after fills and completed-candle valuations.
