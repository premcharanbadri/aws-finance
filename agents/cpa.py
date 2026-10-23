"""
Accounting & Liquidity Reviewer agent.

Runs UPSTREAM in the flow. Given the investing company's financials, it:
  1. Determines a prudent investable budget (hard constraint for the optimizer).
  2. Checks liquidity/solvency (working capital, cash buffer, covenants).
  3. Flags open questions that require a *human CPA* (explicit HITL handoff).

It must NOT render autonomous accounting judgments — it bounds the problem
and escalates judgment calls. Deterministic guardrails compute the numbers;
the LLM adds narrative rationale and surfaces nuanced CPA questions.
"""

from __future__ import annotations

from typing import Any

from services.data import DataProvider
from services.llm import get_client
from services.schemas import BudgetEnvelope

SYSTEM = (
    "You are an Accounting & Liquidity Reviewer for a corporate treasury. "
    "You assess how much cash a company can prudently deploy into market "
    "investments without jeopardizing operations, covenants, or solvency. "
    "You NEVER give definitive accounting or tax rulings; instead you flag "
    "questions a licensed human CPA must answer. Be conservative."
)


def _deterministic_budget(fin: dict[str, Any], max_deploy_pct: float = 0.5) -> dict[str, Any]:
    """Compute the hard numbers independently of the LLM."""
    cash = float(fin.get("cash_and_equivalents", 0.0))
    covenant_min = float(fin.get("debt_covenant_min_cash", 0.0))
    obligations = float(fin.get("existing_obligations", 0.0))
    working_capital = float(fin.get("working_capital", 0.0))

    # Keep the larger of covenant minimum or a safety buffer (20% of cash).
    min_buffer = max(covenant_min, 0.2 * cash)
    deployable = max(0.0, cash - min_buffer - obligations)
    budget = round(min(deployable, max_deploy_pct * cash), 2)

    liquidity_ok = working_capital > 0 and budget > 0
    return {
        "cash": cash,
        "min_buffer": round(min_buffer, 2),
        "budget": budget,
        "liquidity_ok": liquidity_ok,
        "working_capital": working_capital,
    }


def review(
    company: str,
    data: DataProvider,
    guidance: str = "",
) -> BudgetEnvelope:
    """Produce a BudgetEnvelope. `guidance` carries Coordinator revision notes."""
    fin = data.get_financials(company)
    calc = _deterministic_budget(fin)

    prompt = (
        f"Company: {company}\n"
        f"Financial snapshot (USD): {fin}\n"
        f"Deterministic pre-computation: {calc}\n"
        f"{('Coordinator guidance: ' + guidance) if guidance else ''}\n\n"
        "Return JSON with keys: investable_budget (number, use the "
        "pre-computed budget unless you justify a lower value), "
        "liquidity_ok (bool), cpa_flags (array of short strings - questions "
        "for a human CPA, e.g. tax treatment, covenant interpretation, "
        "impairment risk), rationale (1-3 sentences)."
    )

    try:
        out = get_client().generate_json(prompt, system=SYSTEM)
    except Exception as exc:  # LLM/credentials unavailable -> deterministic fallback
        out = {
            "investable_budget": calc["budget"],
            "liquidity_ok": calc["liquidity_ok"],
            "cpa_flags": ["LLM unavailable; verify budget manually"],
            "rationale": f"Deterministic budget only ({exc.__class__.__name__}).",
        }

    # Never let the LLM exceed the deterministic cap.
    budget = min(float(out.get("investable_budget", calc["budget"])), calc["budget"])

    return BudgetEnvelope(
        company=company,
        investable_budget=round(budget, 2),
        cash_available=calc["cash"],
        min_cash_buffer=calc["min_buffer"],
        liquidity_ok=bool(out.get("liquidity_ok", calc["liquidity_ok"])),
        cpa_flags=list(out.get("cpa_flags", [])),
        rationale=str(out.get("rationale", "")),
    )
