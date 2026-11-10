# Current state: 2026-11-10

**Last prompt:** 06 · Record observations in SQLite (complete, not committed). Prompts 1–5 are committed (latest `a5eecb0`).

## Files created or changed (Prompt 6)
- New: `backend/recorder.py` (schema, `record()`, `observations()`, `receipts()`, `record_stream()`, CLI `init`/`record`/`show`)
- Updated: `.gitignore` (SQLite `-wal`/`-shm` files), `README.md` (database path and commands), `docs/design-pipeline.md`,
  `docs/verification.md`
- Local, not in Git: `backend/data/market.sqlite` with 30 live AAPL observations (19:18:19–19:18:47Z)

## Checks performed
Disposable test DB: an exact repeat leaves 1 observation with 2 receipts; a changed message at the same source time becomes
a separate row; a late arrival sorts by source time; an identified revision links to the earlier row without changing it.
A new process and the sqlite3 CLI read back identical rows. The live 30 s capture stored 30 messages, and 6 source seconds
have several distinct messages.

## Outstanding questions
- The student needs to review and approve the Prompt 6 commit.
- The recorder runs from the CLI. Running it continuously inside FastAPI is part of Prompt 8.

## Next task
Prompt 07 · Form one-minute candles from stored observations (difference day_volume for per-minute volume).
