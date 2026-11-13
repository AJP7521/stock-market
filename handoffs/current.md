# Current state: 2026-11-13

**Last prompt:** 14 · Workstation SmokeTest (complete, not committed). Prompts 1–13 are committed (latest `8e646c6`).
This was the last prompt in `prompts/`.

## Files created or changed (Prompt 14)
- New: `backend/smoke.py` (the SmokeTest), `backend/requirements-dev.txt` (playwright 1.65.1),
  `.github/workflows/smoke.yml`
- Updated: `backend/recorder.py` (test clock), `backend/decisions.py` (runtime test-double answers and failures,
  received symbol/candle in test-double responses), `backend/app.py` (`/api/test/clock`, `/api/test/provider`,
  automation `order_size`), `backend/automation.py` (order size per Start), `frontend/src/AutomationPanel.vue`
  (order-size field; trace shows unrealized and total P&L), `README.md`, `docs/design-pipeline.md`,
  `docs/verification.md`
- Installed (approved): playwright 1.65.1 + greenlet 3.5.6 in system Python 3.14.

## Checks performed
`python3 -m backend.smoke`: runs 1–2 failed on test-side timing/element choices (AutoLoop cycles 1–2), runs 3 and 4
passed 48/48 with a clean account each time; cleanup verified after passes and failures. 110/110 unit tests pass.

## Outstanding questions
- The student needs to review and approve the Prompt 14 commit.
- The GitHub Actions workflow has not run: there is no Git remote yet (push needs the student's approval).

## Next task
None in `prompts/`. Possible next steps: push to GitHub to run CI; decide on the pre-existing "database is locked"
start-up race in `recorder.connect`.
