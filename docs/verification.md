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

---

## Prompt 2 · Connect Vue and FastAPI (2026-11-10)

Installed with student approval:
- `python3 -m pip install -r backend/requirements.txt`, which installed fastapi 0.162.4 and uvicorn 0.54.0 into the system Python 3.14 site-packages
- `npm install vue@3.5.45` and `npm install -D vite@8.3.7 @vitejs/plugin-vue@6.0.10` in `frontend/`. These created `package.json` and `package-lock.json`.

Services: backend `python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000`; frontend `cd frontend && npm run dev` (Vite on 127.0.0.1:5173, which forwards `/api` to 127.0.0.1:8000).

| # | Check | Command / URL | Expected | Observed | Result |
|---|---|---|---|---|---|
| 1 | Backend import and version | `python3 -c "import fastapi,uvicorn; ..."` | 0.162.4 / 0.54.0 | fastapi 0.162.4, uvicorn 0.54.0 | pass |
| 2 | Frontend packages | `npm ls --depth=0` | the three packages | @vitejs/plugin-vue 6.0.10, vite 8.3.7, vue 3.5.45 | pass |
| 3 | Frontend build | `npm run build` | build succeeds | `dist/assets/index-*.js` 63.67 kB; built in 35 ms | pass |
| 4 | Direct backend request | `curl http://127.0.0.1:8000/api/health` | 200, JSON status | 200 `{"status":"ok","service":"backend"}` | pass |
| 5 | Through the frontend server | `curl http://127.0.0.1:5173/api/health` | same as #4 | 200 `{"status":"ok","service":"backend"}` (identical) | pass |
| 6 | Backend stopped | `curl http://127.0.0.1:5173/api/health` | request fails | 502, empty body; Vite log `http proxy error: /api/health ... ECONNREFUSED 127.0.0.1:8000` | pass (proves it depends on FastAPI) |
| 7 | Backend restarted | `curl http://127.0.0.1:5173/api/health` | recovers | 200 `{"status":"ok","service":"backend"}` | pass |
| 8 | Browser view | open http://127.0.0.1:5173/ | page shows health | "Backend health: ok" | pass |
| 9 | Stop command | `kill $(lsof -tiTCP:8000 -sTCP:LISTEN) $(lsof -tiTCP:5173 -sTCP:LISTEN)` | ports freed | both ports free | pass |

Notes: pip offered 26.3 and npm offered 12.2.2. Neither was upgraded.

---

## Prompt 3 · Serve historical prices from a CSV file (2026-11-10)

Source: `prompts/data/sample-candles.csv` (supplied, **synthetic** one-minute candles), read with Python's built-in `csv` module in `backend/history.py` and served at `GET /api/history`. No packages were installed.

| # | Check | Command | Expected | Observed | Result |
|---|---|---|---|---|---|
| 1 | Endpoint responds | `curl http://127.0.0.1:8000/api/history` | 200, JSON array | 200, 3 candles | pass |
| 2 | Order and fields | the response | ordered by timestamp; exactly the six fields | 14:30, 14:31, 14:32; keys `timestamp,open,high,low,close,volume` on every row | pass |
| 3 | Row 2026-11-09T14:31:00Z against CSV line 2 | the response | 102 / 104 / 100 / 101 / 850 | open 102.0, high 104.0, low 100.0, close 101.0, volume 850; all six equal the CSV | pass |
| 4 | Ordering of out-of-order input | `load_candles()` on a temporary shuffled CSV | sorted ascending | 14:32, 14:30 in the file → 14:30, 14:32 returned | pass |
| 5 | Wrong header is rejected | `load_candles()` on a temporary CSV with header `time,...` | `ValueError` | `expected header [...] found ['time', ...]` | pass |

Representation: prices are returned as JSON floats (`102.0`), and volume as an integer number of shares in that minute.
