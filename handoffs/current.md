# Current state: 2026-11-19

**Last prompt:** 17 · Backtest the SMA crossover strategy (`prompts/17-backtest-sma-crossover.md`), done and
verified, **not committed**. Latest commit is still `69f9a3e` (Prompt 16). Prompts 16A–16F were already
uncommitted at the start of this session and are still uncommitted.

## Files created or changed (`git status`)
Prompt 17 (this session):
- new: `backend/backtest.py`, `backend/tests/test_backtest.py`, `frontend/src/BacktestPanel.vue`,
  `prompts/17-backtest-sma-crossover.md`
- changed: `backend/pnl.py` (`apply_fill` extracted, same behaviour), `backend/fixtures.py` (TESTBT, SYNTHBT,
  `--keep`), `backend/app.py` (`/api/backtests*`), `backend/smoke.py` (check 4), `frontend/src/App.vue` (panel
  mounted), `.claude/launch.json` (`backend-backtest-demo`), `README.md` (Backtest and Simulation assumptions
  sections, SmokeTest), `docs/design-pipeline.md` (components, three new tables), `docs/verification.md`,
  this file

Already uncommitted from Prompts 16A–16F (not touched except where listed above): `backend/decisions.py`,
`image_decisions.py`, `compare_chart_signal.py`, `tests/test_image_decisions.py`, the other `frontend/src/*Panel.vue`
files, `chartAdapter.js`, `backend.js`, `prompts/16a…16f`.

Local only (ignored): `backend/data/test-backtest.sqlite` (TESTBT + SYNTHBT demo data, with a few saved runs).

## Checks run
| Check | Result |
|---|---|
| `python3 -m unittest discover -s backend/tests -t .` | **145 tests, OK** |
| API + browser pane on TESTBT and SYNTHBT | as expected (hand table); one formatting bug fixed (`$-1.17`) |
| `npm --prefix frontend run build` | built |
| `python3 -m backend.smoke` | run 1 FAIL (TESTBT seeded too early broke the check 1 replay default); AutoLoop cycle 1 fixed it; run 2 **PASS 82/82** (`smoke-20261119-214245`) |

## Waiting on the student
- Review `git status` / `git diff`, then decide whether to commit Prompt 17 (and whether to commit 16A–16F with it
  or separately).
- Known, unchanged: two HTTP 500s "duplicate column name: decision_id" can appear on the first load of a brand-new
  database (race in `paper.ensure_schema`). Fixing it is a separate small task.

## Next task
Commit after review. Then, optionally: run a backtest on recorded live Yahoo data and record it as a live
observation in `docs/verification.md`, kept separate from the test-data checks.
