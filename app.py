"""
Streamlit platform UI — Virtual Advisory Firm.

The user picks ONE specialist persona (or the full report), asks a question,
and the app runs the coordinated pipeline once, then renders only that
persona's view. Running the whole pipeline is required because the specialists
depend on each other (the Analyst needs the budget; the Challenger needs the
proposal) — but the user only sees the persona they selected.

Run:  streamlit run app.py
"""

from __future__ import annotations

import os

import streamlit as st

import orchestrator
from services.data import _INDUSTRIES, get_provider
from services.schemas import to_dict

st.set_page_config(page_title="Virtual Advisory Firm", layout="wide")


# ---------------------------------------------------------------------------
# Personas (the five firm specialists + the consolidated full report)
# ---------------------------------------------------------------------------
PERSONAS = {
    "Relationship Manager": "🤝 Consolidated recommendation and the bottom line.",
    "Investment Analyst": "📊 The proposed stock portfolio and allocations.",
    "Market Strategist": "🌐 Market sentiment, themes, and exposures.",
    "Accounting & Liquidity Reviewer": "🧾 Investable budget, runway, and CPA questions.",
    "Challenger": "🔎 Weak assumptions, concentration, and conflicts.",
    "Full report": "🏛️ Everything, in one scrollable view.",
}


# ---------------------------------------------------------------------------
# Small formatting helpers (Streamlit renders markdown: escape $ so figures
# are not parsed as LaTeX math).
# ---------------------------------------------------------------------------
def money(x) -> str:
    try:
        return f"\\${x:,.0f}"
    except (TypeError, ValueError):
        return "—"


def safe(text) -> str:
    return str(text or "").replace("$", "\\$")


def _held_positions(rec):
    return [p for p in (to_dict(x) for x in rec.portfolio.positions) if p.get("weight", 0) > 0]


# ---------------------------------------------------------------------------
# Per-persona panels
# ---------------------------------------------------------------------------
def panel_relationship_manager(rec):
    accepted = rec.accepted
    st.subheader("🤝 Relationship Manager")

    # One-line verdict.
    if accepted:
        st.success("**Recommendation: PROCEED** — the proposal cleared the firm's review.")
    else:
        st.warning("**Recommendation: HOLD** — the proposal did not clear review. See why below.")

    # Headline metrics.
    c1, c2, c3 = st.columns(3)
    c1.metric("Confidence", f"{rec.confidence:.0%}")
    c2.metric("Revision rounds", rec.iterations)
    c3.metric("Investable budget", money(rec.budget.investable_budget))

    # The narrative (short, clean — no appended noise).
    held = _held_positions(rec)
    if held:
        tickers = ", ".join(p["ticker"] for p in held)
        st.markdown(f"**Proposed allocation ({rec.industries[0] if rec.industries else 'market'}):** {tickers}")
    st.write(safe(rec.rationale))

    # "Why" — the decision drivers, as short bullets.
    with st.container():
        st.markdown("**Why this decision**")
        bullets = []
        comp = getattr(rec, "comparison", {}) or {}
        rev = (comp.get("revised") or {})
        base = (comp.get("equal_weight_baseline") or {})
        if rev.get("annual_return") is not None:
            bullets.append(
                f"Revised portfolio: **{rev['annual_return']:.1%}** annual return, "
                f"**{rev['max_drawdown']:.1%}** max drawdown."
            )
        if base.get("annual_return") is not None:
            bullets.append(f"Equal-weight baseline: {base['annual_return']:.1%} return, "
                           f"{base['max_drawdown']:.1%} max drawdown.")
        if rec.budget.investable_budget <= 0:
            bullets.append("Accounting review set the **investable budget to \\$0** "
                           "(cash does not cover reserve + near-term obligations).")
        for b in bullets:
            st.markdown(f"- {b}")

    # Compact status callouts instead of a long paragraph.
    g = getattr(rec, "groundedness", {}) or {}
    cols = st.columns(2)
    with cols[0]:
        if g.get("passed", True):
            st.caption("✅ Groundedness check passed")
        else:
            st.caption("❌ Groundedness issues: " + safe("; ".join(g.get("issues", []))))
    with cols[1]:
        n = len(rec.budget.cpa_flags or [])
        if rec.cpa_review_required:
            st.caption(f"🧾 Human CPA review required ({n} item{'s' if n != 1 else ''}) — see the Accounting view")
        else:
            st.caption("🧾 No human CPA review required")


def panel_investment_analyst(rec):
    st.subheader("📊 Investment Analyst")
    held = _held_positions(rec)
    if held:
        st.markdown(f"Proposed portfolio — **{len(held)}** positions, "
                    f"budget {money(rec.budget.investable_budget)}.")
        st.dataframe(
            [
                {
                    "Ticker": p["ticker"],
                    "Weight": f"{p['weight']:.1%}",
                    "Allocation": f"${p.get('dollars', 0):,.0f}",
                    "Exp. return": f"{p['expected_return']:.1%}",
                    "Confidence": f"{p['confidence']:.2f}",
                }
                for p in held
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No positions cleared the risk/return bar within the budget this run.")


def panel_market_strategist(rec):
    st.subheader("🌐 Market Strategist")
    st.metric("Aggregate sentiment", f"{rec.market.sentiment_score:+.2f}")
    st.write(safe(rec.market.summary))
    if rec.market.themes:
        st.markdown("**Themes:** " + safe(", ".join(rec.market.themes)))
    st.caption("Sources: " + ", ".join(rec.market.sources or ["—"]))


def panel_accounting(rec):
    b = rec.budget
    st.subheader("🧾 Accounting & Liquidity Reviewer")
    c1, c2, c3 = st.columns(3)
    c1.metric("Investable budget", money(b.investable_budget))
    c2.metric("Cash available", money(b.cash_available))
    c3.metric("Min cash buffer", money(b.min_cash_buffer))
    src = getattr(b, "source", "") or ""
    if src:
        st.caption(f"Financials source: `{src}`")
    st.write(safe(b.rationale))
    flags = list(b.cpa_flags or [])
    if flags:
        st.markdown("**Questions / flags for a human CPA**")
        for f in flags:
            st.markdown(f"- {safe(f)}")


def panel_challenger(rec):
    st.subheader("🔎 Challenger")
    findings = list(getattr(rec, "challenger_findings", []) or [])
    if findings:
        st.markdown("The Challenger raised the following against the proposal:")
        for f in findings:
            st.markdown(f"- {safe(f)}")
    else:
        st.success("No weak assumptions, concentration, or conflicts flagged.")


def panel_backtest(rec):
    """The key differentiator — shown with the Relationship Manager / Full report."""
    comp = getattr(rec, "comparison", {}) or {}

    def fmt(v, pct=True):
        if v is None:
            return "—"
        return f"{v:.1%}" if pct else f"{v:.2f}"

    legs_ok = any(
        (comp.get(k) or {}).get("annual_return") is not None
        for k in ("original", "revised", "equal_weight_baseline")
    )
    st.markdown("### Historical backtest — original vs revised vs equal-weight baseline")
    if comp and legs_ok:
        st.dataframe(
            [
                {
                    "Portfolio": label,
                    "Annual return": fmt((comp.get(key) or {}).get("annual_return")),
                    "Max drawdown": fmt((comp.get(key) or {}).get("max_drawdown")),
                    "Sharpe": fmt((comp.get(key) or {}).get("sharpe"), pct=False),
                }
                for label, key in (
                    ("Original", "original"),
                    ("Revised", "revised"),
                    ("Equal-weight baseline", "equal_weight_baseline"),
                )
            ],
            use_container_width=True,
            hide_index=True,
        )
        st.caption("We compare return and maximum drawdown against an equal-weight baseline.")
    else:
        st.info("Backtest comparison unavailable (no priced positions to evaluate).")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.title("🏛️ Virtual Advisory Firm for Corporate Clients")

with st.sidebar:
    st.header("1 · Choose an advisor")
    persona = st.radio(
        "Persona",
        list(PERSONAS.keys()),
        help="Pick one specialist to hear from, or the full report.",
    )
    st.caption(PERSONAS.get(persona, ""))

    st.header("2 · Your question")
    provider = get_provider()
    industry = st.selectbox("Industry / market", sorted(_INDUSTRIES), index=0)
    company = st.text_input("Investing company", value="AcmeCorp")
    query = st.text_area("Question", value=f"Which {industry} names should we invest in this quarter?")

    with st.expander("Advanced"):
        max_iter = st.slider("Max revision iterations", 1, 5, 3)
        min_sharpe = st.slider("Min acceptable Sharpe", 0.0, 2.0, 0.5, 0.1)
        data_src = os.getenv("DATA_PROVIDER", "synthetic").lower()
        st.caption(f"Data provider: `{data_src}`")
        if data_src in ("redshift", "aws"):
            st.caption(
                "Financials come from Amazon Redshift "
                "(`accounting.liquidity_snapshot`); prices/sentiment are "
                "synthetic. Falls back to synthetic if Redshift is unreachable."
            )
        elif data_src == "yfinance":
            st.caption("Prices from yfinance; financials/sentiment synthetic.")
        else:
            st.caption("All data synthetic (offline demo).")

    run = st.button("Ask the advisory firm", type="primary", use_container_width=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if run:
    with st.spinner("Advisory firm working: Accounting & Liquidity → Analyst + Strategist → Challenger → backtest..."):
        rec = orchestrator.recommend(
            query, industry=industry, company=company,
            max_iterations=max_iter, min_sharpe=min_sharpe,
        )

    st.caption(f"Query: _{safe(query)}_  ·  Company: **{safe(company)}**  ·  Industry: **{industry}**")

    if persona == "Relationship Manager":
        panel_relationship_manager(rec)
        panel_backtest(rec)
    elif persona == "Investment Analyst":
        panel_investment_analyst(rec)
    elif persona == "Market Strategist":
        panel_market_strategist(rec)
    elif persona == "Accounting & Liquidity Reviewer":
        panel_accounting(rec)
    elif persona == "Challenger":
        panel_challenger(rec)
    else:  # Full report
        panel_relationship_manager(rec)
        st.divider()
        panel_investment_analyst(rec)
        st.divider()
        panel_market_strategist(rec)
        st.divider()
        panel_challenger(rec)
        st.divider()
        panel_accounting(rec)
        st.divider()
        panel_backtest(rec)

    st.markdown("---")
    st.caption(safe(rec.disclaimer))
    with st.expander("Raw recommendation (JSON)"):
        st.json(to_dict(rec))
else:
    st.info("👈 Pick an advisor and ask a question, then click **Ask the advisory firm**.")
