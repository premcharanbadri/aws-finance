"""
Shared data contracts for the multi-agent system.

Every message that crosses an agent boundary (Coordinator <-> branch) uses
one of these models. Keeping the contract in one place prevents the JSON
drift that otherwise breaks the revision loop.

Pydantic v2. If pydantic is unavailable, we fall back to dataclasses so the
pipeline still runs (the LLM wrapper parses into dicts either way).
"""

from __future__ import annotations

from typing import Any, Literal, Optional

try:
    from pydantic import BaseModel, Field

    _HAS_PYDANTIC = True
except Exception:  # pragma: no cover - fallback path
    _HAS_PYDANTIC = False
    from dataclasses import dataclass, field

    def Field(default=None, **_kwargs):  # type: ignore
        return default


# ---------------------------------------------------------------------------
# Quantitative branch (Investment Analyst / Predictor / Inference)
# ---------------------------------------------------------------------------

if _HAS_PYDANTIC:

    class Forecast(BaseModel):
        """A single ticker forecast produced by the Predictor agent."""

        ticker: str
        expected_return: float = Field(description="Horizon expected return, e.g. 0.08")
        lower: float = Field(description="Lower bound of prediction interval")
        upper: float = Field(description="Upper bound of prediction interval")
        volatility: float = Field(description="Annualized volatility")
        rationale: str = ""

    class PositionProposal(BaseModel):
        ticker: str
        weight: float
        score: float = 0.0
        confidence: float = 0.0
        expected_return: float = 0.0
        volatility: float = 0.0
        rationale: str = ""

    class PortfolioProposal(BaseModel):
        """Output of the Investment Analyst branch."""

        positions: list[PositionProposal] = Field(default_factory=list)
        method: str = ""
        budget_used: float = 0.0
        notes: str = ""

    # -----------------------------------------------------------------------
    # Accounting & Liquidity Reviewer
    # -----------------------------------------------------------------------

    class BudgetEnvelope(BaseModel):
        """Investable budget + liquidity assessment. Produced upstream and
        passed as a hard constraint into portfolio optimization."""

        company: str
        investable_budget: float = Field(description="Max amount prudent to deploy")
        cash_available: float = 0.0
        min_cash_buffer: float = 0.0
        liquidity_ok: bool = True
        cpa_flags: list[str] = Field(
            default_factory=list,
            description="Open questions requiring a human CPA's judgment",
        )
        rationale: str = ""

    # -----------------------------------------------------------------------
    # Qualitative branch (Market Strategist)
    # -----------------------------------------------------------------------

    class MarketInsight(BaseModel):
        """Output of the Market Strategist branch."""

        sentiment_score: float = Field(
            default=0.0, description="Aggregate sentiment, -1 (bearish) .. +1 (bullish)"
        )
        themes: list[str] = Field(default_factory=list)
        per_ticker_sentiment: dict[str, float] = Field(default_factory=dict)
        sources: list[str] = Field(default_factory=list)
        summary: str = ""

    # -----------------------------------------------------------------------
    # Coordinator synthesis + loop control
    # -----------------------------------------------------------------------

    class RevisionRequest(BaseModel):
        """Coordinator -> branch: request a revised proposal."""

        target: Literal["investment_analyst", "market_strategist", "cpa"]
        reason: str
        guidance: str = ""

    class Recommendation(BaseModel):
        """Final synthesized output delivered to the platform."""

        query: str
        industries: list[str] = Field(default_factory=list)
        portfolio: PortfolioProposal
        budget: BudgetEnvelope
        market: MarketInsight
        confidence: float = 0.0
        accepted: bool = False
        iterations: int = 1
        cpa_review_required: bool = False
        disclaimer: str = ""
        rationale: str = ""

else:  # pragma: no cover - dataclass fallback mirrors the models above

    @dataclass
    class Forecast:
        ticker: str
        expected_return: float
        lower: float
        upper: float
        volatility: float
        rationale: str = ""

    @dataclass
    class PositionProposal:
        ticker: str
        weight: float
        score: float = 0.0
        confidence: float = 0.0
        expected_return: float = 0.0
        volatility: float = 0.0
        rationale: str = ""

    @dataclass
    class PortfolioProposal:
        positions: list = field(default_factory=list)
        method: str = ""
        budget_used: float = 0.0
        notes: str = ""

    @dataclass
    class BudgetEnvelope:
        company: str
        investable_budget: float
        cash_available: float = 0.0
        min_cash_buffer: float = 0.0
        liquidity_ok: bool = True
        cpa_flags: list = field(default_factory=list)
        rationale: str = ""

    @dataclass
    class MarketInsight:
        sentiment_score: float = 0.0
        themes: list = field(default_factory=list)
        per_ticker_sentiment: dict = field(default_factory=dict)
        sources: list = field(default_factory=list)
        summary: str = ""

    @dataclass
    class RevisionRequest:
        target: str
        reason: str
        guidance: str = ""

    @dataclass
    class Recommendation:
        query: str
        portfolio: Any
        budget: Any
        market: Any
        industries: list = field(default_factory=list)
        confidence: float = 0.0
        accepted: bool = False
        iterations: int = 1
        cpa_review_required: bool = False
        disclaimer: str = ""
        rationale: str = ""


def to_dict(model: Any) -> dict:
    """Serialize a schema object to a plain dict regardless of backend."""
    if _HAS_PYDANTIC and hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "__dict__"):
        return dict(model.__dict__)
    return dict(model)
