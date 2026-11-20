# Prompt 16A · Chart-signal question for the image-only decision

~~~json
{
  "type": "choice",
  "name": "chart_signal",
  "instructions": "For this hypothetical chart-reading exercise, inspect the last two completed visible one-minute candles. Exclude a candle that is still forming. Use the chart and supplied capture time to identify completed intervals. Choose wait if two completed candles cannot be identified, the evidence is unclear or insufficient, or the supplied context marks it as stale. Otherwise apply the choice descriptions. The chart is a limited snapshot.",
  "choices": [
    {
      "value": "long",
      "description": "Both completed candles close above their own open."
    },
    {
      "value": "short",
      "description": "Both completed candles close below their own open."
    },
    {
      "value": "wait",
      "description": "Neither directional condition holds, either close equals its open, or the evidence is insufficient, unclear, or stale."
    }
  ]
}
~~~

## Scope chosen for this step

- This question replaces the `stance` question of Prompt 16 word for word (name, instructions and choices).
- The question refers to a "supplied capture time" and to context that may mark the chart as stale, so the
  request now carries a short text context next to the image: the capture time (the backend's clock when the image
  arrives), the chart interval, and whether the chart is **stale**. It is stale when the cutoff candle completed
  more than one interval before the capture time (a newer candle has completed since). The model still receives no
  candle data.
- Python applies the same rule to the snapshot's last two completed candles (not sent to the model) and shows its
  expected answer next to the model's answer, as a match or a conflict. A conflict is shown, not corrected.
- The answer is stored in the existing `stance` column; new columns `capture_ms`, `stale` and `python_signal`.
- Still no trade, no paper-account change, no new dependencies.
