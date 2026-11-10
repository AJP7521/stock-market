# Verification log

Each entry records the command, the expected result and the observed result.
Live-provider observations and mocked or test-data observations are labelled separately.

---

## Prompt 1 · Project structure and documentation (2026-11-10)

Setup decisions: the project root is `/Users/prest/Desktop/Claude/stock-market`.
`prompts/` was moved here unchanged from `live_market_workstation/prompts/`, and the
empty `live_market_workstation/` folder was removed. `git init` was run; nothing is
committed yet.

| # | Check | Command | Expected | Observed | Result |
|---|---|---|---|---|---|
| 1 | Operating system | `sw_vers; uname -m` | macOS, arch known | macOS 27.3 (26C5035r), arm64 | pass |
| 2 | Active Python | `python3 -c "import sys;print(sys.version, sys.executable)"` | Python 3.14, no venv | 3.14.7 at `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`; `VIRTUAL_ENV` and conda unset | pass |
| 3 | pip in that interpreter | `python3 -m pip --version` | pip present | pip 26.2.1 (python 3.14); only pip installed | pass |
| 4 | Built-in modules for Prompts 3 and 6 | `python3 -c "import csv,sqlite3,json; print(sqlite3.sqlite_version)"` | imports succeed | imports OK; SQLite 3.50.4 | pass |
| 5 | Node.js and npm | `node --version; npm --version` | Node present | v24.20.0 (arm64), npm 11.19.0; pnpm, yarn, bun, uv and conda not found | pass |
| 6 | Git | `git --version; git status` | repository initialised | git 2.54.0; empty repository, no commits | pass |
| 7 | Dependency manifests and lockfiles | `find . -name package*.json -o -name requirements*.txt -o -name pyproject.toml -o -name *.lock` | none yet | none found | pass |
| 8 | Third-party Python packages | `python3 -c "import fastapi"` (and `uvicorn`, `yfinance`, `openai`) | not yet needed | `ModuleNotFoundError` for all four | expected; install in later prompts |
| 9 | Sample data present | `head prompts/data/sample-candles.csv` | 6-field header and 3 rows | header `timestamp,open,high,low,close,volume`; rows at 14:30, 14:31 and 14:32 UTC on 2026-11-09 | pass |

### Checks still to run
- FastAPI, uvicorn and Vue/Vite install and startup checks (Prompt 2).
- No SmokeTest exists yet. It becomes possible once a UI exists (Prompt 4) and gets one command in Prompt 14.
