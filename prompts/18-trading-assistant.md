# Prompt 18 · Trading assistant chatbot (side panel)

~~~text
i want t chatbot to assist the user in stock decisions. how should we do this?
~~~

~~~text
finish the rule switch first, then plan the chatbot. the chatbot should advise the user on nthe best possible action
ie sell buy
~~~

~~~text
i want the user to be able to ask the chatbot anything. make the chatbot appear as a side panel.
~~~

Decisions made with the student: a recommendation is placed only through a **"Place this paper order"** button the
student clicks; **text only** for now (voice later, reusing the voice panel's speech code).

## Scope chosen for this step

- A side panel ("Ask assistant" in the top bar) with a chat. The student can ask anything: the app, indicators,
  trading concepts, general questions, or what to do now.
- `backend/assistant.py`: Claude (`claude-opus-5-5`, adaptive thinking) in a manual tool-use loop, at most 6 tool rounds.
  The read-only tools call the app's own Python:
  - market state (candles, SMAs, crossovers, indicators, data label, data age, feed status)
  - the paper account
  - the crossover rule's current action
  - a backtest
  - recent AI decisions

  The model never calculates numbers itself. Answers end with a `give_answer` tool call (strict schema).
- For "what should I do" questions, the answer carries a recommendation for the PAPER account:
  - an action (buy, sell, close, hold or wait) and a quantity
  - a confidence, labelled as the model's own estimate
  - reasons with evidence, the case against, and what would change it

  General questions get an answer with no recommendation.
- Python check of every recommendation against the account: cash, shares (long only), whole quantity, completed
  candle, data younger than `ASSISTANT_STALE_S` (300 s). Test data is flagged. Hold and wait place no order.
- Nothing trades from the chat. Place repeats the check at click time, then calls `paper.execute(origin="assistant")`
  with `request_id = assistant-turn-<id>`, so a second click returns the same order.
- Stored: `assistant_sessions` and `assistant_turns`, which hold the tool calls and results, the recommendation, the
  Python check and the order ID.
- `DECISIONS_PROVIDER=test`: a labelled test double that calls the real tools and recommends the crossover rule's
  action. No new dependencies.
