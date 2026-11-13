"""Second-agent review of a proposed decision (Prompt 11).

The analyst call is made by decisions.decide(). With review=True, decide() then makes ONE reviewer call through the
same provider client (same response format, same timeout, no retries), with separate reviewer instructions. The
reviewer sees the SAME frozen snapshot, the analyst's actual choice and named answers, the backend's supporting facts
and, for a chart question, the same stored PNG. It answers one named choice question, "verdict".

Python never trusts the verdict alone: final_check() requires the analyst's answer, the account rules, the strategy
rules (strategy_checks), the chart checks, a valid reviewer reply AND an "accept" verdict. A rejection, timeout or
invalid reply makes the proposal ineligible. verdict_evidence() explains a verdict with the observed values, e.g. the
simulated cash against the cost of the order at the candle close. Nothing here touches the paper account.
"""

import time
from decimal import Decimal

REVIEWER_INSTRUCTIONS = (
    "You are the REVIEWER for a long-only PAPER trading simulation (no real money). Another agent, the analyst, "
    "proposed an action for one frozen snapshot. Check the analyst's proposal against the snapshot data, the backend "
    "facts and these strategy rules: an upward SMA crossover at this candle is a buy signal when the account can "
    "afford the order size; a downward crossover is an exit signal for an open position (close it, or sell the "
    "order size); without a crossover an open position is held and a flat account waits. Sell, hold and close need "
    "an open position. Choose exactly one verdict. Use only the data provided."
)

VERDICTS = {
    "accept": "The proposal follows the strategy rules and the account can carry it out.",
    "reject_insufficient_cash": "A buy that the simulated cash cannot pay for at the candle close.",
    "reject_insufficient_position": "A sell, hold or close without enough shares in the position.",
    "reject_unsupported_signal": "The snapshot's crossover and position do not support this action.",
}

VERDICT_QUESTION = {
    "type": "choice", "name": "verdict",
    "instructions": "Review the analyst's proposal. Which verdict applies?",
    "choices": [{"value": v, "description": d} for v, d in VERDICTS.items()],
}


def order_cost(snapshot: dict) -> Decimal:
    return Decimal(str(snapshot["candle"]["close"])) * snapshot["account"]["order_size"]


def supporting_facts(snapshot: dict, facts: dict) -> dict:
    """The backend facts the reviewer receives: snapshot facts plus the cost of one order at the candle close."""
    return {**facts, "order_cost_at_close": float(order_cost(snapshot))}


def build_review_request(snapshot: dict, model: str, model_input: dict, facts: dict, proposal: str,
                         analyst_answers: dict, analyst_model: str, image_url: str | None = None) -> dict:
    """Provider-neutral reviewer request, in the same format as the analyst request."""
    content = [{"type": "input_text", "text": (
        "Review this proposal. The analyst's choice and named answers, the backend facts and the same frozen "
        "snapshot the analyst used follow" + (", after the same chart image the analyst saw." if image_url else "."))}]
    if image_url:
        content.append({"type": "input_image", "image_url": image_url})
    content.append({"type": "input_json", "data": {
        "analyst": {"model": analyst_model, "proposal": proposal, "named_answers": analyst_answers},
        "backend_facts": supporting_facts(snapshot, facts),
        "snapshot": model_input,
    }})
    return {"model": model,
            "input": [{"role": "developer", "content": [{"type": "input_text", "text": REVIEWER_INSTRUCTIONS}]},
                      {"role": "user", "content": content}],
            "questions": [VERDICT_QUESTION]}


def strategy_checks(action: str, snapshot: dict) -> list[dict]:
    """Does the action follow the strategy rules for this crossover and position (affordability is an account rule)?"""
    crossover = snapshot["indicators"]["crossover_at_candle"]
    position = snapshot["account"]["position"]
    if crossover == "up":
        expected = ["buy", "hold"] if position > 0 else ["buy"]
    elif crossover == "down":
        expected = ["close", "sell"] if position > 0 else ["wait"]
    else:
        expected = ["hold"] if position > 0 else ["wait"]
    return [{"rule": f"strategy: crossover {crossover} with position {position} allows {' or '.join(expected)}",
             "passed": action in expected, "observed": f"proposal {action}"}]


def verdict_evidence(verdict: str | None, proposal: str | None, snapshot: dict) -> list[str]:
    """Observed values that bear on the verdict (Python's explanation; independent of the reviewer's wording)."""
    acct, ind = snapshot["account"], snapshot["indicators"]
    cash, cost = Decimal(str(acct["cash"])), order_cost(snapshot)
    lines = [f"crossover at candle {snapshot['candle']['timestamp']}: {ind['crossover_at_candle']} "
             f"(SMA{ind['sma_short_window']} {ind['sma_short']:.4f} vs SMA{ind['sma_long_window']} "
             f"{ind['sma_long']:.4f})"]
    if proposal == "buy" or verdict == "reject_insufficient_cash":
        lines.append(f"cash ${cash:,.2f} {'≥' if cash >= cost else '<'} cost of {acct['order_size']} share(s) at "
                     f"close ${Decimal(str(snapshot['candle']['close'])):,.2f} = ${cost:,.2f}")
    if proposal in ("sell", "hold", "close") or verdict == "reject_insufficient_position":
        lines.append(f"position {acct['position']} share(s), order size {acct['order_size']}")
    return lines


def run_review(provider, body: dict, errors: tuple) -> dict:
    """Exactly one reviewer call. Returns status (reviewed | malformed_answer | timeout | provider_error), verdict."""
    timeout_cls, provider_error_cls = errors
    started = time.monotonic()
    response = verdict = error = None
    try:
        response = provider.send(body)
        answer = next((a for a in response.get("answers", []) if isinstance(a, dict) and a.get("name") == "verdict"),
                      None)
        if answer is None:
            status, error = "malformed_answer", "no answer named 'verdict' in the reviewer response"
        elif answer.get("choice") not in VERDICTS:
            status, error = "malformed_answer", f"verdict {answer.get('choice')!r} is not one of {list(VERDICTS)}"
        else:
            status, verdict = "reviewed", answer["choice"]
    except timeout_cls as exc:
        status, error = "timeout", str(exc)
    except provider_error_cls as exc:
        status, error = "provider_error", str(exc)
    except (AttributeError, TypeError) as exc:
        status, error = "malformed_answer", f"unexpected response shape: {exc}"
    return {"status": status, "verdict": verdict, "error": error, "response": response,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "provider": provider.name, "model": provider.model, "call_kind": provider.call_kind}


def final_check(*, analyst_status: str, account: list, strategy: list, chart: list, kind: str,
                review: dict | None) -> dict:
    """Every gate a proposal must pass to be eligible for execution, with pass/fail for each."""
    gates = [{"gate": "analyst returned a valid action", "passed": analyst_status == "decided",
              "observed": analyst_status}]
    gates += [{"gate": f"account rule: {c['rule']}", "passed": c["passed"], "observed": c["observed"]} for c in account]
    gates += [{"gate": c["rule"], "passed": c["passed"], "observed": c["observed"]} for c in strategy]
    gates += [{"gate": f"chart: {c['rule']}", "passed": c["passed"], "observed": c["observed"]} for c in chart]
    if review is not None:
        gates.append({"gate": "reviewer returned a valid verdict", "passed": review["status"] == "reviewed",
                      "observed": review["status"] + (f" ({review['error']})" if review.get("error") else "")})
        gates.append({"gate": "reviewer accepted", "passed": review.get("verdict") == "accept",
                      "observed": review.get("verdict") or "no verdict"})
    gates.append({"gate": "application snapshot (test snapshots never trade)", "passed": kind == "application",
                  "observed": kind})
    return {"eligible": all(g["passed"] for g in gates), "gates": gates}
