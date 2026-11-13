# Current state: 2026-11-12

**Last prompt:** 11 · Agent review (complete, not committed). Prompts 1–10A are committed (latest `84eff64`).

## Files created or changed (Prompt 11)
- New: `backend/review.py` (reviewer request, verdicts, strategy rules, evidence, final check),
  `backend/tests/test_review.py` (8 tests)
- Updated: `backend/decisions.py` (`decide(review=True)`, review columns, Claude `max_retries = 0`), `backend/app.py`
  (`review` field, default true), `frontend/src/AskChartPanel.vue` (reviewer and final-check display), `README.md`,
  `docs/design-pipeline.md`, `docs/verification.md`
- No packages installed or manifests changed.

## Checks performed
90/90 unit tests pass. Three fixed-reply cases through the HTTP API saved as expected (eligible / rejected by the
reviewer / unaffordable buy blocked by Python), 0 orders. One live two-call example with Claude (buy → accept,
eligible); both calls received the same saved snapshot and image. Services stopped.

## Outstanding questions
- The student needs to review and approve the Prompt 11 commit.
- `POST /api/decisions` now makes two calls by default (`"review": false` restores one).

## Next task
Prompt 12 · Apply a reviewed decision to the paper account (Start/Stop automation).
