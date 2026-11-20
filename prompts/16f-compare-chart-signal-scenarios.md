# Prompt 16F · Compare chart_signal scenarios: outgoing definitions and displayed answers

~~~text
Compare examples with no open position, an existing long or short position, unknown position state, and mixed
candle evidence. Check both the outgoing definitions and the displayed answer.
~~~

## Scope chosen for this step

- A repeatable command, `python3 -m backend.compare_chart_signal` (provider test double) and `--live` (Claude),
  built on the SmokeTest's isolated services: temporary SQLite database, `LIVE_FEED=off`, `TEST_FEED=on`, Vite,
  headless Chrome. `--evidence` runs a subset.
- 16 scenarios: 4 kinds of candle evidence × 4 position states. Evidence comes from a new labelled fixture, TESTSIG,
  with real opens and closes. Clear up, clear down, a red candle that still closes higher, and a green candle that
  closes level.
- The long-only paper account cannot hold a short position, so positions come from a new **test-only** override,
  `POST /api/test/position` (only with `TEST_FEED=on`, like the test clock). Each result records
  `position_source = "TEST override"`, and the panel shows it.
- Outgoing definitions are compared with the approved JSON parsed from `prompts/16e-position-aware-chart-signal.md`,
  not with the code. Expected answers are a hand-written table, not `python_signal`.
- The real paper account is never used. No new dependencies.
