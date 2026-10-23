"""
Accounting & Liquidity Reviewer agent (adapter to the liquidity engine).

Runs UPSTREAM in the flow. `review(company, data) -> BudgetEnvelope` is the
interface the Coordinator/orchestrator, Challenger, and UI depend on.

Internally this now delegates the budget decision to the richer liquidity
engine in ``agents.cpa_liquidity.run_cpa`` (deterministic reserve/runway/
drawdown logic with an optional, fail-soft LLM pass). This adapter:
  1. builds a client-profile dict from the data provider's financials,
  2. calls run_cpa,
  3. maps the CpaResult onto the BudgetEnvelope contract the rest of the
     system reads (investable_budget, buffer, liquidity_ok, cpa_flags,
     rationale), and also carries the extra risk constraint
     (max_drawdown_tolerance) so a specialist finding reaches the recommendation.

Field approximations are explicit and surfaced as CPA flags (there is no
monthly-expenses/revenue field in the financials snapshot).
"""

from __future__ import annotations

from typing import Any

from agents.cpa_liquidity import run_cpa
from services.data import DataProvider
from services.schemas import BudgetEnvelope


def _profile_from_financials(company: str, fin: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Map a data-provider financials snapshot onto a run_cpa client profile.

    Returns (profile, approximation_notes). The financials snapshot has no
    monthly expenses/revenue, so we approximate conservatively and record how.
    """
    cash = float(fin.get("cash_and_equivalents", 0.0))
    current_liabilities = float(fin.get("current_liabilities", 0.0))
    obligations_amt = float(fin.get("existing_obligations", 0.0))

    notes: list[str] = []
    # Proxy near-term operating outflow: current liabilities spread over 12 months.
    monthly_expenses = current_liabilities / 12.0 if current_liabilities > 0 else 0.0
    notes.append(
        "Approximation: monthly_expenses derived from current_liabilities/12 "
        "(no expense field in financials); confirm actual operating burn."
    )
    # Conservative: take no credit for revenue we cannot see.
    notes.append(
        "Approximation: expected_monthly_revenue assumed $0 (no revenue field); "
        "confirm recurring inflows that could raise investable budget."
    )

    profile = {
        "client_id": company,
        "company_name": company,
        "cash_on_hand": cash,
        "monthly_expenses": monthly_expenses,
        "expected_monthly_revenue": 0.0,
        "obligations": (
            [{"description": "Existing obligations", "amount": obligations_amt, "due_in_months": 6}]
            if obligations_amt > 0 else []
        ),
        "risk_tolerance": "moderate",
        "horizon_months": 12,
    }
    return profile, notes


def review(
    company: str,
    data: DataProvider,
    guidance: str = "",  # accepted for interface symmetry; run_cpa is deterministic
) -> BudgetEnvelope:
    """Produce a BudgetEnvelope by delegating to the liquidity engine."""
    fin = data.get_financials(company)
    profile, approx_notes = _profile_from_financials(company, fin)

    result = run_cpa(profile)

    cash = float(fin.get("cash_and_equivalents", 0.0))
    covenant_min = float(fin.get("debt_covenant_min_cash", 0.0))
    working_capital = float(fin.get("working_capital", 0.0))

    # The required reserve from the engine, floored by any debt covenant minimum.
    reserve = float(result.get("required_reserve", 0.0))
    min_buffer = max(reserve, covenant_min)

    # Budget is the engine's figure, but never more than cash minus the
    # covenant-aware buffer (keeps the covenant as a hard floor).
    engine_budget = float(result.get("investable_budget", 0.0))
    budget = max(0.0, min(engine_budget, cash - min_buffer))

    # Compose CPA flags: engine risk flags + CPA questions + data-quality notes
    # + our field approximations. Deduplicate while preserving order.
    cpa_flags: list[str] = []
    for src in (
        result.get("risk_flags", []),
        result.get("cpa_questions", []),
        result.get("data_quality_flags", []),
        approx_notes,
    ):
        for item in src or []:
            if isinstance(item, str) and item.strip() and item not in cpa_flags:
                cpa_flags.append(item.strip())

    liquidity_ok = working_capital > 0 and budget > 0

    env = BudgetEnvelope(
        company=company,
        investable_budget=round(budget, 2),
        cash_available=round(cash, 2),
        min_cash_buffer=round(min_buffer, 2),
        liquidity_ok=liquidity_ok,
        cpa_flags=cpa_flags,
        rationale=str(result.get("summary", "")),
    )
    # Carry the extra specialist constraint onto the envelope (non-breaking:
    # BudgetEnvelope is pydantic/dataclass; set attribute for downstream use).
    try:
        env.max_drawdown_tolerance = float(result.get("max_drawdown_tolerance", 0.25))
        env.liquidity_need_pct = float(result.get("liquidity_need_pct", 0.0))
    except Exception:
        pass
    return env
