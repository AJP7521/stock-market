# Current state: 2026-11-10

**Last prompt:** 02 · Connect Vue and FastAPI (complete, not committed). Prompt 1 is committed as `cf96f71`.

## Files created or changed (Prompt 2)
- New: `backend/__init__.py`, `backend/app.py` (GET /api/health), `backend/requirements.txt`
- New: `frontend/package.json`, `frontend/package-lock.json`, `frontend/vite.config.js`,
  `frontend/index.html`, `frontend/src/main.js`, `frontend/src/App.vue`
- Updated: `README.md` (setup, run and stop commands), `docs/design-pipeline.md`, `docs/verification.md`
- Installed (approved): fastapi 0.162.4 and uvicorn 0.54.0 into the system Python 3.14; vue 3.5.45,
  vite 8.3.7 and @vitejs/plugin-vue 6.0.10 into `frontend/node_modules`

## Checks performed
All nine Prompt 2 checks passed: the direct and proxied `/api/health` responses are identical, the
proxied request returns 502 when the backend is stopped, it recovers after a restart, and the browser
shows "Backend health: ok". Both services were stopped afterwards.

## Outstanding questions
- The student needs to review and approve the Prompt 2 commit.

## Next task
Prompt 03 · Load historical CSV: `GET /api/history` from `prompts/data/sample-candles.csv`, using the `csv` module.
