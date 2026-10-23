"""CPA Agent ΓÇö Accounting & Liquidity Reviewer.

Reviews a client's cash position, decides how much is safe to invest, and flags
questions a human CPA should confirm. Its only input is the client profile; it
never reads another agent's output (see README "Independence").

Pipeline (per README):
    1. Input     : client profile dict.
    2. Compute   : runway, required reserve, investable budget ΓÇö plain,
                   deterministic Python. This is the source of truth.
    3. Reason    : optional LLM pass (via services.llm) that explains the budget
                   and adds qualitative risk flags / CPA questions. If the LLM is
                   unavailable, a deterministic summary and flags are used instead.
    4. Output    : the structured dict documented in the README.

Reserve policy (HACKATHON PLACEHOLDERS ΓÇö a human CPA would tune these):
    revenue (effective) = expected_monthly_revenue * revenue_haircut  (default 0.85)
    net burn        = max(monthly_expenses - effective_revenue, 0)
    reserve months  = {low: 12, moderate: 6, high: 3} by risk tolerance
    reserve basis   = gross expenses (default) or net of effective revenue
    required reserve = reserve_months * reserve_basis
    runway_months   = cash / net_burn  (None if net_burn <= 0; unlimited)
    investable_budget = max(min(snapshot, timeline) , 0)

Pass a ``policy`` dict to override any placeholder (reserve months, thresholds,
``revenue_haircut``, ``net_reserve``, ``max_drawdown_by_risk``). Free-text
obligation notes (``obligations_text``/``notes``) are parsed into structured
obligations by the LLM and merged non-authoritatively; the summary is guarded
so the LLM cannot state a dollar budget that contradicts the computed one.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional, TypedDict

from services.llm import call_llm_json

# --- Tunable policy placeholders (a human CPA would set these per engagement) ---
RESERVE_MONTHS_BY_RISK = {"low": 12, "moderate": 6, "high": 3}
DEFAULT_RESERVE_MONTHS = 6  # used when risk_tolerance is missing/unknown
NEAR_TERM_MONTHS = 3        # window for "large near-term obligation" flag
LARGE_OBLIGATION_FRACTION = 0.25  # single obligation > 25% of cash is "large"

# Item 5: expected revenue is not guaranteed, so haircut it in the cash-flow
# math. 0.85 means we only count 85% of projected revenue.
REVENUE_HAIRCUT = 0.85

# Item 6: should the reserve use gross expenses, or net of committed revenue?
# Default gross (conservative); a CPA can flip this per engagement.
NET_RESERVE_DEFAULT = False

# Risk constraints exported for the challenger / portfolio agent (item 7).
# Max drawdown the client can stomach, by risk tolerance.
MAX_DRAWDOWN_BY_RISK = {"low": 0.10, "moderate": 0.25, "high": 0.40}
DEFAULT_MAX_DRAWDOWN = 0.25


class CpaResult(TypedDict):
    """Typed shape of run_cpa's output (item 12).

    Lets the orchestrator and downstream agents catch key mismatches early
    instead of discovering a missing field at runtime.
    """
    investable_budget: float
    required_reserve: float
    runway_months: Optional[float]
    runway_is_unlimited: bool
    min_projected_balance: float
    max_drawdown_tolerance: float
    liquidity_need_pct: float
    risk_flags: List[str]
    cpa_questions: List[str]
    data_quality_flags: List[str]
    summary: str


def _num(value: Any, default: float = 0.0):
    """Coerce a possibly-missing/str value to float.

    Returns ``default`` for ``None``/unparseable input. Callers that need to
    *detect* missing input should use :func:`_num_checked` instead.
    """
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _num_checked(value: Any, default: float = 0.0):
    """Like _num but reports whether the value was missing/invalid.

    Returns ``(number, ok)`` where ``ok`` is False when ``value`` was None or
    could not be parsed, so the caller can raise a data-quality flag (item 2).
    """
    if value is None:
        return default, False
    try:
        return float(value), True
    except (TypeError, ValueError):
        return default, False


def project_cash_timeline(cash, net_monthly, obligations_by_month, horizon):
    """Project the month-end cash balance across the horizon (item 4).

    Starts at ``cash``, applies ``net_monthly`` (revenue - expenses, may be
    negative) each month, and subtracts any obligations landing in that month.
    Returns the list of month-end balances (months 1..horizon) and the minimum
    balance reached, so a mid-horizon dip is caught rather than hidden by a
    single end-of-horizon snapshot.
    """
    balances = []
    balance = cash
    months = int(max(horizon, 0))
    for month in range(1, months + 1):
        balance += net_monthly
        balance -= obligations_by_month.get(month, 0.0)
        balances.append(balance)
    min_balance = min(balances) if balances else cash
    return balances, min_balance


def compute_metrics(profile: Dict[str, Any], policy: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Deterministic liquidity math. This is the source of truth for numbers."""
    policy = policy or {}
    reserve_by_risk = policy.get("reserve_months_by_risk", RESERVE_MONTHS_BY_RISK)
    default_reserve_months = policy.get("default_reserve_months", DEFAULT_RESERVE_MONTHS)
    near_term_months = policy.get("near_term_months", NEAR_TERM_MONTHS)
    large_frac = policy.get("large_obligation_fraction", LARGE_OBLIGATION_FRACTION)
    max_dd_by_risk = policy.get("max_drawdown_by_risk", MAX_DRAWDOWN_BY_RISK)
    revenue_haircut = policy.get("revenue_haircut", REVENUE_HAIRCUT)
    net_reserve = policy.get("net_reserve", NET_RESERVE_DEFAULT)

    data_quality_flags: List[str] = []

    cash, cash_ok = _num_checked(profile.get("cash_on_hand"))
    if not cash_ok:
        data_quality_flags.append("cash_on_hand missing or invalid; treated as $0.")
    expenses, exp_ok = _num_checked(profile.get("monthly_expenses"))
    if not exp_ok:
        data_quality_flags.append("monthly_expenses missing or invalid; treated as $0.")
    revenue, rev_ok = _num_checked(profile.get("expected_monthly_revenue"))
    if not rev_ok:
        data_quality_flags.append("expected_monthly_revenue missing or invalid; treated as $0.")
    horizon, hor_ok = _num_checked(profile.get("horizon_months"), 12)
    if not hor_ok:
        data_quality_flags.append("horizon_months missing or invalid; assumed 12 months.")
    if cash < 0:
        data_quality_flags.append("cash_on_hand is negative; client may already be insolvent.")

    risk = str(profile.get("risk_tolerance", "")).strip().lower()
    if risk not in reserve_by_risk:
        data_quality_flags.append(
            f"risk_tolerance '{profile.get('risk_tolerance')}' unrecognized; "
            f"defaulted to {default_reserve_months}-month reserve."
        )

    # Item 5: haircut expected revenue ΓÇö it is projected, not guaranteed.
    effective_revenue = revenue * revenue_haircut

    net_burn = max(expenses - effective_revenue, 0.0)
    net_monthly = effective_revenue - expenses  # signed; positive when cash-flow positive
    reserve_months = reserve_by_risk.get(risk, default_reserve_months)
    # Item 6: reserve on gross expenses (default) or net of haircut revenue.
    reserve_monthly_basis = max(expenses - effective_revenue, 0.0) if net_reserve else expenses
    required_reserve = reserve_months * reserve_monthly_basis

    # Item 1: no more infinite sentinel. Unlimited runway -> None + a flag.
    if net_burn <= 0:
        runway_months = None
        runway_is_unlimited = True
    else:
        runway_months = cash / net_burn
        runway_is_unlimited = False

    obligations = profile.get("obligations") or []
    obligations_due = 0.0
    obligations_by_month: Dict[int, float] = {}
    near_term_large: List[Dict[str, Any]] = []
    for ob in obligations:
        amount = _num(ob.get("amount"))
        due_raw = ob.get("due_in_months")
        if due_raw is None:
            # Item 3 (conservative): missing date -> treat as due within horizon,
            # landing at the end of the horizon, and flag it.
            due_in = horizon
            data_quality_flags.append(
                f"Obligation '{ob.get('description', 'obligation')}' has no "
                f"due_in_months; conservatively treated as due within the horizon."
            )
        else:
            due_in = _num(due_raw, horizon)
        if due_in <= horizon:
            obligations_due += amount
            month_idx = max(int(math.ceil(due_in)), 1)
            obligations_by_month[month_idx] = obligations_by_month.get(month_idx, 0.0) + amount
        if due_in <= near_term_months and cash > 0 and amount > large_frac * cash:
            near_term_large.append(ob)

    # Item 4: month-by-month projection. The budget must respect the *minimum*
    # projected balance, not just the end-of-horizon snapshot.
    balances, min_balance = project_cash_timeline(cash, net_monthly, obligations_by_month, horizon)
    snapshot_budget = cash - required_reserve - obligations_due
    timeline_budget = min_balance - required_reserve
    investable_budget = max(min(snapshot_budget, timeline_budget), 0.0)

    # Item 7: risk constraints the challenger / portfolio agent can consume.
    max_drawdown_tolerance = max_dd_by_risk.get(risk, DEFAULT_MAX_DRAWDOWN)
    liquidity_need = required_reserve + obligations_due
    liquidity_need_pct = (liquidity_need / cash) if cash > 0 else 1.0
    liquidity_need_pct = min(max(liquidity_need_pct, 0.0), 1.0)

    return {
        "cash_on_hand": cash,
        "monthly_expenses": expenses,
        "expected_monthly_revenue": revenue,
        "effective_monthly_revenue": effective_revenue,
        "revenue_haircut": revenue_haircut,
        "net_reserve": net_reserve,
        "horizon_months": horizon,
        "risk_tolerance": risk,
        "net_burn": net_burn,
        "net_monthly": net_monthly,
        "reserve_months": reserve_months,
        "required_reserve": required_reserve,
        "runway_months": runway_months,
        "runway_is_unlimited": runway_is_unlimited,
        "obligations_due_within_horizon": obligations_due,
        "near_term_large_obligations": near_term_large,
        "projected_balances": balances,
        "min_projected_balance": min_balance,
        "investable_budget": investable_budget,
        "max_drawdown_tolerance": max_drawdown_tolerance,
        "liquidity_need_pct": liquidity_need_pct,
        "data_quality_flags": data_quality_flags,
    }


def deterministic_flags(m: Dict[str, Any]) -> List[str]:
    """Risk flags derived purely from the computed metrics (no LLM)."""
    flags: List[str] = []

    # Item 1: runway_months is None when unlimited, so guard before comparing.
    if not m["runway_is_unlimited"] and m["runway_months"] is not None:
        if m["runway_months"] < m["reserve_months"]:
            flags.append(
                f"Thin runway: ~{m['runway_months']:.1f} months of cash, "
                f"below the {m['reserve_months']}-month reserve target."
            )

    if m["investable_budget"] <= 0:
        flags.append(
            "No investable budget: cash does not cover the required reserve "
            "plus obligations due within the horizon."
        )

    # Item 4: a mid-horizon dip below the reserve, even if cash recovers later.
    if m["min_projected_balance"] < m["required_reserve"]:
        flags.append(
            f"Projected cash dips to ${m['min_projected_balance']:,.0f} during the "
            f"horizon, below the ${m['required_reserve']:,.0f} reserve target."
        )

    for ob in m["near_term_large_obligations"]:
        amount = _num(ob.get("amount"))
        desc = ob.get("description", "obligation")
        due_in = ob.get("due_in_months", "?")
        flags.append(
            f"Large near-term obligation: '{desc}' of ${amount:,.0f} due in "
            f"{due_in} months (> {int(LARGE_OBLIGATION_FRACTION * 100)}% of cash)."
        )

    if m["net_burn"] > 0:
        flags.append(
            f"Cash-flow negative: expenses exceed revenue by "
            f"${m['net_burn']:,.0f}/month."
        )

    return flags


def deterministic_summary(m: Dict[str, Any], flags: List[str]) -> str:
    """Plain-language explanation used when the LLM is unavailable."""
    if m["runway_is_unlimited"]:
        runway = "effectively unlimited"
    else:
        runway = f"{m['runway_months']:.1f} months"
    haircut_pct = int(round((1 - m["revenue_haircut"]) * 100))
    parts = [
        f"With ${m['cash_on_hand']:,.0f} cash and a net burn of "
        f"${m['net_burn']:,.0f}/month (expected revenue haircut {haircut_pct}%), "
        f"the runway is {runway}.",
        f"A {m['reserve_months']}-month reserve of ${m['required_reserve']:,.0f} "
        f"is held back, and ${m['obligations_due_within_horizon']:,.0f} of "
        f"obligations fall due within {m['horizon_months']:.0f} months.",
        f"The projected cash low point over the horizon is "
        f"${m['min_projected_balance']:,.0f}.",
        f"That leaves an investable budget of ${m['investable_budget']:,.0f}.",
    ]
    if flags:
        parts.append("Key risks: " + " ".join(flags))
    return " ".join(parts)


def deterministic_questions(m: Dict[str, Any]) -> List[str]:
    """Baseline questions for a human CPA to confirm."""
    questions = [
        "Are the monthly expense and revenue figures representative going forward, "
        "or seasonal/one-off?",
        "Should the required reserve use gross expenses, or net of committed revenue?",
    ]
    if m["near_term_large_obligations"]:
        questions.append(
            "Can any large near-term obligations be deferred, refinanced, or are "
            "they firm commitments?"
        )
    if m["investable_budget"] <= 0:
        questions.append(
            "Is there any line of credit or expected inflow that would create "
            "investable capacity despite the current zero budget?"
        )
    # Item 2: turn each data-quality gap into a CPA confirmation question.
    for dq in m["data_quality_flags"]:
        questions.append(f"Data quality: {dq} Please confirm.")
    return questions


def _build_prompt(profile: Dict[str, Any], m: Dict[str, Any]) -> str:
    """Prompt for the optional LLM reasoning pass."""
    # m is already JSON-friendly (runway_months is None when unlimited).
    return (
        "You are a CPA reviewing a company's liquidity before it invests surplus "
        "cash. Use ONLY the figures provided; do not invent numbers. The "
        "deterministic computations are authoritative ΓÇö do not change them, only "
        "interpret them.\n\n"
        f"Client profile (raw):\n{json.dumps(profile, indent=2, default=str)}\n\n"
        f"Computed metrics:\n{json.dumps(m, indent=2, default=str)}\n\n"
        "Return ONLY a JSON object with these keys:\n"
        '  "summary": a short plain-language explanation of the investable budget '
        "and liquidity position (2-4 sentences).\n"
        '  "risk_flags": array of short strings for liquidity/accounting risks a '
        "CPA would care about.\n"
        '  "cpa_questions": array of short strings a human CPA should confirm.\n'
        "Respond with the JSON object and nothing else."
    )


def parse_freetext_obligations(text: str) -> List[Dict[str, Any]]:
    """Item 8: use the LLM to turn messy obligation prose into structure.

    Example input: "tax bill of about 80k in March, plus a $20,000 lease renewal
    next month". The model interprets amounts/timing; it does NOT do the budget
    arithmetic. Returns a list of {description, amount, due_in_months} dicts, or
    an empty list if the LLM is unavailable or the reply is unusable. The caller
    merges these non-authoritatively and flags them for CPA confirmation.
    """
    if not text or not str(text).strip():
        return []

    prompt = (
        "Extract financial obligations from the text below into structured data. "
        "Interpret approximate amounts ('about 80k' -> 80000) and relative timing "
        "into a number of months from now ('next month' -> 1; name a month -> "
        "months until that month, assuming the next occurrence). Do NOT invent "
        "obligations that are not mentioned.\n\n"
        f"Text:\n{text}\n\n"
        'Return ONLY a JSON object: {"obligations": [{"description": str, '
        '"amount": number, "due_in_months": number}]}. Use null for a field you '
        "cannot determine. Respond with the JSON object and nothing else."
    )
    data = call_llm_json(prompt)
    if not isinstance(data, dict):
        return []
    raw = data.get("obligations")
    if not isinstance(raw, list):
        return []

    parsed: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        amount = _num(item.get("amount"), 0.0)
        if amount <= 0:
            continue  # unusable without an amount
        ob: Dict[str, Any] = {
            "description": str(item.get("description") or "parsed obligation"),
            "amount": amount,
            "source": "llm_freetext",  # mark provenance; not authoritative
        }
        due = item.get("due_in_months")
        if due is not None:
            ob["due_in_months"] = _num(due)
        parsed.append(ob)
    return parsed


def _extract_dollar_figures(text: str) -> List[float]:
    """Pull dollar amounts like $1,200,000 or $9.4M from free text."""
    figures: List[float] = []
    # $1,234,567(.89) with optional K/M/B suffix
    for m in re.finditer(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmMbB])?", text):
        num = float(m.group(1).replace(",", ""))
        suffix = (m.group(2) or "").lower()
        if suffix == "k":
            num *= 1_000
        elif suffix == "m":
            num *= 1_000_000
        elif suffix == "b":
            num *= 1_000_000_000
        figures.append(num)
    return figures


def summary_contradicts_budget(summary: str, budget: float, tolerance: float = 0.02) -> bool:
    """Item 9: does the summary's stated budget contradict the computed one?

    Heuristic guardrail: if the summary mentions an "investable"/"budget"/
    "invest" figure in dollars that differs from ``budget`` by more than
    ``tolerance`` (relative) and by more than $1, treat it as a contradiction.
    Figures that merely restate other known numbers are not the concern here;
    we only police the budget claim to keep the LLM from overriding the math.
    """
    if not summary:
        return False
    lowered = summary.lower()
    # Only scrutinize sentences that talk about the investable budget.
    budget_sentences = [
        s for s in re.split(r"(?<=[.!?])\s+", summary)
        if any(k in s.lower() for k in ("invest", "budget"))
    ]
    if not budget_sentences:
        return False
    for sentence in budget_sentences:
        for fig in _extract_dollar_figures(sentence):
            denom = max(abs(budget), 1.0)
            if abs(fig - budget) > 1.0 and abs(fig - budget) / denom > tolerance:
                return True
    return False


def run_cpa(profile: Dict[str, Any], policy: Dict[str, Any] | None = None) -> CpaResult:
    """Entry point. Review a client profile and return the CPA result.

    The numeric fields always come from deterministic math. The LLM, when
    available, enriches the narrative (summary) and the qualitative lists
    (risk_flags, cpa_questions); otherwise deterministic equivalents are used.

    ``policy`` optionally overrides the reserve/threshold placeholders so a CPA
    can see the effect of, say, a 9-month reserve instead of 6.
    """
    profile = dict(profile)  # shallow copy; we may augment obligations

    # Item 8: parse any free-text obligation notes into structured obligations
    # and merge them in (non-authoritatively) before the math runs.
    freetext = profile.get("obligations_text") or profile.get("notes")
    parsed_obligations: List[Dict[str, Any]] = []
    if isinstance(freetext, str) and freetext.strip():
        parsed_obligations = parse_freetext_obligations(freetext)
        if parsed_obligations:
            existing = list(profile.get("obligations") or [])
            profile["obligations"] = existing + parsed_obligations

    m = compute_metrics(profile, policy)

    det_flags = deterministic_flags(m)
    det_questions = deterministic_questions(m)
    det_summary = deterministic_summary(m, det_flags)

    # Each LLM-parsed obligation should be confirmed by a human CPA.
    for ob in parsed_obligations:
        det_questions.append(
            f"Data quality: obligation '{ob['description']}' (${ob['amount']:,.0f}) "
            f"was parsed from free text; please confirm amount and timing."
        )

    summary = det_summary
    risk_flags = list(det_flags)
    cpa_questions = list(det_questions)

    llm = call_llm_json(_build_prompt(profile, m))
    if isinstance(llm, dict):
        candidate = llm.get("summary")
        if isinstance(candidate, str) and candidate.strip():
            # Item 9: reject an LLM summary whose stated budget contradicts the
            # computed one; keep the deterministic summary instead.
            if summary_contradicts_budget(candidate.strip(), m["investable_budget"]):
                cpa_questions.append(
                    "Data quality: the drafted narrative stated an investable "
                    "figure inconsistent with the computed budget; the computed "
                    "figure was kept. Please review."
                )
            else:
                summary = candidate.strip()
        # Merge LLM-suggested flags/questions on top of the deterministic ones,
        # de-duplicating while preserving order.
        for key, target in (("risk_flags", risk_flags), ("cpa_questions", cpa_questions)):
            extra = llm.get(key)
            if isinstance(extra, list):
                for item in extra:
                    if isinstance(item, str) and item.strip() and item not in target:
                        target.append(item.strip())

    result: CpaResult = {
        "investable_budget": float(m["investable_budget"]),
        "required_reserve": float(m["required_reserve"]),
        # Item 1: None + flag instead of a non-JSON Infinity sentinel.
        "runway_months": m["runway_months"],
        "runway_is_unlimited": m["runway_is_unlimited"],
        "min_projected_balance": float(m["min_projected_balance"]),
        # Item 7: risk constraints for the challenger / portfolio agent.
        "max_drawdown_tolerance": float(m["max_drawdown_tolerance"]),
        "liquidity_need_pct": float(m["liquidity_need_pct"]),
        "risk_flags": risk_flags,
        "cpa_questions": cpa_questions,
        "data_quality_flags": list(m["data_quality_flags"]),
        "summary": summary,
    }
    return result


def sample_client_profiles():
    """Self-contained sample client profiles for the liquidity CPA reference.

    Kept here (not in services.data) so this reference module stays decoupled
    from the wired DataProvider stack.
    """
    return [
        {"client_id": "acme", "company_name": "Acme Corp", "cash_on_hand": 2_000_000,
         "monthly_expenses": 150_000, "expected_monthly_revenue": 120_000,
         "obligations": [{"description": "Term loan payment", "amount": 300_000, "due_in_months": 6}],
         "risk_tolerance": "moderate", "horizon_months": 12},
        {"client_id": "tight", "company_name": "Tight Cash Co", "cash_on_hand": 400_000,
         "monthly_expenses": 120_000, "expected_monthly_revenue": 80_000,
         "obligations": [
             {"description": "Payroll tax", "amount": 90_000, "due_in_months": 2},
             {"description": "Equipment purchase", "amount": 150_000, "due_in_months": 5}],
         "risk_tolerance": "low", "horizon_months": 12},
        {"client_id": "flush", "company_name": "Flush Holdings", "cash_on_hand": 10_000_000,
         "monthly_expenses": 200_000, "expected_monthly_revenue": 350_000,
         "obligations": [], "risk_tolerance": "high", "horizon_months": 36},
    ]


if __name__ == "__main__":
    for p in sample_client_profiles():
        result = run_cpa(p)
        print(f"\n=== {p['company_name']} ({p['client_id']}) ===")
        print(json.dumps(result, indent=2, default=str))
