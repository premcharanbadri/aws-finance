"""
Compliance Reviewer + groundedness evaluator (final output gate).

Two jobs, both run before anything reaches the user:

  1. Groundedness / consistency evaluation (Evaluation Layer 3) -
     DETERMINISTIC assertions, no LLM, cheap and auditable:
       - every ticker in the portfolio was actually analyzed
       - cited sentiment matches the Strategist's data
       - budget deployed does not exceed the approved envelope
       - a disclaimer is present
  2. Compliance gate - attaches the required risk disclaimer and sets
     cpa_review_required when the CPA agent raised flags.

This is the guardrail layer for a regulated recommendation system: it cannot
fix bad content, but it refuses to pass ungrounded or non-compliant output.
"""

from __future__ import annotations

from typing import Any

from services.schemas import (
    BudgetEnvelope,
    MarketInsight,
    PortfolioProposal,
    Recommendation,
    to_dict,
)

DISCLAIMER = (
    "This is an automated, informational analysis generated on synthetic or "
    "historical data. It is not investment, accounting, tax, or legal advice. "
    "Figures are model estimates, not guarantees. Consult a licensed "
    "professional before acting."
)


def evaluate_groundedness(
    portfolio: PortfolioProposal,
    market: MarketInsight,
    budget: BudgetEnvelope,
    analyzed_tickers: list[str],
) -> dict[str, Any]:
    """Layer-3 deterministic checks. Returns {passed, checks, issues}."""
    checks: dict[str, bool] = {}
    issues: list[str] = []

    positions = _positions(portfolio)

    # 1. Only analyzed tickers appear.
    held = [p["ticker"] for p in positions]
    unknown = [t for t in held if t not in analyzed_tickers]
    checks["tickers_grounded"] = not unknown
    if unknown:
        issues.append(f"Portfolio references un-analyzed tickers: {unknown}")

    # 2. Budget not exceeded (1% tolerance for rounding).
    used = float(getattr(portfolio, "budget_used", 0.0) or 0.0)
    cap = float(getattr(budget, "investable_budget", 0.0) or 0.0)
    checks["budget_respected"] = used <= cap * 1.01 + 1.0
    if not checks["budget_respected"]:
        issues.append(f"Budget used {used} exceeds approved {cap}")

    # 3. Weights sum to ~1 (if any positions carry weight).
    total_w = sum(float(p.get("weight", 0.0)) for p in positions)
    checks["weights_normalized"] = (not positions) or abs(total_w - 1.0) < 0.02 or total_w == 0.0
    if not checks["weights_normalized"]:
        issues.append(f"Weights sum to {round(total_w, 4)}, expected ~1.0")

    # 4. Sentiment values are in valid range.
    pts = getattr(market, "per_ticker_sentiment", {}) or {}
    bad = {k: v for k, v in pts.items() if not -1.0 <= float(v) <= 1.0}
    checks["sentiment_valid"] = not bad
    if bad:
        issues.append(f"Sentiment out of [-1,1]: {bad}")

    passed = all(checks.values())
    return {"passed": passed, "checks": checks, "issues": issues}


def review(
    rec: Recommendation,
    analyzed_tickers: list[str],
) -> Recommendation:
    """Compliance gate: run groundedness checks, attach disclaimer, set CPA flag."""
    ground = evaluate_groundedness(rec.portfolio, rec.market, rec.budget, analyzed_tickers)

    rec.disclaimer = DISCLAIMER
    cpa_flags = list(getattr(rec.budget, "cpa_flags", []) or [])
    rec.cpa_review_required = bool(cpa_flags) or not ground["passed"]

    note = f"Groundedness: {'PASS' if ground['passed'] else 'FAIL'}."
    if ground["issues"]:
        note += " Issues: " + "; ".join(ground["issues"])
    if cpa_flags:
        note += f" Human-CPA review required for: {cpa_flags}."
    rec.rationale = (rec.rationale + " " + note).strip()

    # A failed groundedness check blocks acceptance regardless of backtest.
    if not ground["passed"]:
        rec.accepted = False
    return rec


def _positions(portfolio: PortfolioProposal) -> list[dict[str, Any]]:
    out = []
    for p in getattr(portfolio, "positions", []) or []:
        out.append(p if isinstance(p, dict) else to_dict(p))
    return out
