# Current state: 2026-11-10

**Last prompt:** 07 · Form one-minute candles from observations (complete, not committed). Prompts 1–6 are committed (latest `538be4d`).

## Files created or changed (Prompt 7)
- New: `backend/candles.py` (as-of-aware one-minute candle builder and CLI), `backend/tests/__init__.py`,
  `backend/tests/test_candles.py` (7 unittest cases with test data)
- Updated: `README.md` (candle and test commands), `docs/design-pipeline.md` (aggregation rules), `docs/verification.md`

## Checks performed
All 7 tests pass. The prompt's 15:00 candle is 100/103/100/101/11 after the minute ends, and 100/103/100/103/9 while
forming. A late middle observation restores the same final values. Corrections, unknown volume (no baseline, reset,
new session) and a known zero are all covered. The live AAPL capture builds one 19:18 candle with unknown volume
(no baseline).

## Outstanding questions
- The student needs to review and approve the Prompt 7 commit.
- A live check of an unbroken capture across several minutes is still pending (live capture so far covers 28 s).

## Next task
Prompt 07A · Derive 5m/15m/1h/daily candles from the one-minute data, aligned to the regular session (exchange calendar).
