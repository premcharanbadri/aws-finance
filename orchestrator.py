"""
Coordinator agent (orchestrator).

Topology (enforced here):
  - The Coordinator is the ONLY agent that talks to the branches.
  - Branches never talk to each other.
  - Flow per the agreed design:
        1. CPA (Accounting & Liquidity Reviewer) runs FIRST -> budget envelope.
        2. Investment Analyst and Market Strategist run IN PARALLEL, both
           constrained/informed by the budget + universe.
        3. Coordinator synthesizes, then evaluates (backtest accept_signal +
           groundedness). If rejected and iterations remain, it issues a
           RevisionRequest to the relevant branch and loops.
        4. Compliance Reviewer gates the final output.

In-process first (threads for parallelism). Each branch is a pure function
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

    # --- Step 1: CPA runs first -> budget envelope (hard constraint) --------
    budget = cpa.review(company, data)

    # --- Revision loop over the parallel branches ---------------------------
    analyst_guidance = ""
    strategist_guidance = ""
    accepted = False
    reason = ""
    iterations = 0
    proposal: PortfolioProposal | None = None
    insight: MarketInsight | None = None

    for iterations in range(1, max_iterations + 1):
        # --- Step 2: Analyst + Strategist IN PARALLEL -----------------------
        proposal, insight = _dispatch_parallel(
            industry, data, budget.investable_budget, analyst_guidance, strategist_guidance
        )

        # --- Step 3: objective evaluation (backtest accept/reject) ----------
        weights = {
            p["ticker"]: p["weight"]
            for p in _positions(proposal)
            if p.get("weight", 0) > 0
        }
        prices = {t: data.get_prices(t, 252) for t in weights}
        bt = backtest.backtest(weights, prices)
        accepted, reason = backtest.accept_signal(bt, min_sharpe=min_sharpe)

        if accepted:
            break

        # --- Revision: steer the branch most responsible for the reject -----
        analyst_guidance, strategist_guidance = _revision_guidance(
            reason, bt, insight
        )

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
    )

    # --- Step 4: compliance + groundedness gate -----------------------------
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
    except Exception as exc:
        held = [p["ticker"] for p in _positions(proposal) if p.get("weight", 0) > 0]
        return (
            f"Recommended allocation within {industry}: {held}. "
            f"Budget ${budget.investable_budget:,.0f}. "
            f"Backtest: {reason}. (LLM synthesis unavailable: {exc.__class__.__name__}.)"
        )


def _positions(proposal: PortfolioProposal) -> list[dict[str, Any]]:
    out = []
    for p in getattr(proposal, "positions", []) or []:
        out.append(p if isinstance(p, dict) else to_dict(p))
    return out


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
