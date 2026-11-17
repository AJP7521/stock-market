# Prompt 16 · Image-only chart decision (long, short or wait)

~~~text
Use the Decision API control to send a chart snapshot to an Claude model and
show its choice of long, short, or wait. The model
should assess the image rather than receiving the underlying candle data. The
result may also appear as a marker on the chart. Show how the parts connect in
an educational flowchart. This decision does not place a trade.
~~~

## Scope chosen for this step

- A new **Chart-only decision** panel next to Ask about chart. It reuses the frozen snapshot and the in-browser PNG
  capture from Prompt 10A, so the image shows exactly the candles through the chosen cutoff.
- The model receives **only the PNG and fixed instructions** (no symbol, prices, SMA values, crossover or account
  JSON). The backend keeps the snapshot to check that the capture matches it and to compare afterwards.
- One named choice question, `stance`: `long`, `short` or `wait`. It is a view of the chart, not an order. The
  simulator stays long-only, nothing touches the paper account, and these results are never eligible for execution.
- Results are stored in a new table, `image_decisions`, separate from `decisions`, so automation, review and voice
  never pick them up.
- The latest result for each candle appears as a marker on the main chart (long ▲ below, short ▼ above, wait ●),
  reloaded from SQLite.
- An educational flowchart in the panel shows each part (Vue, FastAPI, SQLite, captures folder, Claude) and lights
  up the active step while a request runs.
- No new dependencies.
