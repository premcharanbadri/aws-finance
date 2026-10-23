"""
Market Strategist branch (qualitative).

Performs market research and marketing analytics: aggregates news/sentiment
for the candidate tickers, identifies themes, and summarizes the market
narrative for the Coordinator. Runs in PARALLEL with the Investment Analyst
and communicates only with the Coordinator.

Data access goes through the DataProvider (synthetic news now; S3 + vector
store RAG in production). The LLM synthesizes; the per-ticker sentiment
numbers come from the data so they stay grounded and auditable.
"""

from __future__ import annotations

from typing import Any

from services.data import DataProvider
from services.llm import get_client
from services.schemas import MarketInsight

SYSTEM = (
    "You are a Market Strategist. Given recent news and sentiment data for a "
    "set of tickers, identify the dominant market themes and summarize the "
    "narrative. Ground every claim in the provided data; do not invent "
    "headlines or numbers. Be concise and balanced."
)


def _aggregate_sentiment(data: DataProvider, tickers: list[str]) -> tuple[dict[str, float], list[str], list[str]]:
    """Deterministic sentiment aggregation from the data layer."""
    per_ticker: dict[str, float] = {}
    sources: set[str] = set()
    headlines: list[str] = []
    for t in tickers:
        news = data.get_news(t, limit=5)
        if not news:
            continue
        per_ticker[t] = round(sum(n["sentiment"] for n in news) / len(news), 3)
        for n in news:
            sources.add(n["source"])
            headlines.append(f"[{n['sentiment']:+.2f}] {n['headline']} ({n['source']})")
    return per_ticker, sorted(sources), headlines


def research(
    industry: str,
    data: DataProvider,
    tickers: list[str] | None = None,
    guidance: str = "",
) -> MarketInsight:
    """Run the qualitative branch for an industry / candidate tickers."""
    if tickers is None:
        tickers = data.get_universe(industry)

    per_ticker, sources, headlines = _aggregate_sentiment(data, tickers)
    agg = round(sum(per_ticker.values()) / len(per_ticker), 3) if per_ticker else 0.0

    prompt = (
        f"Industry: {industry}\n"
        f"Per-ticker average sentiment: {per_ticker}\n"
        f"Aggregate sentiment: {agg}\n"
        f"Recent headlines:\n" + "\n".join(headlines[:20]) + "\n"
        f"{('Coordinator guidance: ' + guidance) if guidance else ''}\n\n"
        "Return JSON: themes (array of <=4 short strings), summary "
        "(2-3 sentences grounded ONLY in the headlines above)."
    )
    try:
        out = get_client().generate_json(prompt, system=SYSTEM)
        themes = list(out.get("themes", []))
        summary = str(out.get("summary", ""))
    except Exception as exc:
        themes = ["sentiment aggregation only"]
        summary = f"LLM unavailable ({exc.__class__.__name__}); aggregate sentiment {agg}."

    return MarketInsight(
        sentiment_score=agg,
        themes=themes,
        per_ticker_sentiment=per_ticker,
        sources=sources,
        summary=summary,
    )
