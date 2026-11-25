# Prompt 17A · Rule-based switch for Paper automation

~~~text
add the rule-based switch to Paper automation
~~~

(Follow-up to the requirements check after Prompt 17: the SMA crossover strategy reached the live paper account only
through the AI analyst and reviewer. "Apply the SMA crossover strategy in a simulated account" is met more directly
when the fixed rule itself can trade the paper account.)

## Scope chosen for this step

- Paper automation gets a **mode**: `ai` (analyst + reviewer, unchanged, still the default) or `rule` (fixed SMA
  crossover rule). The mode is chosen before **Start** and stored on `automation_state` and on every run.
- Rule mode uses the same claim, deduplication and frozen snapshot. Python then decides from that snapshot alone,
  with no chart image and no model call:
  - upward crossover with no shares → buy the order size
  - downward crossover with shares held → close the whole position
  - otherwise → hold (shares held) or wait (flat), no order
- The order goes through the one execution function, fills at the next observed price and gets the same pre-fill
  rechecks as AI orders: decision age (from the snapshot's decision time), automation still running, cash and shares.
- No `decisions` row is created for rule runs; the run stores `rule_action` and the reason.
- No new dependencies. Tests: `backend/tests/test_automation_rule.py`; SmokeTest check 3b.
