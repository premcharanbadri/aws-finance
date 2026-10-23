"""
Streamlit platform UI.

The user only queries the platform (no data ingestion). The app calls the
Coordinator (orchestrator.recommend) and renders the grounded recommendation,
the portfolio, market sentiment, the budget/liquidity review, and the
compliance disclaimer + human-CPA flags.

Run:  .venv/bin/streamlit run app.py
"""

from __future__ import annotations

import os

import streamlit as st

import orchestrator
from services.data import _INDUSTRIES, get_provider
from services.schemas import to_dict

st.set_page_config(page_title="Virtual Advisory Firm", layout="wide")

st.title("🏛️ Virtual Advisory Firm for Corporate Clients")
st.caption(
    "Specialized AI agents coordinated into one recommendation — "
    "Relationship Manager · Market Strategist · Accounting & Liquidity Reviewer · "
    "Challenger · Investment Analyst"
)

with st.sidebar:
    st.header("Query")
    provider = get_provider()
    industries = sorted(_INDUSTRIES)
    industry = st.selectbox("Industry / market", industries, index=0)
    company = st.text_input("Investing company", value="AcmeCorp")
    query = st.text_area(
        "Your question",
        value=f"Which {industry} names should we invest in this quarter?",
    )
    max_iter = st.slider("Max revision iterations", 1, 5, 3)
    min_sharpe = st.slider("Min acceptable Sharpe", 0.0, 2.0, 0.5, 0.1)
    st.caption(f"Data provider: `{os.getenv('DATA_PROVIDER', 'synthetic')}`")
    run = st.button("Generate recommendation", type="primary")

if run:
    with st.spinner("Advisory firm working: Accounting & Liquidity → Analyst + Strategist (parallel) → Challenger → backtest..."):
        rec = orchestrator.recommend(
            query,
            industry=industry,
            company=company,
            max_iterations=max_iter,
            min_sharpe=min_sharpe,
        )

    status = "✅ Accepted" if rec.accepted else "⚠️ Not accepted (see rationale)"
    st.subheader("Relationship Manager — consolidated recommendation")
    st.markdown(f"**{status}**")
    st.write(rec.rationale)

    col1, col2, col3 = st.columns(3)
    col1.metric("Confidence", f"{rec.confidence:.2f}")
    col2.metric("Iterations", rec.iterations)
    col3.metric("Market sentiment", f"{rec.market.sentiment_score:+.2f}")

    st.markdown("### Investment Analyst — proposed portfolio")
    positions = [to_dict(p) for p in rec.portfolio.positions]
    held = [p for p in positions if p.get("weight", 0) > 0]
    if held:
        st.dataframe(
            [
                {
                    "Ticker": p["ticker"],
                    "Weight": f"{p['weight']:.2%}",
                    "Allocation ($)": f"{p.get('dollars', 0):,.0f}",
                    "Exp. return": f"{p['expected_return']:.2%}",
                    "Confidence": f"{p['confidence']:.2f}",
                }
                for p in held
            ],
            use_container_width=True,
        )
    else:
        st.info("No positions carried weight in this run.")

    st.markdown("### Market Strategist")
    st.write(rec.market.summary)
    if rec.market.themes:
        st.write("Themes: " + ", ".join(rec.market.themes))
    st.caption("Sources: " + ", ".join(rec.market.sources or ["—"]))

    st.markdown("### Challenger")
    findings = list(getattr(rec, "challenger_findings", []) or [])
    if findings:
        for f in findings:
            st.write(f"- {f}")
    else:
        st.success("No weak assumptions, concentration, or conflicts flagged.")

    st.markdown("### Historical backtest — original vs revised vs equal-weight baseline")
    comp = getattr(rec, "comparison", {}) or {}
    if comp:
        def _fmt(v, pct=True):
            if v is None:
                return "—"
            return f"{v:.2%}" if pct else f"{v:.2f}"
        rows = []
        for label, key in (
            ("Original", "original"),
            ("Revised", "revised"),
            ("Equal-weight baseline", "equal_weight_baseline"),
        ):
            leg = comp.get(key, {})
            rows.append({
                "Portfolio": label,
                "Annual return": _fmt(leg.get("annual_return")),
                "Max drawdown": _fmt(leg.get("max_drawdown")),
                "Sharpe": _fmt(leg.get("sharpe"), pct=False),
            })
        st.dataframe(rows, use_container_width=True)
        st.caption(
            "Key differentiator: every specialist's findings feed the revision; "
            "we compare return and maximum drawdown against an equal-weight baseline."
        )

    st.markdown("### Accounting & Liquidity Reviewer")
    b = rec.budget
    st.write(
        f"Investable budget: **${b.investable_budget:,.0f}** "
        f"(cash ${b.cash_available:,.0f}, min buffer ${b.min_cash_buffer:,.0f})"
    )
    st.write(b.rationale)
    if rec.cpa_review_required:
        st.warning("🧾 Human CPA review required.")
        for flag in b.cpa_flags:
            st.write(f"- {flag}")

    st.markdown("---")
    st.caption(rec.disclaimer)

    with st.expander("Raw recommendation (JSON)"):
        st.json(to_dict(rec))
else:
    st.info("Set your query in the sidebar and click **Generate recommendation**.")
