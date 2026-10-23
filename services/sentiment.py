"""
Sentiment scoring abstraction.

Mirrors the DataProvider pattern: the Market Strategist depends only on the
SentimentProvider interface, so the scoring backend is a swappable config
choice and the real-AWS swap is zero-friction.

Split of responsibilities (per the hybrid design):
  - SentimentProvider produces the GROUNDED, per-headline NUMERIC score.
    This is the deterministic number the inference engine + revision loop use.
  - The LLM (in agents/marketer.py) still produces themes + narrative.

Backends (SENTIMENT_PROVIDER env var):
  synthetic (default) | comprehend

Keeping sentiment deterministic (Comprehend, not an LLM) is consistent with
the governance principle: the LLM proposes narrative, deterministic services
produce the numbers that drive decisions.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod


class SentimentProvider(ABC):
    @abstractmethod
    def score(self, text: str, target: str | None = None) -> float:
        """Return a sentiment score in [-1.0, 1.0] for `text`.

        If `target` (e.g. a ticker) is given, prefer sentiment directed at
        that entity (targeted sentiment) when the backend supports it.
        """


class SyntheticSentimentProvider(SentimentProvider):
    """Offline default. The synthetic news already carries a sentiment value;
    for free text we use a tiny deterministic lexicon so the pipeline runs
    without AWS. Not meant to be accurate - just reproducible and offline."""

    _POS = {"beats", "raises", "partnership", "expands", "growth", "record", "upgrade", "strong"}
    _NEG = {"scrutiny", "warns", "pressure", "transition", "cut", "downgrade", "miss", "weak", "probe"}

    def score(self, text: str, target: str | None = None) -> float:
        tokens = {t.strip(".,;:").lower() for t in text.split()}
        pos = len(tokens & self._POS)
        neg = len(tokens & self._NEG)
        if pos == neg == 0:
            return 0.0
        return round((pos - neg) / (pos + neg), 3)


class ComprehendSentimentProvider(SentimentProvider):
    """Amazon Comprehend backend.

    Uses DetectTargetedSentiment when a target entity is supplied (sentiment
    *toward* a specific ticker/company), else DetectSentiment. Comprehend
    returns a sentiment label + per-class scores; we fold them into a single
    [-1, 1] signal (Positive - Negative), weighted by confidence.

    Requires `comprehend:DetectSentiment` / `DetectTargetedSentiment` on the
    caller role. Falls back to the synthetic scorer on any error so the
    system degrades gracefully.
    """

    def __init__(self, region: str | None = None) -> None:
        import boto3

        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._client = boto3.client("comprehend", region_name=self.region)
        self._fallback = SyntheticSentimentProvider()

    @staticmethod
    def _fold(scores: dict) -> float:
        """Map Comprehend SentimentScore {Positive,Negative,Neutral,Mixed}
        to a single [-1, 1] value."""
        pos = float(scores.get("Positive", 0.0))
        neg = float(scores.get("Negative", 0.0))
        return round(max(-1.0, min(1.0, pos - neg)), 3)

    def score(self, text: str, target: str | None = None) -> float:
        if not text.strip():
            return 0.0
        try:
            if target:
                resp = self._client.detect_targeted_sentiment(
                    Text=text, LanguageCode="en"
                )
                # Average the sentiment of mentions matching the target.
                vals = []
                for entity in resp.get("Entities", []):
                    for mention in entity.get("Mentions", []):
                        name = mention.get("Text", "")
                        if target.lower() in name.lower():
                            vals.append(self._fold(mention.get("MentionSentiment", {}).get("SentimentScore", {})))
                if vals:
                    return round(sum(vals) / len(vals), 3)
                # Fall through to document-level if no targeted match.
            resp = self._client.detect_sentiment(Text=text, LanguageCode="en")
            return self._fold(resp.get("SentimentScore", {}))
        except Exception:
            return self._fallback.score(text, target)


def get_sentiment_provider() -> SentimentProvider:
    choice = os.getenv("SENTIMENT_PROVIDER", "synthetic").lower()
    if choice == "comprehend":
        try:
            return ComprehendSentimentProvider()
        except Exception:
            # boto3/region unavailable -> stay offline.
            return SyntheticSentimentProvider()
    return SyntheticSentimentProvider()
