# Current state: 2026-11-10

**Last prompt:** 04 · Integrate the candlestick chart (complete, not committed). Prompts 1–3 are committed (latest `8f6de53`).

## Files created or changed (Prompt 4)
- New: `frontend/src/CandleChart.vue` (candles, volume and a hover legend; TradingView attribution),
  `frontend/src/chartAdapter.js` (UTC ISO → UTCTimestamp; same time for both series)
- Updated: `frontend/src/App.vue` (loads `/api/history`), `frontend/package.json` and `package-lock.json`
  (lightweight-charts 5.2.1, approved), `README.md`, `docs/design-pipeline.md`, `docs/verification.md`

## Checks performed
The build passes. In the browser, 14:30, 14:31 and 14:32 show as separate candles with volume bars, and hovering
14:31 shows C 101 and Vol 850, matching the CSV. Attribution links are present, the console is clean, and one AutoLoop
fix was needed (fitContent timing). Services were stopped afterwards.

## Outstanding questions
- The student needs to review and approve the Prompt 4 commit.
- The chart legend has `data-testid` hooks ready for the Prompt 14 SmokeTest. No automated browser test exists yet.

## Next task
Prompt 05 · Connect Yahoo Finance: yfinance WebSocket subscription for AAPL (needs install approval).
