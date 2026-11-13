"""Voice conversation about one recorded decision (Prompt 13), with Claude and the browser's own speech.

The Claude API has no audio input or output, so speech is handled in the browser (Web Speech API: recognition turns
the student's speech into text, speech synthesis reads the reply aloud). This module is the text side:

  record(conn, decision_id)   READ-ONLY summary from SQLite: the frozen snapshot (crossover, SMAs, account at the
                              decision), the analyst's proposal and answers, the reviewer's verdict and evidence,
                              Python's final check, and the recorded paper outcome (automation run, order, fill,
                              P&L row, account now) -- or a statement that no simulated trade happened and why.
  start_session()             stores the summary text the session will use (it never changes during the session)
  ask()                       one question -> one Claude call with the summary as the only source and the session's
                              earlier turns as history; the reply is saved with its model and latency
  end_session()               marks the session ended (the browser releases the microphone itself)

The model gets no tools and no database access: it can only read the summary text. When a question goes beyond
the record, it is told to say so and name the missing evidence. DECISIONS_PROVIDER=test uses a labelled
deterministic test double instead of Claude (call_kind 'mock').
"""

import os
import sqlite3
import time

from backend.candles import iso_ms
from backend.decisions import CLAUDE_DEFAULT_MODEL, get_decision
from backend.recorder import now_ms

SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_sessions (
    id            INTEGER PRIMARY KEY,
    decision_id   INTEGER NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    call_kind     TEXT NOT NULL,           -- 'live' Claude or 'mock' test double
    summary_text  TEXT NOT NULL,           -- the read-only record the model was given
    created_ms    INTEGER NOT NULL,
    ended_ms      INTEGER,
    end_reason    TEXT
);
CREATE TABLE IF NOT EXISTS voice_turns (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL REFERENCES voice_sessions(id),
    role          TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text          TEXT NOT NULL,
    created_ms    INTEGER NOT NULL,
    model         TEXT,
    latency_ms    INTEGER,
    status        TEXT                     -- assistant turns: ok | provider_error | timeout | refusal
);
"""

INSTRUCTIONS = (
    "You answer spoken questions from a student about ONE recorded decision in a PAPER trading simulation (no real "
    "money, no real orders). The RECORD below is your only source; it is read-only and you cannot look anything "
    "else up. Your reply will be read aloud: answer in one to three short, plain sentences, without lists, "
    "markdown, symbols or code. Say numbers the way a person would say them. Distinguish what the analyst agent "
    "proposed, what the reviewer agent decided, what Python checked, and what happened to the paper account. If "
    "the market data is labelled TEST, say it was test data when relevant. If a question goes beyond the record, "
    "say that the record does not show it and name the evidence that would be needed. Do not give investment "
    "advice.\n\nRECORD\n"
)


class VoiceError(ValueError):
    pass


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _money(v) -> str:
    return "unknown" if v is None else f"${v:,.2f}"


def record(conn: sqlite3.Connection, decision_id: int) -> dict:
    """Read-only facts about one decision and its paper outcome, plus the summary text the voice model reads."""
    d = get_decision(conn, decision_id)
    if d is None:
        raise VoiceError(f"no decision {decision_id}")
    snap = d["snapshot"] or {}
    candle, ind, acct = snap.get("candle") or {}, snap.get("indicators") or {}, snap.get("account") or {}
    review = d.get("review")
    final = d.get("final_check")
    run = order = pnl = account_now = None
    try:
        from backend.automation import trace
        t = trace(conn, decision_id)
        run, order, pnl, account_now = t["run"], t["order"], t["pnl_row"], t["account_now"]
    except Exception:                          # automation tables absent: a manual decision
        pass

    lines = [
        f"Decision number {decision_id}, created {d['created_at']}.",
        f"Symbol {snap.get('symbol')}, interval {snap.get('interval')}, snapshot kind {snap.get('kind')}. "
        f"Market data: {snap.get('market_data_source')}.",
        f"Selected candle {candle.get('timestamp')} closed at {candle.get('close')}; decision time "
        f"{snap.get('decision_time')}.",
        f"Python-calculated indicators at that candle: short SMA({ind.get('sma_short_window')}) {ind.get('sma_short')}, "
        f"long SMA({ind.get('sma_long_window')}) {ind.get('sma_long')}, crossover {ind.get('crossover_at_candle')}.",
        f"Paper account {acct.get('account_id')} at the decision: cash {_money(acct.get('cash'))}, position "
        f"{acct.get('position')} shares, order size {acct.get('order_size')}.",
        f"Analyst agent ({d['provider']} {d['model']}, {'mocked test double' if d['call_kind'] == 'mock' else 'live model'}): "
        f"status {d['status']}, proposal {d['model_proposal']['action'] or 'none'}"
        + (f", named answers {d['answers']}" if d.get("answers") else "")
        + (f", error {d['error']}" if d.get("error") else "") + ".",
    ]
    if review:
        lines.append(f"Reviewer agent ({review.get('provider')} {review.get('model')}, "
                     f"{'mocked test double' if review.get('call_kind') == 'mock' else 'live model'}): status "
                     f"{review.get('status')}, verdict {review.get('verdict') or 'none'}. Observed values: "
                     + "; ".join(review.get("evidence") or []) + ".")
    else:
        lines.append("No reviewer agent ran for this decision (single-call decision).")
    if final:
        failed = [g["gate"] for g in final["gates"] if not g["passed"]]
        lines.append("Python final check: " + ("eligible for execution." if final["eligible"]
                                               else "not eligible; failed: " + "; ".join(failed) + "."))
    else:
        lines.append(f"Python rule check: {d['validation']['status']}; eligible for an order: "
                     f"{'yes' if d['eligible_for_order'] else 'no'}.")
    if run is None:
        lines.append("Paper outcome: this decision was a manual question, not paper automation. Manual questions "
                     "never place orders, so no simulated trade happened for this decision.")
    else:
        lines.append(f"Paper automation run for this candle: status {run['status']}"
                     + (f", reason: {run['reason']}" if run.get("reason") else "") + ".")
        if order is None:
            lines.append("No paper order was placed for this decision, so no simulated trade happened.")
        else:
            lines.append(f"Paper order {order['id']}: {order['side']} {order['quantity']} share(s), status "
                         f"{order['status']}" + (f", reason: {order['reason']}" if order.get("reason") else "") + ".")
            if order.get("fill"):
                f = order["fill"]
                lines.append(f"Fill: {f['quantity']} share(s) at {f['price']} at {f['executed_at']} (next observed "
                             f"price); cash after {_money(f['cash_after'])}, position after {f['position_after']}.")
            if pnl:
                lines.append(f"P&L row after the fill: realized {_money(pnl['realized_micros'] / 1e6)}, total "
                             + (_money(pnl['total_micros'] / 1e6) if pnl.get("total_micros") is not None
                                else "unavailable") + ".")
        if account_now:
            lines.append(f"Account {account_now['account']} now: cash {_money(account_now['cash'])}, "
                         f"{account_now['positions'].get(snap.get('symbol'), 0)} {snap.get('symbol')} shares, "
                         f"{account_now['fill_count']} fills in total (this can include later decisions).")
    lines.append("The record does not include: live prices after the decision time, other decisions' reasoning, "
                 "the agents' hidden reasoning, or any real brokerage account.")
    return {"decision_id": decision_id, "symbol": snap.get("symbol"), "candle": candle.get("timestamp"),
            "crossover": ind.get("crossover_at_candle"), "proposal": d["model_proposal"]["action"],
            "verdict": (review or {}).get("verdict"), "eligible": d["eligible_for_order"],
            "outcome": (run or {}).get("status", "manual question: no order"),
            "fill": (order or {}).get("fill"), "account_now": account_now, "summary_text": "\n".join(lines)}


# --------------------------------------------------------------------------- providers

class ClaudeVoice:
    name, call_kind = "anthropic", "live"

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout_s: float | None = None):
        self.api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or os.environ.get("VOICE_MODEL") or CLAUDE_DEFAULT_MODEL
        self.timeout_s = timeout_s if timeout_s is not None else float(os.environ.get("VOICE_TIMEOUT_S", "30"))

    def reply(self, summary: str, history: list[dict]) -> dict:
        import anthropic
        if not self.api_key:
            raise VoiceError("ANTHROPIC_API_KEY is not set in the backend environment")
        client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout_s, max_retries=1)
        try:
            message = client.beta.messages.create(
                model=self.model, max_tokens=1000, system=INSTRUCTIONS + summary, messages=history,
                thinking={"type": "adaptive"}, output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        except anthropic.APITimeoutError:
            return {"text": "", "status": "timeout", "model": self.model}
        except anthropic.APIStatusError as exc:
            return {"text": "", "status": "provider_error", "model": self.model,
                    "error": f"HTTP {exc.status_code}: {exc.message}"[:300]}
        except anthropic.APIConnectionError as exc:
            return {"text": "", "status": "provider_error", "model": self.model, "error": f"connection error: {exc}"}
        if message.stop_reason == "refusal":
            return {"text": "", "status": "refusal", "model": message.model}
        text = "".join(b.text for b in message.content if b.type == "text").strip()
        return {"text": text, "status": "ok", "model": message.model}


class TestDoubleVoice:
    """VOICE TEST DOUBLE (DECISIONS_PROVIDER=test): fixed, labelled replies built from the record; no network."""

    name, call_kind, model = "test-double", "mock", "test-double"

    def __init__(self):
        self.received = []

    def reply(self, summary: str, history: list[dict]) -> dict:
        self.received.append({"summary": summary, "history": list(history)})
        question = history[-1]["content"]
        outcome = next((line for line in summary.splitlines() if line.startswith(("Paper outcome", "Paper order",
                                                                                  "No paper order"))), "")
        analyst = next((line for line in summary.splitlines() if line.startswith("Analyst agent")), "")
        return {"text": f"Test double reply to: {question} | {analyst} | {outcome} | turns so far {len(history)}",
                "status": "ok", "model": self.model}


def make_voice_provider():
    if os.environ.get("DECISIONS_PROVIDER", "claude").strip().lower() == "test":
        return TestDoubleVoice()
    return ClaudeVoice()


# --------------------------------------------------------------------------- sessions

def start_session(conn, decision_id: int, provider) -> dict:
    ensure_schema(conn)
    rec = record(conn, decision_id)
    with conn:
        sid = conn.execute("INSERT INTO voice_sessions (decision_id, provider, model, call_kind, summary_text, "
                           "created_ms) VALUES (?, ?, ?, ?, ?, ?)",
                           (decision_id, provider.name, provider.model, provider.call_kind, rec["summary_text"],
                            now_ms())).lastrowid
    return {**get_session(conn, sid), "record": rec}


def get_session(conn, session_id: int) -> dict | None:
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    s = conn.execute("SELECT * FROM voice_sessions WHERE id = ?", (session_id,)).fetchone()
    if s is None:
        return None
    turns = [dict(t) for t in conn.execute("SELECT * FROM voice_turns WHERE session_id = ? ORDER BY id",
                                           (session_id,))]
    return {"session_id": s["id"], "decision_id": s["decision_id"], "provider": s["provider"], "model": s["model"],
            "call_kind": s["call_kind"], "summary_text": s["summary_text"], "created_at": iso_ms(s["created_ms"]),
            "ended_at": iso_ms(s["ended_ms"]) if s["ended_ms"] else None, "end_reason": s["end_reason"],
            "turns": turns}


def ask(conn, session_id: int, question: str, provider) -> dict:
    """One spoken question -> one reply, using the session's frozen summary and earlier turns."""
    session = get_session(conn, session_id)
    if session is None:
        raise VoiceError(f"no voice session {session_id}")
    if session["ended_at"]:
        raise VoiceError(f"voice session {session_id} has ended")
    question = question.strip()
    if not question:
        raise VoiceError("no question was heard")
    history = []
    for t in session["turns"]:
        if t["role"] == "assistant" and (t["status"] != "ok" or not t["text"]):
            history.pop()                       # drop an unanswered question so roles keep alternating
            continue
        history.append({"role": t["role"], "content": t["text"]})
    history.append({"role": "user", "content": question})
    with conn:
        conn.execute("INSERT INTO voice_turns (session_id, role, text, created_ms) VALUES (?, 'user', ?, ?)",
                     (session_id, question, now_ms()))
    started = time.monotonic()
    out = provider.reply(session["summary_text"], history)
    latency = round((time.monotonic() - started) * 1000)
    with conn:
        conn.execute("INSERT INTO voice_turns (session_id, role, text, created_ms, model, latency_ms, status) "
                     "VALUES (?, 'assistant', ?, ?, ?, ?, ?)",
                     (session_id, out["text"], now_ms(), out["model"], latency, out["status"]))
    return {"session_id": session_id, "question": question, "reply": out["text"], "status": out["status"],
            "error": out.get("error"), "model": out["model"], "call_kind": provider.call_kind, "latency_ms": latency}


def end_session(conn, session_id: int, reason: str = "ended by the student") -> dict:
    session = get_session(conn, session_id)
    if session is None:
        raise VoiceError(f"no voice session {session_id}")
    if not session["ended_at"]:
        with conn:
            conn.execute("UPDATE voice_sessions SET ended_ms = ?, end_reason = ? WHERE id = ?",
                         (now_ms(), reason[:200], session_id))
    return get_session(conn, session_id)


def recent_decisions(conn, limit: int = 30) -> list[dict]:
    """Decisions to choose from, newest first, with their outcome."""
    from backend.decisions import ensure_schema as ensure_decisions
    ensure_decisions(conn)
    rows = conn.execute("SELECT id FROM decisions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for (did,) in rows:
        try:
            r = record(conn, did)
        except VoiceError:
            continue
        out.append({k: r[k] for k in ("decision_id", "symbol", "candle", "crossover", "proposal", "verdict",
                                      "eligible", "outcome")})
    return out
