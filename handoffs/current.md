# Current state: 2026-11-19

**Last prompts:** 17A · Rule-based switch for Paper automation, and 18 · Trading assistant chatbot (side panel). Both
are done and verified with test data, and both are **not committed**. Latest commit: `335c035` (Prompts 16A–17).

## Files created or changed since `335c035` (`git status`)
- 17A: `backend/automation.py`, `backend/paper.py`, `backend/app.py` (`mode`), `frontend/src/AutomationPanel.vue`,
  `backend/tests/test_automation_rule.py`, `prompts/17a-rule-based-paper-automation.md`
- 18: `backend/assistant.py`, `backend/app.py` (`/api/assistant/*`), `frontend/src/AssistantPanel.vue`,
  `frontend/src/App.vue` (top-bar toggle), `backend/tests/test_assistant.py`, `prompts/18-trading-assistant.md`
- both: `backend/smoke.py` (checks 3b and 5), `.claude/launch.json` (`backend-assistant-demo`), `README.md`,
  `docs/design-pipeline.md`, `docs/verification.md`, this file
- Local only (ignored): `backend/data/test-assistant.sqlite` (TESTBT; one assistant order), `test-backtest.sqlite`

## Checks run
| Check | Result |
|---|---|
| `python3 -m unittest discover -s backend/tests -t .` | **158 tests, OK** |
| `python3 -m backend.smoke` | **PASS 102/102** (`smoke-20261119-220258`) |
| Browser pane: assistant on TESTBT with the test double | as expected (see `docs/verification.md`) |
| Live Claude answers (AAPL, real database) | **done**: an action question gave WAIT (stale data flagged), a general question gave no recommendation, 0 orders; Markdown display fixed (`frontend/src/chatText.js`) |

## Waiting on the student
- Review `git diff` and decide whether to commit 17A and 18, together or separately.
- Known, unchanged: the first-load HTTP 500 "duplicate column name: decision_id" race on a new database.

## Next task
Commit 17A and 18 after review. Optional: look at a formatted live reply on screen during regular market hours,
when the data is fresh and Place can be tried with a real recommendation.
