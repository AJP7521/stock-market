# Current state: 2026-11-12

**Last prompt:** 10 · Decision API (complete, not committed). Prompts 1–9D are committed (latest `0b1a51d`).

## Files created or changed (Prompt 10)
- New: `backend/decisions.py`, `backend/series.py` (`candle_series` moved out of `app.py`), `backend/settings.py`
  (`.env` loader), `backend/tests/test_decisions.py` (15 tests), `.env.example`
- Updated: `backend/app.py` (`/api/decisions`, `/api/decisions/snapshots`, `.env` loading), `backend/requirements.txt`
  (`anthropic==2.8.0`), `README.md`, `docs/design-pipeline.md`, `docs/verification.md`
- Local only (ignored): `.env` with `DECISIONS_PROVIDER=claude` and `ANTHROPIC_API_KEY`
- Installed: `anthropic` 2.8.0 into system Python 3.14 (approved by the student)

## Checks performed
70/70 unit tests pass (fixed snapshot, malformed answers, controlled timeout and connection failure for both clients).
One live Claude request (`claude-opus-5-5`) on the labelled TESTSMA test data: proposal buy, valid, matched the SQLite
decision row field for field; paper account untouched; key absent from DB, log and response. Backend stopped.

## Outstanding questions
- The student needs to review and approve the Prompt 10 commit.
- The student uses Claude only. Claude is now the default provider; OpenAI remains optional (`DECISIONS_PROVIDER=openai`),
  unit-tested only (no live call).

## Next task
Prompt 10A · Ask about chart (render a chart from a snapshot ID, capture it, submit it for analysis).
