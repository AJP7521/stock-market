"""Paper automation from reviewed decisions (Prompt 12).

State and runs live in SQLite, so they survive a backend restart:

  automation_state  one row per strategy + symbol + interval: account, SMA windows, enabled, generation, start/stop
  automation_runs   one row per (account, strategy, symbol, interval, candle) -- UNIQUE, so a candle is analysed and
                    executed at most once, also after a restart or page reload

Flow for each newly completed candle while automation is enabled (Vue drives the chart capture):

  claim_run()      dedupe, freeze the snapshot at that candle for the automation account   -> run "analysing"
  analyse_run()    decide(image, review=True); then, only if the same automation generation is still enabled,
                   apply_decision()                                                        -> see statuses
  fail_run()       chart or image unavailable                                              -> "failed_analysis"

  apply_decision() only an eligible, reviewer-approved decision for the run's account and symbol:
                   buy / sell use the configured order size, close the whole position (simulator rules),
                   hold / wait record no order. The order carries the decision_id and a request_id built from the
                   dedupe key, so applying the same decision again returns the original order.

Immediately before a fill, the simulator calls fill_check() inside the fill transaction: the decision must not be
already filled, must be younger than DECISION_MAX_AGE_S (decision time = when the candle completed), and its
automation generation must still be enabled. order_decided() records the fill or the rejection on the run in that
same transaction. stop() disables new executions: runs still waiting for the model are stopped when the reply
arrives, and pending automation orders are rejected.

Run statuses: analysing, failed_analysis, skipped (not eligible / not approved), stopped, no_order (approved hold
or wait), order_pending, filled, rejected (at submission or fill recheck).

Rule-based mode (Prompt 17A)
----------------------------
start(mode="rule") runs the fixed SMA crossover rule instead of the analyst and reviewer. claim_run() still claims
the candle once and freezes the same snapshot; then rule_run() decides in Python from that snapshot only (the
crossover at the completed candle and the automation account's position), with no chart image and no model call:

  upward crossover, no shares      -> buy order_size shares
  downward crossover, shares held  -> close (sell the whole position)
  anything else                    -> hold (shares held) or wait (flat): no order

The order goes through the same execution function, fills at the next observed price and gets the same pre-fill
rechecks (decision age from the snapshot's decision time, automation still running, cash/shares). The run records
mode = 'rule' and rule_action; decision_id stays NULL because no model decision exists.

Snapshots whose market data is labelled test go to the disposable test account (PAPER_TEST_ACCOUNT); others go to
the main paper account. A manual chart question has no run, so apply_decision() refuses it.
"""

import os
import sqlite3

from backend import paper
from backend.candles import iso_ms
from backend.decisions import create_snapshot, decide, get_decision, load_snapshot
from backend.paper import account_state, execute, open_account
from backend.recorder import now_ms
from backend.series import candle_series, data_sources
from backend.timeframes import MINUTE_MS, to_ms

DECISION_MAX_AGE_S = float(os.environ.get("DECISION_MAX_AGE_S", "300"))
TEST_ACCOUNT = os.environ.get("PAPER_TEST_ACCOUNT", "paper-test")
TEST_STARTING_CASH = float(os.environ.get("PAPER_TEST_STARTING_CASH", "1000"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS automation_state (
    key          TEXT PRIMARY KEY,                -- strategy|symbol|interval
    strategy     TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    interval     TEXT NOT NULL,
    short        INTEGER NOT NULL,
    long         INTEGER NOT NULL,
    account_id   TEXT NOT NULL,
    order_size   INTEGER NOT NULL DEFAULT 1,     -- shares for buy and sell (close sells the whole position)
    mode         TEXT NOT NULL DEFAULT 'ai',      -- 'ai' (analyst + reviewer) or 'rule' (fixed crossover rule)
    enabled      INTEGER NOT NULL,
    generation   INTEGER NOT NULL,
    started_ms   INTEGER,
    stopped_ms   INTEGER
);
CREATE TABLE IF NOT EXISTS automation_runs (
    id            INTEGER PRIMARY KEY,
    account_id    TEXT NOT NULL,
    strategy      TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    interval      TEXT NOT NULL,
    candle_ts     TEXT NOT NULL,
    generation    INTEGER NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('analysing', 'failed_analysis', 'skipped', 'stopped', 'no_order',
                                                  'order_pending', 'filled', 'rejected')),
    reason        TEXT,
    snapshot_id   INTEGER,
    decision_id   INTEGER,
    order_id      INTEGER,
    fill_id       INTEGER,
    mode          TEXT NOT NULL DEFAULT 'ai',     -- mode at claim time
    rule_action   TEXT,                           -- rule mode: buy / close / hold / wait
    created_ms    INTEGER NOT NULL,
    updated_ms    INTEGER NOT NULL,
    UNIQUE (account_id, strategy, symbol, interval, candle_ts)
);
"""


class AutomationError(ValueError):
    pass


MODES = ("ai", "rule")


def _add_column(conn, table: str, column: str, ddl: str) -> None:
    if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            conn.commit()
        except sqlite3.OperationalError as exc:      # added by a concurrent request in the meantime
            if "duplicate column" not in str(exc):
                raise


def ensure_schema(conn: sqlite3.Connection) -> None:
    paper.ensure_schema(conn)
    conn.executescript(SCHEMA)
    _add_column(conn, "automation_state", "order_size", "INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "automation_state", "mode", "TEXT NOT NULL DEFAULT 'ai'")
    _add_column(conn, "automation_runs", "mode", "TEXT NOT NULL DEFAULT 'ai'")
    _add_column(conn, "automation_runs", "rule_action", "TEXT")


def strategy_name(short: int, long: int) -> str:
    return f"sma{short}x{long}"


def _key(strategy: str, symbol: str, interval: str) -> str:
    return f"{strategy}|{symbol}|{interval}"


def account_for(conn, symbol: str, paper_account: str, test_account: str) -> str:
    """Test-labelled market data trades only in the disposable test account."""
    return test_account if "test" in data_sources(conn, symbol) else paper_account


def state(conn, symbol: str, interval: str, short: int, long: int) -> dict | None:
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM automation_state WHERE key = ?",
                       (_key(strategy_name(short, long), symbol, interval),)).fetchone()
    return None if row is None else {**dict(row), "enabled": bool(row["enabled"]),
                                     "started_at": iso_ms(row["started_ms"]) if row["started_ms"] else None,
                                     "stopped_at": iso_ms(row["stopped_ms"]) if row["stopped_ms"] else None}


def start(conn, symbol: str, interval: str, short: int, long: int, *, paper_account: str, test_account: str,
          test_starting_cash: float, order_size: int = 1, mode: str = "ai", at_ms: int | None = None) -> dict:
    """Enable automation (a new generation). `order_size` is used for buy and sell orders until the next Start.

    mode "ai": analyst + reviewer decide; mode "rule": the fixed SMA crossover rule decides (rule_run).
    """
    if isinstance(order_size, bool) or not isinstance(order_size, int) or order_size < 1:
        raise AutomationError("order size must be a positive whole number of shares")
    if mode not in MODES:
        raise AutomationError(f"mode must be one of {', '.join(MODES)} (got {mode!r})")
    ensure_schema(conn)
    at_ms = at_ms if at_ms is not None else now_ms()
    account = account_for(conn, symbol, paper_account, test_account)
    if account == test_account:
        open_account(conn, test_account, test_starting_cash)
    strategy = strategy_name(short, long)
    with conn:
        conn.execute(
            """INSERT INTO automation_state (key, strategy, symbol, interval, short, long, account_id, order_size,
                   mode, enabled, generation, started_ms, stopped_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, NULL)
               ON CONFLICT (key) DO UPDATE SET enabled = 1, generation = generation + 1, account_id = excluded.account_id,
                   order_size = excluded.order_size, mode = excluded.mode, started_ms = excluded.started_ms,
                   stopped_ms = NULL""",
            (_key(strategy, symbol, interval), strategy, symbol, interval, short, long, account, order_size, mode,
             at_ms))
    return state(conn, symbol, interval, short, long)


def stop(conn, symbol: str, interval: str, short: int, long: int, at_ms: int | None = None) -> dict:
    """Disable new executions: reject pending automation orders; runs awaiting a model reply stop on arrival."""
    ensure_schema(conn)
    at_ms = at_ms if at_ms is not None else now_ms()
    key = _key(strategy_name(short, long), symbol, interval)
    with conn:
        conn.execute("UPDATE automation_state SET enabled = 0, stopped_ms = ? WHERE key = ? AND enabled = 1",
                     (at_ms, key))
        pending = conn.execute(
            """SELECT r.id, r.order_id FROM automation_runs r JOIN paper_orders o ON o.id = r.order_id
               WHERE r.strategy = ? AND r.symbol = ? AND r.interval = ? AND o.status = 'pending'""",
            (strategy_name(short, long), symbol, interval)).fetchall()
        for run_id, order_id in pending:
            reason = "automation stopped before the fill"
            conn.execute("UPDATE paper_orders SET status = 'rejected', reason = ?, decided_ms = ? WHERE id = ?",
                         (reason, at_ms, order_id))
            _set(conn, run_id, "rejected", reason, at_ms)
    return state(conn, symbol, interval, short, long)


def _set(conn, run_id: int, status: str, reason: str | None, at_ms: int, **cols) -> None:
    sets = ", ".join(["status = ?", "reason = ?", "updated_ms = ?"] + [f"{k} = ?" for k in cols])
    conn.execute(f"UPDATE automation_runs SET {sets} WHERE id = ?", (status, reason, at_ms, *cols.values(), run_id))


def get_run(conn, run_id: int) -> dict | None:
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
    return None if r is None else {**dict(r), "created_at": iso_ms(r["created_ms"]), "updated_at": iso_ms(r["updated_ms"])}


def _enabled_generation(conn, run: dict) -> bool:
    row = conn.execute("SELECT enabled, generation FROM automation_state WHERE strategy = ? AND symbol = ? AND "
                       "interval = ?", (run["strategy"], run["symbol"], run["interval"])).fetchone()
    return bool(row) and bool(row[0]) and row[1] == run["generation"]


def _candle_end(item: dict, interval: str) -> int:
    if interval == "1m":
        return to_ms(item["candle"]["timestamp"]) + MINUTE_MS
    return to_ms(item["status"]["interval_end"])


def claim_run(conn, symbol: str, interval: str, short: int, long: int, candle_time: str, *,
              order_size: int | None = None, chart_candles: int = 60, at_ms: int | None = None) -> dict:
    """Create the run for one newly completed candle (or return the existing one) and freeze its snapshot."""
    ensure_schema(conn)
    at_ms = at_ms if at_ms is not None else now_ms()
    st = state(conn, symbol, interval, short, long)
    if st is None or not st["enabled"]:
        raise AutomationError("automation is not running for this symbol, interval and strategy")
    existing = conn.execute(
        "SELECT id FROM automation_runs WHERE account_id = ? AND strategy = ? AND symbol = ? AND interval = ? "
        "AND candle_ts = ?", (st["account_id"], st["strategy"], symbol, interval, candle_time)).fetchone()
    if existing:
        return {"created": False, "run": get_run(conn, existing[0]), "snapshot": None}
    # Test-labelled market data may have arrived after Start: route again, so test prices never trade in the
    # main paper account.
    if "test" in data_sources(conn, symbol) and st["account_id"] != TEST_ACCOUNT:
        open_account(conn, TEST_ACCOUNT, TEST_STARTING_CASH)
        with conn:
            conn.execute("UPDATE automation_state SET account_id = ? WHERE key = ?", (TEST_ACCOUNT, st["key"]))
        st = state(conn, symbol, interval, short, long)
        existing = conn.execute(
            "SELECT id FROM automation_runs WHERE account_id = ? AND strategy = ? AND symbol = ? AND interval = ? "
            "AND candle_ts = ?", (st["account_id"], st["strategy"], symbol, interval, candle_time)).fetchone()
        if existing:
            return {"created": False, "run": get_run(conn, existing[0]), "snapshot": None}
    item = next((c for c in candle_series(symbol, interval, at_ms, conn=conn)["candles"]
                 if c["candle"]["timestamp"] == candle_time), None)
    if item is None or not item["status"]["complete"]:
        raise AutomationError(f"the {interval} candle at {candle_time} has not completed")
    if _candle_end(item, interval) <= st["started_ms"]:
        raise AutomationError(f"the candle at {candle_time} completed before automation started; only newly "
                              "completed candles are analysed")
    with conn:
        try:
            run_id = conn.execute(
                """INSERT INTO automation_runs (account_id, strategy, symbol, interval, candle_ts, generation, status,
                       mode, created_ms, updated_ms) VALUES (?, ?, ?, ?, ?, ?, 'analysing', ?, ?, ?)""",
                (st["account_id"], st["strategy"], symbol, interval, candle_time, st["generation"], st["mode"],
                 at_ms, at_ms)).lastrowid
        except sqlite3.IntegrityError:          # claimed concurrently
            run_id = conn.execute(
                "SELECT id FROM automation_runs WHERE account_id = ? AND strategy = ? AND symbol = ? AND "
                "interval = ? AND candle_ts = ?", (st["account_id"], st["strategy"], symbol, interval,
                                                    candle_time)).fetchone()[0]
            return {"created": False, "run": get_run(conn, run_id), "snapshot": None}
    snap = create_snapshot(conn, symbol, interval, short, long, st["account_id"],
                           order_size if order_size is not None else st["order_size"], at_ms,
                           candle_time=candle_time, chart_candles=chart_candles)
    with conn:
        conn.execute("UPDATE automation_runs SET snapshot_id = ? WHERE id = ?", (snap["snapshot_id"], run_id))
        if not snap["complete"]:
            _set(conn, run_id, "failed_analysis", "incomplete snapshot: " + "; ".join(snap["missing"]), at_ms)
    if snap["complete"] and st["mode"] == "rule":
        rule_run(conn, run_id, snap, at_ms)          # no image, no model: decided now from the frozen snapshot
    return {"created": True, "run": get_run(conn, run_id), "snapshot": snap}


def rule_action(snapshot: dict) -> tuple[str, str]:
    """The fixed SMA crossover rule on one frozen snapshot. Returns (action, reason). Uses nothing else."""
    crossover = snapshot["indicators"]["crossover_at_candle"]
    held = snapshot["account"]["position"]
    short, long = snapshot["indicators"]["sma_short_window"], snapshot["indicators"]["sma_long_window"]
    what = f"SMA{short}/SMA{long} crossover at the candle: {crossover}; {held} share(s) held"
    if crossover == "up" and held == 0:
        return "buy", f"rule: {what} -> buy {snapshot['account']['order_size']}"
    if crossover == "down" and held > 0:
        return "close", f"rule: {what} -> close the position"
    action = "hold" if held > 0 else "wait"
    return action, f"rule: {what} -> {action}, no order"


def rule_run(conn, run_id: int, snapshot: dict, at_ms: int | None = None) -> dict:
    """Rule mode: decide from the run's frozen snapshot and place at most one paper order (idempotent)."""
    at_ms = at_ms if at_ms is not None else now_ms()
    run = get_run(conn, run_id)
    if run is None:
        raise AutomationError(f"no automation run {run_id}")
    if run["status"] != "analysing":
        return run
    if not _enabled_generation(conn, run):
        with conn:
            _set(conn, run_id, "stopped", "automation stopped before the rule ran", at_ms)
        return get_run(conn, run_id)
    action, reason = rule_action(snapshot)
    if action in ("hold", "wait"):
        with conn:
            _set(conn, run_id, "no_order", reason, at_ms, rule_action=action)
        return get_run(conn, run_id)
    request_id = f"auto:{run['account_id']}:{run['strategy']}:{run['symbol']}:{run['interval']}:{run['candle_ts']}"
    result = execute(conn, run["account_id"], run["symbol"], action,
                     snapshot["account"]["order_size"] if action == "buy" else None,
                     request_id, at_ms, origin="automation")
    order = result["order"]
    with conn:
        if order["status"] == "rejected":
            _set(conn, run_id, "rejected", order["reason"], at_ms, order_id=order["id"], rule_action=action)
        else:
            _set(conn, run_id, "order_pending", f"{reason}; pending until the next observed price", at_ms,
                 order_id=order["id"], rule_action=action)
    return get_run(conn, run_id)


def fail_run(conn, run_id: int, reason: str, at_ms: int | None = None) -> dict:
    """The chart or image was unavailable: record the failed analysis; nothing is executed."""
    run = get_run(conn, run_id)
    if run is None:
        raise AutomationError(f"no automation run {run_id}")
    if run["status"] == "analysing":
        with conn:
            _set(conn, run_id, "failed_analysis", f"chart or image unavailable: {reason}"[:500],
                 at_ms if at_ms is not None else now_ms())
    return get_run(conn, run_id)


def analyse_run(conn, run_id: int, provider, image: str | None, capture: dict | None,
                at_ms: int | None = None) -> dict:
    """Analyst + reviewer on the run's frozen snapshot, then apply the decision if automation is still enabled."""
    from backend.chart_images import ImageRejected
    run = get_run(conn, run_id)
    if run is None:
        raise AutomationError(f"no automation run {run_id}")
    if run["status"] != "analysing" or run["decision_id"] is not None:
        return {"run": run, "decision": get_decision(conn, run["decision_id"]) if run["decision_id"] else None}
    if not _enabled_generation(conn, run):
        with conn:
            _set(conn, run_id, "stopped", "automation stopped before the analysis", now_ms())
        return {"run": get_run(conn, run_id), "decision": None}
    try:
        decision = decide(conn, run["snapshot_id"], provider, image=image, capture=capture, review=True)
    except ImageRejected as exc:
        return {"run": fail_run(conn, run_id, exc.problem), "decision": None}
    with conn:
        conn.execute("UPDATE automation_runs SET decision_id = ? WHERE id = ?", (decision["decision_id"], run_id))
    if not _enabled_generation(conn, get_run(conn, run_id)):   # Stop pressed while the models were answering
        with conn:
            _set(conn, run_id, "stopped", "automation stopped while the model reply was pending; not executed",
                 now_ms())
        return {"run": get_run(conn, run_id), "decision": decision}
    return apply_decision(conn, decision["decision_id"], at_ms=at_ms)


def apply_decision(conn, decision_id: int, at_ms: int | None = None) -> dict:
    """Turn one approved decision into at most one paper order (idempotent)."""
    ensure_schema(conn)
    at_ms = at_ms if at_ms is not None else now_ms()
    row = conn.execute("SELECT id FROM automation_runs WHERE decision_id = ?", (decision_id,)).fetchone()
    decision = get_decision(conn, decision_id)
    if decision is None:
        raise AutomationError(f"no decision {decision_id}")
    if row is None:
        raise AutomationError(f"decision {decision_id} was not made by paper automation (a manual chart question "
                              "never creates an order)")
    run = get_run(conn, row[0])
    snapshot = decision["snapshot"]
    if run["order_id"] is not None or run["status"] in ("no_order", "skipped", "filled", "rejected", "stopped"):
        return {"run": run, "decision": decision, "order": _order_or_none(conn, run["order_id"]),
                "duplicate": True, "note": f"decision {decision_id} was already handled ({run['status']})"}
    final = decision.get("final_check") or {}
    action = decision["model_proposal"]["action"]
    reason = None
    if not (decision["eligible_for_order"] and final.get("eligible")):
        failed = [g["gate"] for g in final.get("gates", []) if not g["passed"]] or [decision["status"]]
        reason = "not eligible: " + "; ".join(failed)
    elif (decision.get("review") or {}).get("verdict") != "accept":
        reason = "the reviewer did not approve the proposal"
    elif snapshot["account"]["account_id"] != run["account_id"] or snapshot["symbol"] != run["symbol"]:
        reason = "the decision is for a different account or symbol"
    if reason:
        with conn:
            _set(conn, run["id"], "skipped", reason, at_ms)
        return {"run": get_run(conn, run["id"]), "decision": decision, "order": None, "duplicate": False,
                "note": f"skipped: {reason}"}
    if action in ("hold", "wait"):
        result = execute(conn, run["account_id"], run["symbol"], action, None, f"auto-{run['id']}", at_ms,
                         origin="automation", decision_id=decision_id)
        with conn:
            _set(conn, run["id"], "no_order", result["note"], at_ms)
        return {"run": get_run(conn, run["id"]), "decision": decision, "order": None, "duplicate": False,
                "note": result["note"]}
    request_id = f"auto:{run['account_id']}:{run['strategy']}:{run['symbol']}:{run['interval']}:{run['candle_ts']}"
    result = execute(conn, run["account_id"], run["symbol"], action,
                     snapshot["account"]["order_size"] if action in ("buy", "sell") else None,
                     request_id, at_ms, origin="automation", decision_id=decision_id)
    order = result["order"]
    with conn:
        if order["status"] == "rejected":
            _set(conn, run["id"], "rejected", order["reason"], at_ms, order_id=order["id"])
        else:
            _set(conn, run["id"], "order_pending", "pending until the next observed price", at_ms,
                 order_id=order["id"])
    return {"run": get_run(conn, run["id"]), "decision": decision, "order": order,
            "duplicate": result["duplicate"], "note": result["note"]}


def _order_or_none(conn, order_id):
    if order_id is None:
        return None
    return paper._order(conn, order_id)


# --------------------------------------------------------------------------- simulator hooks

def fill_check(conn, order: dict, received_ms: int) -> str | None:
    """Recheck an automation order immediately before its fill (inside the fill transaction).

    AI mode: the order carries a decision_id. Rule mode: no decision; the age comes from the run's snapshot.
    """
    try:
        run = conn.execute("SELECT id, strategy, symbol, interval, generation, snapshot_id FROM automation_runs "
                           "WHERE order_id = ?", (order["id"],)).fetchone()
    except sqlite3.OperationalError:
        run = None
    if order.get("decision_id") is not None:
        if conn.execute("SELECT 1 FROM paper_fills WHERE decision_id = ?", (order["decision_id"],)).fetchone():
            return f"decision {order['decision_id']} was already filled"
        snap = conn.execute("SELECT s.decision_ms FROM decisions d JOIN decision_snapshots s ON s.id = d.snapshot_id "
                            "WHERE d.id = ?", (order["decision_id"],)).fetchone()
        if snap is None:
            return f"decision {order['decision_id']} not found"
    else:
        if run is None:
            return None                         # not an automation order
        snap = conn.execute("SELECT decision_ms FROM decision_snapshots WHERE id = ?", (run[5],)).fetchone()
        if snap is None:
            return f"snapshot of automation run {run[0]} not found"
    age_s = (received_ms - snap[0]) / 1000
    if age_s > DECISION_MAX_AGE_S:
        return f"decision too old at fill: {age_s:.0f} s after the candle completed (limit {DECISION_MAX_AGE_S:.0f} s)"
    if run is not None:
        st = conn.execute("SELECT enabled, generation FROM automation_state WHERE strategy = ? AND symbol = ? AND "
                          "interval = ?", (run[1], run[2], run[3])).fetchone()
        if not st or not st[0] or st[1] != run[4]:
            return "automation stopped before the fill"
    return None


def order_decided(conn, order_id: int, status: str, fill_id: int | None, reason: str | None) -> None:
    """Record a fill or rejection on the run, in the same transaction as the simulator's change."""
    try:
        run = conn.execute("SELECT id FROM automation_runs WHERE order_id = ?", (order_id,)).fetchone()
    except sqlite3.OperationalError:      # automation tables not created in this database
        return
    if run is None:
        return
    at = conn.execute("SELECT decided_ms FROM paper_orders WHERE id = ?", (order_id,)).fetchone()[0]
    if status == "filled":
        _set(conn, run[0], "filled", None, at, fill_id=fill_id)
    else:
        _set(conn, run[0], "rejected", reason, at)


if fill_check not in paper.FILL_CHECKS:
    paper.FILL_CHECKS.append(fill_check)
if order_decided not in paper.ORDER_DECIDED:
    paper.ORDER_DECIDED.append(order_decided)


# --------------------------------------------------------------------------- reading

def runs(conn, symbol: str, interval: str, short: int, long: int, limit: int = 30) -> list[dict]:
    ensure_schema(conn)
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM automation_runs WHERE strategy = ? AND symbol = ? AND interval = ? ORDER BY candle_ts DESC, "
        "id DESC LIMIT ?", (strategy_name(short, long), symbol, interval, limit))]
    out = []
    for run in (get_run(conn, i) for i in ids):
        d = get_decision(conn, run["decision_id"]) if run["decision_id"] else None
        crossover = d["backend_facts"].get("crossover_at_candle") if d else None
        if run["mode"] == "rule" and run["snapshot_id"]:
            snap = load_snapshot(conn, run["snapshot_id"])
            crossover = snap["indicators"]["crossover_at_candle"] if snap else None
        out.append({**run, "crossover": crossover,
                    "proposal": d["model_proposal"]["action"] if d else run["rule_action"],
                    "verdict": (d.get("review") or {}).get("verdict") if d else None,
                    "eligible": d["eligible_for_order"] if d else None,
                    "call_kind": d["call_kind"] if d else None})
    return out


def trace(conn, decision_id: int) -> dict:
    """One decision from the crossover through proposal and review to the fill or skip and the account state."""
    d = get_decision(conn, decision_id)
    if d is None:
        raise AutomationError(f"no decision {decision_id}")
    row = conn.execute("SELECT id FROM automation_runs WHERE decision_id = ?", (decision_id,)).fetchone()
    run = get_run(conn, row[0]) if row else None
    order = _order_or_none(conn, run["order_id"]) if run else None
    pnl = None
    if order and order.get("fill"):
        conn.row_factory = sqlite3.Row
        p = conn.execute("SELECT * FROM pnl_history WHERE event_key = ?", (f"fill:{order['fill']['id']}",)).fetchone()
        pnl = dict(p) if p else None
    snap = d["snapshot"]
    account_id = snap["account"]["account_id"] if snap and snap.get("account") else None
    return {
        "decision_id": decision_id, "run": run,
        "crossover": {"candle": snap["candle"], "indicators": snap["indicators"],
                      "decision_time": snap["decision_time"], "market_data": snap["market_data_source"]},
        "account_at_decision": snap["account"],
        "analyst": {"provider": d["provider"], "model": d["model"], "call_kind": d["call_kind"],
                    "proposal": d["model_proposal"]["action"], "answers": d["answers"], "status": d["status"]},
        "review": d.get("review") and {k: d["review"].get(k) for k in
                                       ("provider", "model", "call_kind", "status", "verdict", "evidence", "error")},
        "final_check": d["final_check"], "eligible": d["eligible_for_order"], "explanation": d["explanation"],
        "order": order, "pnl_row": pnl,
        "account_now": account_state(conn, account_id) if account_id else None,
        "image_url": d["image"]["url"] if d.get("image") else None,
    }

