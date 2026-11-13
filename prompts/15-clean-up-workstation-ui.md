# Prompt 15 · Clean up the workstation UI

~~~text
I want to make the UI look cleaner. Make it look like this:
[screenshot of a finance quote page: a white top bar with a wordmark, a rounded
search box and a green search button; a large symbol title; a large last price
with the change in red or green and an "as of" time; range tabs above a clean
area chart with light horizontal grid lines; a right-hand column of cards]
~~~

## Scope agreed for this step

- Presentation only: layout and CSS in `frontend/`. No backend, API, database or dependency changes.
- Use the screenshot as a style reference, not a copy: the workstation keeps its own name and no other company's
  logo or branding.
- Keep every `data-testid` and the visible text the SmokeTest reads, so `python3 -m backend.smoke` runs unchanged.
- Keep the simulated / test-data labels visible.
