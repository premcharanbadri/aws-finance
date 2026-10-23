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

st.set_page_config(page_title="Investment Recommendation Platform", layout="wide")

st.title("📈 Investment Recommendation Platform")
st.caption(
    "Multi-agent system: Coordinator · Investment Analyst (Predictor + "
    "Statistical Inference) · Market Strategist · Accounting & Liquidity Reviewer"
)

with st.sidebar:
    st.header("Query")
    provider = get_provider()
    industries = sorted(_INDUSTRIES)
    industry = st.selectbox("Industry / market", industries, index=0)
    company = st.text_input("Investing company", value="YourCompany")
    query = st.text_area(
        "Your question",
        value=f"Which {industry} names should we invest in this quarter?",
    )
    max_iter = st.slider("Max revision iterations", 1, 5, 3)
    min_sharpe = st.slider("Min acceptable Sharpe", 0.0, 2.0, 0.5, 0.1)

    st.markdown("---")
    st.subheader("Company financials")

    _provider_mode = os.getenv("DATA_PROVIDER", "synthetic").lower()
    financials = None

    if _provider_mode == "aws":
        # Budget comes from the real general ledger in Redshift (Text-to-SQL
        # over liquidity_snapshot). No manual entry needed — show the live
        # figures so it's clear they're GL-derived, not typed in.
        st.caption("Budget is derived from your general ledger in Redshift (live).")
        try:
            from services.financials import get_financials_from_redshift

            _fr = get_financials_from_redshift(company)
            _d = _fr.data
            st.metric(
                "Investable cash (from GL)",
                f"${_d.get('warehouse_investable_cash_usd', 0):,.0f}",
            )
            st.caption(
                f"Unrestricted cash ${_d.get('cash_and_equivalents', 0):,.0f} · "
                f"covenant min ${_d.get('debt_covenant_min_cash', 0):,.0f} · "
                f"runway {_d.get('runway_months', 0)} mo · "
                f"as of {_d.get('as_of_date', 'n/a')}"
            )
            st.caption(f"Source: `{_fr.source}`")
        except Exception as e:
            st.warning(f"Could not read GL budget from Redshift: {e}")
    else:
        # Non-warehouse modes: optional manual entry; blank -> illustrative.
        st.caption(
            "Enter your company's numbers for a real budget, or leave at 0 for "
            "an illustrative sample."
        )
        cash = st.number_input("Cash & equivalents", min_value=0.0, value=0.0, step=1_000_000.0, format="%.0f")
        cur_liab = st.number_input("Current liabilities", min_value=0.0, value=0.0, step=1_000_000.0, format="%.0f")
        obligations = st.number_input("Existing obligations", min_value=0.0, value=0.0, step=500_000.0, format="%.0f")
        covenant_min = st.number_input("Debt covenant min cash", min_value=0.0, value=0.0, step=500_000.0, format="%.0f")
        if cash > 0:
            financials = {
                "cash_and_equivalents": cash,
                "current_liabilities": cur_liab,
                "existing_obligations": obligations,
                "debt_covenant_min_cash": covenant_min,
                "current_assets": cash + cur_liab,
            }

    st.caption(f"Data provider: `{_provider_mode}`")
    run = st.button("Generate recommendation", type="primary")

if run:
    with st.spinner("Agents working (budget → parallel analysis → review)..."):
        rec = orchestrator.recommend(
            query,
            industry=industry,
            financials=financials,
            company=company,
            max_iterations=max_iter,
            min_sharpe=min_sharpe,
        )

    status = "✅ Accepted" if rec.accepted else "⚠️ Not accepted (see rationale)"
    st.subheader(status)
    st.write(rec.rationale)

    col1, col2, col3 = st.columns(3)
    col1.metric("Confidence", f"{rec.confidence:.2f}")
    col2.metric("Iterations", rec.iterations)
    col3.metric("Market sentiment", f"{rec.market.sentiment_score:+.2f}")

    st.markdown("### Proposed portfolio")
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

    st.markdown("### Market strategist")
    st.write(rec.market.summary)
    if rec.market.themes:
        st.write("Themes: " + ", ".join(rec.market.themes))
    st.caption("Sources: " + ", ".join(rec.market.sources or ["—"]))

    st.markdown("### Accounting & liquidity review")
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
