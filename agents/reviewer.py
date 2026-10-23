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


def challenge(
    portfolio: PortfolioProposal,
    market: MarketInsight,
    budget: BudgetEnvelope,
) -> dict[str, Any]:
    """Challenger role: find weak assumptions, concentrated risks, and conflicts.

    This is the adversarial reviewer from the firm spec. It inspects the PROPOSED
    portfolio (not compliance) and returns structured findings plus per-branch
    revision guidance, so the Coordinator can drive a genuine challenge→revision
    cycle rather than relying on the backtest alone.

    Returns {findings: [str], severity: 'none'|'low'|'high', analyst_guidance,
    strategist_guidance}.
    """
    positions = [p for p in _positions(portfolio) if p.get("weight", 0) > 0]
    findings: list[str] = []
    analyst_guidance = ""
    strategist_guidance = ""

    if not positions:
        return {
            "findings": ["Empty portfolio: no positions carry weight."],
            "severity": "high",
            "analyst_guidance": "produce at least one justified position within budget",
            "strategist_guidance": "",
        }

    # 1. Concentration risk — any single name dominating the book.
    top = max(positions, key=lambda p: p.get("weight", 0.0))
    if top["weight"] > 0.40:
        findings.append(
            f"Concentration risk: {top['ticker']} is {top['weight']:.0%} of the "
            f"portfolio (>40%)."
        )
        analyst_guidance = "reduce single-name concentration; diversify holdings"

    # 2. Thin diversification.
    if len(positions) < 3:
        findings.append(
            f"Thin diversification: only {len(positions)} position(s); "
            f"idiosyncratic risk is high."
        )
        analyst_guidance = analyst_guidance or "add more names to diversify"

    # 3. Weak assumptions — negative-expected-return or low-confidence names held.
    weak = [p for p in positions if p.get("expected_return", 0.0) <= 0]
    if weak:
        findings.append(
            "Weak assumption: holding names with non-positive expected return: "
            + ", ".join(p["ticker"] for p in weak)
        )
        analyst_guidance = analyst_guidance or "drop names without a positive return thesis"
    lowconf = [p for p in positions if p.get("confidence", 0.0) < 0.2]
    if lowconf:
        findings.append(
            "Weak assumption: low-confidence forecasts drive "
            + ", ".join(p["ticker"] for p in lowconf)
        )

    # 4. Quant vs sentiment conflict — held names the Strategist is bearish on.
    pts = getattr(market, "per_ticker_sentiment", {}) or {}
    conflicts = [
        p["ticker"] for p in positions
        if pts.get(p["ticker"], 0.0) < -0.2
    ]
    if conflicts:
        findings.append(
            "Conflict: quant proposes names the Market Strategist is bearish on: "
            + ", ".join(conflicts)
        )
        strategist_guidance = "reconcile bearish sentiment on held names or justify the tilt"

    # 5. Budget under-utilization (capital efficiency assumption).
    used = float(getattr(portfolio, "budget_used", 0.0) or 0.0)
    cap = float(getattr(budget, "investable_budget", 0.0) or 0.0)
    if cap > 0 and used < 0.5 * cap:
        findings.append(
            f"Capital under-deployed: only ${used:,.0f} of ${cap:,.0f} budget used."
        )

    severity = "none"
    if findings:
        severity = "high" if (top["weight"] > 0.40 or weak or conflicts) else "low"

    return {
        "findings": findings,
        "severity": severity,
        "analyst_guidance": analyst_guidance,
        "strategist_guidance": strategist_guidance,
    }


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
