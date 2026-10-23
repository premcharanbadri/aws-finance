"""
Statistical Inference Engine (deterministic ML/stats pipeline).

NOT an LLM agent. A pure, reproducible pipeline the Investment Analyst runs
after the Predictor agent produces raw forecasts:

  1. Confidence from prediction-interval width (conformal-style).
  2. Risk-adjusted ranking with Bayesian shrinkage.
  3. Capped, long-only mean-variance optimization (Black-Litterman-ready).
  4. Budget sizing: convert weights to dollar allocations within the
     BudgetEnvelope from the Accounting & Liquidity Reviewer.

Light dependency (numpy). Swap the optimizer for PyPortfolioOpt/cvxpy and the
confidence step for real conformal prediction in production.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from services.schemas import Forecast


def _confidence_from_interval(f: Forecast) -> float:
    width = max(f.upper - f.lower, 1e-9)
    scale = abs(f.expected_return) + f.volatility + 1e-9
    return float(1.0 / (1.0 + width / scale))


def rank(
    forecasts: list[Forecast],
    risk_free: float = 0.02,
    shrinkage: float = 0.3,
) -> list[dict[str, Any]]:
    """Risk-adjusted ranking with Bayesian shrinkage toward the mean."""
    if not forecasts:
        return []
    mean_er = float(np.mean([f.expected_return for f in forecasts]))
    ranked: list[dict[str, Any]] = []
    for f in forecasts:
        shrunk_er = (1 - shrinkage) * f.expected_return + shrinkage * mean_er
        conf = _confidence_from_interval(f)
        vol = max(f.volatility, 1e-6)
        sharpe_like = (shrunk_er - risk_free) / vol
        ranked.append(
            {
                "ticker": f.ticker,
                "score": float(conf * sharpe_like),
                "confidence": conf,
                "expected_return": shrunk_er,
                "volatility": vol,
                "rationale": f.rationale,
            }
        )
    ranked.sort(key=lambda a: a["score"], reverse=True)
    return ranked


def optimize_weights(
    ranked: list[dict[str, Any]],
    cov: np.ndarray | None = None,
    max_weight: float = 0.35,
    risk_aversion: float = 3.0,
) -> list[dict[str, Any]]:
    """Long-only, capped, fully-invested mean-variance optimization."""
    if not ranked:
        return ranked
    mu = np.array([a["expected_return"] for a in ranked])
    n = len(ranked)
    if cov is None:
        vols = np.array([a["volatility"] for a in ranked])
        cov = np.diag(vols**2)
    try:
        w = np.linalg.pinv(risk_aversion * cov) @ mu
    except np.linalg.LinAlgError:
        w = np.ones(n)
    w = np.clip(w, 0.0, None)
    if w.sum() == 0:
        w = np.ones(n)
    w = w / w.sum()
    w = np.minimum(w, max_weight)
    if w.sum() == 0:
        w = np.ones(n) / n
    w = w / w.sum()
    for a, weight in zip(ranked, w):
        a["weight"] = float(round(weight, 4))
    return ranked


def run_inference(
    forecasts: list[Forecast],
    investable_budget: float = 0.0,
    risk_free: float = 0.02,
    max_weight: float = 0.35,
    top_k: int | None = None,
) -> dict[str, Any]:
    """Rank -> keep top_k -> optimize -> size to budget.

    Returns a JSON-serializable dict matching PortfolioProposal shape.
    """
    ranked = rank(forecasts, risk_free=risk_free)
    if top_k is not None:
        ranked = ranked[:top_k]
    ranked = optimize_weights(ranked, max_weight=max_weight)

    positions = []
    for a in ranked:
        dollars = round(a["weight"] * investable_budget, 2) if investable_budget else 0.0
        positions.append(
            {
                "ticker": a["ticker"],
                "weight": a["weight"],
                "score": round(a["score"], 4),
                "confidence": round(a["confidence"], 4),
                "expected_return": round(a["expected_return"], 4),
                "volatility": round(a["volatility"], 4),
                "dollars": dollars,
                "rationale": a["rationale"],
            }
        )
    return {
        "positions": positions,
        "method": "rank(conformal-confidence x shrunk-sharpe) -> capped MVO -> budget sizing",
        "budget_used": round(
            sum(p["dollars"] for p in positions), 2
        )
        if investable_budget
        else 0.0,
        "n_candidates": len(forecasts),
    }
