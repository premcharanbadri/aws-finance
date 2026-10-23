"""Shared contracts. Agent functions return JSON-serializable dictionaries."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Client(StrictModel):
    name: str = 'Demo company'
    cash: FiniteFloat = Field(ge=0)
    monthly_outflow: FiniteFloat = Field(ge=0)
    reserve_months: int = Field(ge=0, le=120)
    commitments: FiniteFloat = Field(ge=0)
    business_description: str
    overlap_tickers: list[str] = Field(default_factory=list)
    overlap_review_threshold: FiniteFloat = Field(default=.45, ge=0, le=1)

class MarketReport(StrictModel):
    summary: str
    priority_markets: list[str]
    portfolio_implications: list[str]
    evidence_ids: list[str]
    missing_information: list[str]

class AccountingNarrative(StrictModel):
    summary: str
    review_questions: list[str]

class PositionReason(StrictModel):
    ticker: str
    reason: str
    evidence_ids: list[str]

class ReviewResponse(StrictModel):
    finding_id: str
    decision: Literal['accepted', 'rejected']
    reason: str

class Portfolio(StrictModel):
    weights: dict[str, FiniteFloat]
    rationale: list[PositionReason]
    responses_to_review: list[ReviewResponse] = Field(default_factory=list)

class Finding(StrictModel):
    finding_id: str
    severity: Literal['minor', 'major']
    issue: str
    evidence_ids: list[str]
    requested_change: str

class Review(StrictModel):
    verdict: Literal['accept', 'revise', 'escalate']
    summary: str
    findings: list[Finding]

    @model_validator(mode='after')
    def findings_for_revision(self):
        if self.verdict == 'revise' and not self.findings:
            raise ValueError('A revision needs at least one actionable finding.')
        if self.verdict == 'accept' and any(f.severity == 'major' for f in self.findings):
            raise ValueError('Cannot accept while listing unresolved major findings.')
        ids = [f.finding_id for f in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError('Finding IDs must be unique.')
        return self
