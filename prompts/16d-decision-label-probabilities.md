# Prompt 16D · Probabilities in the decision label

~~~text
This illustrative response excerpt shows the fields used for the decision label. Other response fields are omitted:
~~~

~~~json
{
  "answers": [{
    "type": "choice",
    "name": "chart_signal",
    "choice": "wait",
    "probabilities": [
      {"value": "long", "probability": 0.10},
      {"value": "short", "probability": 0.05},
      {"value": "wait", "probability": 0.85}
    ]
  }]
}
~~~

## Decision made with the student

The Claude Messages API returns only the chosen answer, not measured probabilities. The student chose to **ask
Claude to estimate** a probability for each choice (option "Ask Claude to estimate"), labelled **model-estimated**
wherever it is shown.

## Scope chosen for this step

- Image-only decisions only (`chart_signal`). The Prompt 10–12 analyst and reviewer requests are unchanged.
- The Claude request adds `chart_signal_probabilities` (one number per choice) to the structured output; the reply is
  converted to the excerpt's shape: `answers[].probabilities = [{value, probability}]` in choice order.
- The backend checks the estimates: one per choice, each 0–1, sum within 0.02 of 1. Invalid or missing estimates are
  stored with the reason and never shown as a percentage; the answer itself still counts.
- The decision label shows the choice and its estimated probability (e.g. `WAIT · 85%`), in the panel and on the
  chart marker (`Claude: wait 85%`).
- New `image_decisions` columns `probabilities_json` and `probabilities_check`. No new dependencies.
