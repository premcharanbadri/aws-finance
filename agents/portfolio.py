"""
Investment Analyst branch (quantitative).

Structure mirrors the architecture:

    Investment Analyst  (this module's `analyze`)
        └── Predictor agent (LLM)  -> produces Forecast per ticker
                └── forecast tool  -> statistical baseline on real/synthetic
                                       price history (stand-in for a trained
                                       SageMaker model)
        └── Statistical Inference engine (services.inference) -> ranks,
            weights, and sizes to the budget envelope.

The Predictor is LLM-DRIVEN but does not invent numbers: it is given the
statistical baseline (drift/vol from price history + fundamentals) and
reasons about regime/quality to adjust the forecast and explain it. This is
the safe division of labor (LLMs reason about context; math comes from data).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from services import inference
from services.data import DataProvider
from services.llm import get_client
from services.schemas import Forecast, PortfolioProposal, PositionProposal

PREDICTOR_SYSTEM = (
    "You are a Predictor agent for equity forecasting. You are given a "
    "statistical baseline (historical drift and volatility) and fundamentals "
    "for a ticker. Adjust the expected return modestly based on fundamental "
    "quality and provide a prediction interval. You MUST stay close to the "
    "statistical baseline; do not fabricate large moves. Output calibrated, "
    "conservative numbers."
)


def _baseline_stats(prices: np.ndarray) -> tuple[float, float]:
    """Annualized drift and volatility from a price series."""
    if prices.size < 2:
        return 0.0, 0.2
    daily = np.diff(prices) / prices[:-1]
    return float(np.mean(daily) * 252), float(np.std(daily) * np.sqrt(252))


def _predict_one(ticker: str, data: DataProvider) -> Forecast:
    """Predictor agent for a single ticker: baseline + LLM adjustment."""
    prices = data.get_prices(ticker, days=252)
    fund = data.get_fundamentals(ticker)
    base_er, vol = _baseline_stats(prices)

    prompt = (
        f"Ticker: {ticker}\n"
        f"Statistical baseline: expected_return={base_er:.4f}, volatility={vol:.4f}\n"
        f"Fundamentals: {fund}\n\n"
        "Return JSON: expected_return (number, within +/-50% of the baseline), "
        "lower (number), upper (number, a prediction interval around "
        "expected_return), rationale (<=1 sentence)."
    )
    try:
        out = get_client().generate_json(prompt, system=PREDICTOR_SYSTEM)
        er = float(out.get("expected_return", base_er))
        # Clamp the LLM to +/-50% of baseline magnitude to prevent drift.
        span = abs(base_er) * 0.5 + 0.02
        er = max(base_er - span, min(base_er + span, er))
        lower = float(out.get("lower", er - vol))
        upper = float(out.get("upper", er + vol))
        rationale = str(out.get("rationale", "baseline"))
    except Exception as exc:
        er, lower, upper, rationale = base_er, base_er - vol, base_er + vol, (
            f"baseline only ({exc.__class__.__name__})"
        )

    return Forecast(
        ticker=ticker,
        expected_return=round(er, 4),
        lower=round(lower, 4),
        upper=round(upper, 4),
        volatility=round(vol, 4),
        rationale=rationale,
    )


def analyze(
    industry: str,
    data: DataProvider,
    investable_budget: float = 0.0,
    top_k: int = 5,
    max_weight: float = 0.35,
    guidance: str = "",
) -> PortfolioProposal:
    """Run the full quantitative branch for an industry and budget.

    `guidance` carries Coordinator revision notes (e.g. 'reduce concentration',
    'lower risk') which we translate into optimizer parameters.
    """
    # Revision hooks: let the Coordinator steer the optimizer via guidance.
    g = guidance.lower()
    if "concentration" in g or "diversify" in g:
        max_weight = min(max_weight, 0.25)
    if "lower risk" in g or "conservative" in g:
        top_k = max(top_k, 6)

    universe = data.get_universe(industry)
    forecasts = [_predict_one(t, data) for t in universe]

    result = inference.run_inference(
        forecasts,
        investable_budget=investable_budget,
        max_weight=max_weight,
        top_k=top_k,
    )

    positions = [PositionProposal(**p) if _accepts_kwargs(PositionProposal, p) else _coerce(p)
                 for p in result["positions"]]
    return PortfolioProposal(
        positions=positions,
        method=result["method"],
        budget_used=result["budget_used"],
        notes=guidance,
    )


def _accepts_kwargs(cls, data: dict) -> bool:
    try:
        cls(**data)
        return True
    except Exception:
        return False


def _coerce(p: dict[str, Any]) -> PositionProposal:
    """Build a PositionProposal from a superset dict (drops extra keys)."""
    allowed = {"ticker", "weight", "score", "confidence", "expected_return", "volatility", "dollars", "rationale"}
    return PositionProposal(**{k: v for k, v in p.items() if k in allowed})
