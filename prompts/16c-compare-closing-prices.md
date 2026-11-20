# Prompt 16C · `chart_signal` choices compare the two closing prices

~~~json
{
  "choices": [
    {
      "value": "long",
      "description": "The more recent of the two completed candles closes above the preceding candle's close."
    },
    {
      "value": "short",
      "description": "The more recent of the two completed candles closes below the preceding candle's close."
    },
    {
      "value": "wait",
      "description": "The two closing prices are equal, or the evidence is insufficient, unclear, or stale."
    }
  ]
}
~~~

## Scope chosen for this step

- These choices replace the `chart_signal` choices of Prompt 16A word for word. The question's name and
  instructions are unchanged.
- The rule is now a comparison between the two candles: long if the newer close is above the older close, short if
  it is below, wait if they are equal (and, as before, wait if two completed candles cannot be identified or the
  chart is stale). Opens no longer matter.
- Python's comparison answer (`python_signal`) follows the same new rule. Stored rows from Prompt 16A keep the value
  Python calculated at the time.
- No database, API or dependency change.
