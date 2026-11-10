# Current state: 2026-11-10

**Last prompt:** 09D · Record profit and loss (complete, not committed). Prompts 1–9C are committed (latest `1556216`).

## Files created or changed (Prompt 9D)
- New: `backend/pnl.py`, `backend/tests/test_pnl.py` (6 tests)
- Updated: `backend/paper.py` (valuations before fills; P&L row in the fill transaction), `backend/app.py`
  (`/api/paper/pnl`), `frontend/src/PaperPanel.vue` (P&L history table), `README.md`, `docs/design-pipeline.md`,
  `docs/verification.md`

## Checks performed
55/55 unit tests pass. Browser on the real clock with test prices: the five rows (600/4/0/0/0, 600/4/0/40/40,
710/3/10/30/40, 710/3/10/−15/−5, 1025/0/25/0/25) match the expected table on screen and in SQLite, before and after a
backend restart (diff empty). Services were stopped.

## Outstanding questions
- The student needs to review and approve the Prompt 9D commit.
- The `unavailable` valuation is covered only by a unit test.

## Next task
Prompt 10 · Decision API: frozen snapshot plus model proposal (needs an OpenAI package and API key: CHECK → TAKE ACTION).
