# Current state: 2026-11-10

**Last prompt:** 03 · Load historical CSV (complete, not committed). Prompts 1 and 2 are committed (`cf96f71`, `1be70f6`).

## Files created or changed (Prompt 3)
- New: `backend/history.py`, a `csv`-module loader that checks for exactly the six fields, parses UTC timestamps and sorts by minute start
- Updated: `backend/app.py` (adds `GET /api/history`), `README.md`, `docs/design-pipeline.md`, `docs/verification.md`

## Checks performed
`/api/history` returns the three synthetic candles in order with the six fields, and the 14:31 row matches CSV
line 2 (102/104/100/101/850). Shuffled input is sorted, and a wrong header is rejected. The backend was stopped afterwards.

## Outstanding questions
- The student needs to review and approve the Prompt 3 commit.
- The endpoint takes no symbol or timeframe yet. Prompt 7A adds the timeframe parameter.

## Next task
Prompt 04 · Integrate the candlestick chart: TradingView Lightweight Charts in Vue (needs install approval).
