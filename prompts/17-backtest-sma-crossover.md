# Prompt 17 · Backtest the SMA crossover strategy

~~~text
Check the application against these requirements:

* Indicators. Display two simple moving averages of different lengths and their crossover. EMA, MACD, RSI, and
  other indicator types are optional.
* Simulated trading and accounting. Apply the SMA crossover strategy in a simulated account. Track orders and fills,
  cash, positions, and realized and unrealized profit or loss. Make the account state and trade history
  understandable, and document the assumptions used to simulate execution and calculate results.
* Backtesting. Evaluate the strategy on historical data using only information available at each decision time.
  Show the resulting trades and account performance, and explain what the results establish and what they leave
  uncertain.

(Follow-up after the audit: "Yes, plan it" — build the missing backtest.)
~~~

## Audit result (before this step)

- Indicators: met (SMA short/long + crossover arrows, `backend/indicators.py`).
- Simulated trading and accounting: mostly met. Orders, fills, cash, positions and weighted-average-cost P&L exist
  (`paper.py`, `pnl.py`), but the SMA strategy reached the account only through the AI analyst and reviewer, and the
  assumptions did not mention slippage, spread or partial fills.
- Backtesting: missing.

## Scope chosen for this step (approved plan)

- A **rule-based** SMA crossover strategy (no model): upward crossover while flat → buy; downward crossover while
  long → sell everything. Long only, like the paper account.
- A backtest engine (`backend/backtest.py`) that walks completed candles in order; at candle *i* the decision sees
  only `candles[:i+1]`, and the order fills at the **next candle's open**. Whole shares, fully invested, zero fees, no
  slippage or spread. The open position at the end is valued at the last close, not sold.
- One P&L rule for paper trading and backtests: `pnl.apply_fill` (weighted average cost), extracted from
  `pnl.record_fill`.
- Data: candles recorded in SQLite plus two new **labelled TEST** fixtures: TESTBT (18 candles, hand-calculated) and
  SYNTHBT (two sessions of a seeded random walk, synthetic). No network, no new dependencies.
- Each run is saved: `backtest_runs`, `backtest_fills`, `backtest_equity`.
- API: `POST /api/backtests`, `GET /api/backtests`, `GET /api/backtests/{id}`.
- UI: `BacktestPanel.vue`: summary tiles (equity, return vs buy and hold, drawdown, round trips, realized and
  unrealized P&L), equity curve with buy/sell markers, orders and fills, round trips, "What this result establishes /
  What it leaves uncertain", earlier runs.
- Tests: hand calculation, no look-ahead (changing or removing future candles leaves earlier decisions unchanged;
  signals equal the chart's crossovers), next-open fill, unfilled last signal, forming candle excluded, storage.
- SmokeTest check 4: run the TESTBT backtest in the real UI, compare screen and SQLite with the hand table.
