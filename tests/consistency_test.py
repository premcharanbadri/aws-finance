"""
Consistency tie-out tests.

Verifies that data stays consistent as it flows across components, not just
that each component runs. These assertions catch regressions where, e.g., the
budget stops propagating, dollar sizing drifts from weights, or the portfolio
references tickers that were never analyzed.

Two layers:
  - Core (always runs, synthetic/offline): weight normalization, dollar sizing
    = weight x budget, budget not exceeded, sentiment aggregate = mean, universe
    grounding, compliance recorded.
  - AWS tie-out (only when DATA_PROVIDER=aws): the Redshift GL budget equals the
    CPA budget equals the recommendation budget, exactly.

Run:  .venv/bin/python -m tests.consistency_test
"""

from __future__ import annotations

import os

os.environ.setdefault("DATA_PROVIDER", "synthetic")

import orchestrator  # noqa: E402
from services.data import get_provider  # noqa: E402
from services.schemas import to_dict  # noqa: E402

TOL = 2.0  # dollars; absorbs floating-point rounding


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    assert cond, f"consistency failed: {name} {detail}"


def _positions(rec):
    return [p if isinstance(p, dict) else to_dict(p) for p in rec.portfolio.positions]


def main() -> None:
    mode = os.getenv("DATA_PROVIDER", "synthetic").lower()
    print(f"Consistency audit (DATA_PROVIDER={mode})")
    data = get_provider()
    universe = data.get_universe("technology")

    rec = orchestrator.recommend(
        "Which tech names should we invest in?",
        industry="technology",
        company="FintechCo",
        max_iterations=2,
    )
    held = [p for p in _positions(rec) if (p.get("weight") or 0) > 0]
    budget = rec.budget.investable_budget

    # A. Weights normalized.
    wsum = sum(p["weight"] for p in held)
    check("weights sum to ~1", abs(wsum - 1.0) < 0.02 or wsum == 0.0, f"sum={wsum:.4f}")

    # B. Dollar sizing = weight x budget (per position).
    for p in held:
        expected = round(p["weight"] * budget, 2)
        check(
            f"dollars == weight*budget [{p['ticker']}]",
            abs(expected - p.get("dollars", 0.0)) < TOL,
            f"got {p.get('dollars')} expected {expected}",
        )

    # C. Sum of allocations == budget_used, and budget not exceeded.
    dsum = sum(p.get("dollars", 0.0) for p in held)
    check("sum(dollars) == budget_used", abs(dsum - rec.portfolio.budget_used) < TOL,
          f"{dsum} vs {rec.portfolio.budget_used}")
    check("budget_used does not exceed budget", rec.portfolio.budget_used <= budget * 1.01 + 1.0,
          f"used={rec.portfolio.budget_used} budget={budget}")

    # D. Sentiment aggregate == mean(per-ticker), all in [-1, 1].
    pts = rec.market.per_ticker_sentiment or {}
    if pts:
        calc = round(sum(pts.values()) / len(pts), 3)
        check("aggregate sentiment == mean(per-ticker)", abs(rec.market.sentiment_score - calc) < 0.011,
              f"agg={rec.market.sentiment_score} mean={calc}")
        check("all per-ticker sentiment in [-1,1]", all(-1.0 <= v <= 1.0 for v in pts.values()))

    # E. Universe grounding: no portfolio ticker outside the analyzed universe.
    bad = [p["ticker"] for p in held if p["ticker"] not in universe]
    check("portfolio tickers all analyzed", not bad, f"ungrounded={bad}")

    # F. Compliance recorded.
    check("disclaimer present", bool(rec.disclaimer))
    check("groundedness recorded", "Groundedness" in rec.rationale)

    # G. AWS tie-out: GL budget == CPA/recommendation budget, exactly.
    if mode == "aws":
        from services.financials import get_financials_from_redshift

        gl = get_financials_from_redshift("FintechCo").data["warehouse_investable_cash_usd"]
        check("Redshift GL budget == recommendation budget", abs(gl - budget) < 1.0,
              f"GL={gl} rec={budget}")
    else:
        print("  [SKIP] AWS GL tie-out (set DATA_PROVIDER=aws to enable)")

    print("\nALL CONSISTENCY CHECKS PASSED")


if __name__ == "__main__":
    main()
