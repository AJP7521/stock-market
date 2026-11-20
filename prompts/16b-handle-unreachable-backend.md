# Prompt 16B · Handle an unreachable backend without console errors

~~~text
how should i correct the console errors?
go ahead and fix them
~~~

Context: with the backend stopped (for the SmokeTest) and the page left open, the console filled with
`Uncaught (in promise) TypeError: Failed to fetch` from PaperPanel (every 1 s), AutomationPanel (every 2 s) and
App.vue's SMA/indicator requests, plus one `Failed to load resource` line per request.

## Scope agreed for this step

- Frontend only (`frontend/src`); no backend, API, database or dependency change.
- A shared helper `backend.js`: `backendFetch` treats a network failure or the Vite proxy's 502/503/504 as
  "backend unreachable"; `createPoller` runs a repeating task, catches failures and doubles the pause after each
  one (up to 30 s), returning to the normal interval after a success.
- Paper and Automation panels poll through it and show a "Backend unreachable" notice, keeping the last values.
- App.vue catches every SMA / indicator / marker / replay request; the header shows "Backend unreachable: retrying…",
  checks `/api/health` with a growing pause, and when the backend answers reloads the live chart and reopens the
  WebSocket for the same symbol.
- Indicator and Voice panels no longer leave their first-load request uncaught.
- `Failed to load resource` lines are written by the browser itself and cannot be hidden by page code; they are
  reduced by asking less often, not removed.
