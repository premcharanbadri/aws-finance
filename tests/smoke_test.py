"""
End-to-end smoke test on synthetic data.

Proves the full multi-agent pipeline runs in-process without AWS credentials:
CPA budget -> parallel Analyst+Strategist -> backtest accept/reject + revision
loop -> synthesis -> compliance/groundedness gate.

LLM calls fall back deterministically when Bedrock is unreachable, so this
runs offline in CI. Run:  .venv/bin/python -m tests.smoke_test
"""

from __future__ import annotations

import os

os.environ.setdefault("DATA_PROVIDER", "synthetic")

from agents import cpa, marketer, portfolio, reviewer  # noqa: E402
from services import backtest, inference  # noqa: E402
from services.data import get_provider  # noqa: E402
from services.schemas import Forecast, to_dict  # noqa: E402
import orchestrator  # noqa: E402


def check(name: str, cond: bool) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    assert cond, f"smoke test failed: {name}"


def main() -> None:
    data = get_provider()

    print("1. Data layer")
    universe = data.get_universe("technology")
    check("universe non-empty", len(universe) > 0)
    check("prices default to ~10yr daily history", data.get_prices(universe[0]).size >= 2500)
    check("prices honor explicit length", data.get_prices(universe[0], 252).size == 252)
    check("financials present", "cash_and_equivalents" in data.get_financials("AcmeCorp"))

    print("2. CPA budget envelope")
    budget = cpa.review("AcmeCorp", data)
    check("budget non-negative", budget.investable_budget >= 0)
    check("budget capped by cash", budget.investable_budget <= budget.cash_available)

    print("3. Investment Analyst branch")
    proposal = portfolio.analyze("technology", data, budget.investable_budget, top_k=4)
    check("positions produced", len(proposal.positions) > 0)
    weights = [getattr(p, "weight", 0) for p in proposal.positions]
    check("weights sum ~1", abs(sum(weights) - 1.0) < 0.02 or sum(weights) == 0)

    print("4. Market Strategist branch")
    insight = marketer.research("technology", data)
    check("sentiment in range", -1.0 <= insight.sentiment_score <= 1.0)
    check("per-ticker sentiment present", len(insight.per_ticker_sentiment) > 0)

    print("5. Inference engine (direct)")
    fcs = [Forecast(ticker="X", expected_return=0.1, lower=0.0, upper=0.2, volatility=0.25)]
    res = inference.run_inference(fcs, investable_budget=1_000_000, top_k=1)
    check("inference sizes budget", res["positions"][0]["dollars"] > 0)

    print("6. Backtest + accept signal")
    w = {p.ticker: p.weight for p in proposal.positions if p.weight > 0}
    prices = {t: data.get_prices(t) for t in w}
    bt = backtest.backtest(w, prices)
    check("backtest ok", bt.get("ok") is True)
    accepted, reason = backtest.accept_signal(bt)
    check("accept signal returns reason", isinstance(reason, str) and len(reason) > 0)
    print(f"      backtest={bt} accepted={accepted}")

    print("7. Forecast walk-forward evaluation (Layer 1)")
    def predict_fn(history):
        import numpy as np
        daily = np.diff(history) / history[:-1]
        er = float(np.mean(daily) * 21)
        vol = float(np.std(daily) * (21 ** 0.5))
        return {"expected_return": er, "lower": er - vol, "upper": er + vol}
    ev = backtest.walk_forward_eval(data.get_prices(universe[0], 400), predict_fn)
    check("walk-forward produced windows", ev.get("ok") and ev["windows"] > 0)
    print(f"      eval={ev}")

    print("8. Full orchestrator (Coordinator + revision loop + gate)")
    rec = orchestrator.recommend(
        "Which tech names should we invest in?", industry="technology", max_iterations=2
    )
    d = to_dict(rec)
    check("recommendation has disclaimer", bool(rec.disclaimer))
    check("iterations within cap", 1 <= rec.iterations <= 2)
    check("industries set", rec.industries == ["technology"])
    check("groundedness recorded in rationale", "Groundedness" in rec.rationale)

    print("9. Compliance gate (direct groundedness check)")
    g = reviewer.evaluate_groundedness(proposal, insight, budget, universe)
    check("groundedness passes on valid data", g["passed"])

    print("\nALL SMOKE TESTS PASSED")
    print(f"accepted={rec.accepted} iterations={rec.iterations} "
          f"confidence={rec.confidence} cpa_review={rec.cpa_review_required}")


if __name__ == "__main__":
    main()
