# Prompt 16E · Position-aware `chart_signal` (long, short, hold, exit, wait)

~~~text
Adding these entries is only part of the revision. Students should ask their coding agent to make the following parts
agree:

* Shared instructions: Include the expanded options, retain the two-completed-candle rule, and give insufficient,
  unclear, or stale evidence and unknown position state priority as WAIT.
* Existing descriptions: Restrict LONG and SHORT to an explicitly empty position, and revise WAIT to match the
  expanded meanings. HOLD means that the defined continuation condition is met; WAIT means that no applicable change
  can be determined.
* Position context: Supply whether a position exists and its direction. These are application-defined context
  fields, not built-in Decision API fields. An explicitly empty position differs from missing or unknown state, and
  the chart image alone does not establish either.
* Answer handling: Accept the added values, display their labels, and match their probabilities by value. Update any
  chart markers or other displays that depend on the choice list.
~~~

The added entries were not included. The coding agent recommended a full question, and the student answered "use
your recommended entries as written". Position context: the student chose "Main paper account".

~~~json
{
  "type": "choice",
  "name": "chart_signal",
  "instructions": "For this hypothetical chart-reading exercise, inspect the last two completed visible one-minute candles. Exclude a candle that is still forming. Use the chart and supplied capture time to identify completed intervals. Use the supplied position context to determine whether a position exists and its direction; the chart image alone does not establish the position. Choose wait if two completed candles cannot be identified, the evidence is unclear or insufficient, the supplied context marks it as stale, or the position state is missing or unknown; these conditions take priority over every other choice. Otherwise apply the choice descriptions. The chart is a limited snapshot.",
  "choices": [
    {"value": "long", "description": "The position is explicitly empty, and the more recent of the two completed candles closes above the preceding candle's close."},
    {"value": "short", "description": "The position is explicitly empty, and the more recent of the two completed candles closes below the preceding candle's close."},
    {"value": "hold", "description": "A position is open, and the more recent completed candle's close continues in the position's direction: above the preceding candle's close for a long position, or below it for a short position."},
    {"value": "exit", "description": "A position is open, and the more recent completed candle's close moves against the position's direction: below the preceding candle's close for a long position, or above it for a short position."},
    {"value": "wait", "description": "No applicable change can be determined: the two closing prices are equal, or the evidence is insufficient, unclear, or stale, or the position state is missing or unknown."}
  ]
}
~~~

## Scope chosen for this step

- The question above replaces the 16A/16C question word for word.
- Position context comes from the frozen snapshot's main paper account (`paper`) at the decision time: 0 shares →
  explicitly empty; more than 0 → open, long; account missing → unknown. It is sent as an application-defined context
  line, not a Decision API field. The paper account is long-only, so "short" never occurs in practice.
- Python applies the same rule with the position (unknown, stale, fewer than two candles or equal closes → wait).
- The five values are accepted, validated, labelled and matched to their probabilities by value. Chart markers get a
  shape and colour per value.
- `image_decisions`: new columns `position_state`, `position_direction`, `position_shares`; the `stance` CHECK
  constraint is widened to the five values by rebuilding the table once, keeping every row.
- Still no trade and no paper-account change. No new dependencies.
