"""
Relationship Manager (Coordinator / orchestrator).

Topology (enforced here):
  - The Relationship Manager is the ONLY agent that talks to the specialists.
  - Specialists never talk to each other.
  - Flow per the Virtual Advisory Firm design:
        1. Accounting & Liquidity Reviewer runs FIRST -> budget envelope.
        2. Investment Analyst and Market Strategist run IN PARALLEL, both
           constrained/informed by the budget + universe.
        3. Challenger critiques the proposed portfolio (weak assumptions,
           concentration, quant/sentiment conflicts); its findings PLUS the
           objective backtest drive the revision loop.
        4. Historical backtest comparison: original vs revised vs equal-weight
           baseline on return and maximum drawdown (the key differentiator).
        5. Compliance/groundedness gate before the consolidated recommendation.

In-process first (threads for parallelism). Each specialist is a pure function
call today; wrapping each as its own AgentCore Runtime later only changes the
transport in `_dispatch_parallel`, not this control flow.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agents import cpa, marketer, portfolio, reviewer
from services import backtest
from services.data import DataProvider, get_provider
from services.llm import get_client
from services.schemas import (
    MarketInsight,
    PortfolioProposal,
    Recommendation,
    to_dict,
)

COORDINATOR_SYSTEM = (
    "You are the Coordinator of an investment-recommendation system. You "
    "synthesize a quantitative portfolio proposal, a market-sentiment "
    "assessment, and a budget/liquidity review into a single, grounded "
    "recommendation about which industries/markets to invest in. You never "
    "contradict the underlying data."
)


def recommend(
    query: str,
    industry: str,
    company: str = "AcmeCorp",
    data: DataProvider | None = None,
    max_iterations: int = 3,
    min_sharpe: float = 0.5,
) -> Recommendation:
    """Run the full multi-agent pipeline and return a gated Recommendation."""
    data = data or get_provider()
    universe = data.get_universe(industry)

    # --- Step 1: Accounting & Liquidity Reviewer first -> budget envelope ---
    budget = cpa.review(company, data)

    # --- Revision loop over the parallel branches ---------------------------
    analyst_guidance = ""
    strategist_guidance = ""
    accepted = False
    reason = ""
    iterations = 0
    proposal: PortfolioProposal | None = None
    insight: MarketInsight | None = None

    original_proposal: PortfolioProposal | None = None
    original_bt: dict[str, Any] = {}
    challenger_findings: list[str] = []

    for iterations in range(1, max_iterations + 1):
        # --- Step 2: Investment Analyst + Market Strategist IN PARALLEL -----
        proposal, insight = _dispatch_parallel(
            industry, data, budget.investable_budget, analyst_guidance, strategist_guidance
        )

        # --- Step 3a: Challenger critiques the PROPOSED portfolio -----------
        crit = reviewer.challenge(proposal, insight, budget)
        challenger_findings = crit["findings"]

        # --- Step 3b: objective evaluation (backtest accept/reject) ---------
        weights = {
            p["ticker"]: p["weight"]
            for p in _positions(proposal)
            if p.get("weight", 0) > 0
        }
        prices = {t: data.get_prices(t, 252) for t in weights}
        bt = backtest.backtest(weights, prices)
        accepted, reason = backtest.accept_signal(bt, min_sharpe=min_sharpe)

        # Record the FIRST iteration as the "original" for comparison.
        if iterations == 1:
            original_proposal = proposal
            original_bt = bt

        # Challenger can veto an otherwise-accepted proposal on high-severity
        # findings, forcing a revision (specialist findings influence outcome).
        challenger_blocks = crit["severity"] == "high"

        if accepted and not challenger_blocks:
            break

        # --- Revision: combine backtest + Challenger guidance ---------------
        bt_analyst, bt_strategist = _revision_guidance(reason, bt, insight)
        analyst_guidance = crit["analyst_guidance"] or bt_analyst
        strategist_guidance = crit["strategist_guidance"] or bt_strategist

    # --- Step 4: historical backtest comparison (KEY DIFFERENTIATOR) --------
    # original vs revised vs equal-weight baseline, on return + max drawdown.
    revised_weights = {
        p["ticker"]: p["weight"] for p in _positions(proposal) if p.get("weight", 0) > 0
    }
    revised_prices = {t: data.get_prices(t, 252) for t in revised_weights}
    revised_bt = backtest.backtest(revised_weights, revised_prices)
    baseline_prices = {t: data.get_prices(t, 252) for t in universe}
    baseline_bt = backtest.benchmark_equal_weight(baseline_prices)
    comparison = _build_comparison(original_bt, revised_bt, baseline_bt)

    # --- Synthesis (LLM narrative over grounded numbers) --------------------
    rationale = _synthesize(query, industry, proposal, insight, budget, reason)

    rec = Recommendation(
        query=query,
        industries=[industry],
        portfolio=proposal,
        budget=budget,
        market=insight,
        confidence=_confidence(proposal),
        accepted=accepted,
        iterations=iterations,
        rationale=rationale,
        challenger_findings=challenger_findings,
        comparison=comparison,
    )

    # --- Compliance + groundedness gate -------------------------------------
    rec = reviewer.review(rec, analyzed_tickers=universe)
    return rec


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _dispatch_parallel(industry, data, budget, analyst_guidance, strategist_guidance):
    """Run the two branches concurrently (in-process). Swap bodies for
    AgentCore `invoke` calls to distribute later."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_analyst = pool.submit(
            portfolio.analyze, industry, data, budget, 5, 0.35, analyst_guidance
        )
        f_strategist = pool.submit(
            marketer.research, industry, data, None, strategist_guidance
        )
        return f_analyst.result(), f_strategist.result()


def _revision_guidance(reason: str, bt: dict[str, Any], insight: MarketInsight):
    """Map a reject reason to concrete guidance for each branch."""
    analyst, strategist = "", ""
    r = reason.lower()
    if "sharpe" in r:
        analyst = "lower risk; favor higher risk-adjusted names and diversify"
    if "drawdown" in r:
        analyst = "reduce concentration to limit drawdown"
    # If sentiment strongly disagrees with the quant tilt, ask for context.
    if insight is not None and getattr(insight, "sentiment_score", 0.0) < -0.2:
        strategist = "sentiment is bearish; re-check which names have support"
    if not analyst:
        analyst = "revise proposal to improve risk-adjusted return"
    return analyst, strategist


def _synthesize(query, industry, proposal, insight, budget, reason) -> str:
    prompt = (
        f"User query: {query}\nIndustry: {industry}\n"
        f"Portfolio: {to_dict(proposal)}\n"
        f"Market insight: {to_dict(insight)}\n"
        f"Budget envelope: {to_dict(budget)}\n"
        f"Backtest decision: {reason}\n\n"
        "Write a 3-4 sentence recommendation explaining which names/industry "
        "to invest in and why, grounded ONLY in the data above."
    )
    try:
        return get_client().generate(prompt, system=COORDINATOR_SYSTEM, max_tokens=400)
    except Exception:
        held = [p["ticker"] for p in _positions(proposal) if p.get("weight", 0) > 0]
        names = ", ".join(held) if held else "no positions cleared the risk/return bar"
        budget_str = f"USD {budget.investable_budget:,.0f}"
        return (
            f"Within the {industry} universe, the recommended allocation is {names}. "
            f"Investable budget: {budget_str}. "
            f"Backtest outcome: {reason}. "
            f"(Narrative generated deterministically; live model narration was unavailable.)"
        )


def _positions(proposal: PortfolioProposal) -> list[dict[str, Any]]:
    out = []
    for p in getattr(proposal, "positions", []) or []:
        out.append(p if isinstance(p, dict) else to_dict(p))
    return out


def _build_comparison(original_bt, revised_bt, baseline_bt) -> dict[str, Any]:
    """Assemble the original / revised / equal-weight comparison.

    Each leg exposes annual_return and max_drawdown (the two spec metrics),
    plus sharpe/volatility when available. Missing legs are marked not-ok.
    """
    def leg(bt: dict[str, Any]) -> dict[str, Any]:
        if not bt or not bt.get("ok"):
            return {"ok": False, "annual_return": None, "max_drawdown": None}
        return {
            "ok": True,
            "annual_return": bt.get("annual_return"),
            "max_drawdown": bt.get("max_drawdown"),
            "sharpe": bt.get("sharpe"),
            "annual_volatility": bt.get("annual_volatility"),
        }

    return {
        "original": leg(original_bt),
        "revised": leg(revised_bt),
        "equal_weight_baseline": leg(baseline_bt),
        "metrics": ["annual_return", "max_drawdown"],
    }


def _confidence(proposal: PortfolioProposal) -> float:
    pos = [p for p in _positions(proposal) if p.get("weight", 0) > 0]
    if not pos:
        return 0.0
    return round(sum(p.get("confidence", 0.0) for p in pos) / len(pos), 4)


if __name__ == "__main__":
    os.environ.setdefault("DATA_PROVIDER", "synthetic")
    out = recommend(
        "Which tech names should we invest in this quarter?",
        industry="technology",
        max_iterations=2,
    )
    print(to_dict(out))
