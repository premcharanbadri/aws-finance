import json
import logging
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from contracts import Client
from orchestrator import run
from services.data import load_prices, prepare


ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger(__name__)


def money(value):
    return f"${value:,.0f}"


def allocation_table(proposal, budget=None):
    reasons = {
        item["ticker"]: item["reason"]
        for item in proposal["rationale"]
    }

    rows = []

    for ticker, weight in proposal["weights"].items():
        row = {
            "Stock": ticker,
            "Weight": f"{weight:.1%}",
        }

        if budget is not None:
            row["Investment amount"] = money(weight * budget)

        row["Reason"] = reasons.get(
            ticker,
            "Not allocated" if weight == 0 else "No rationale supplied",
        )

        rows.append(row)

    return pd.DataFrame(rows)


def show_agent_report(ui, stage, report):
    titles = {
        "marketing": "1. Market Strategist",
        "accounting": "2. Accounting and Liquidity Reviewer",
        "initial": "3. Initial Portfolio Proposal",
        "initial_review": "4. Independent Review",
        "revision": "5. Revised Portfolio Proposal",
        "final_review": "6. Final Review",
    }

    ui.subheader(titles.get(stage, stage))

    if stage == "marketing":
        ui.write(report["summary"])

        if report["priority_markets"]:
            ui.markdown("**Markets highlighted**")
            for item in report["priority_markets"]:
                ui.write("• " + item)

        ui.markdown("**Implications for the portfolio**")
        for item in report["portfolio_implications"]:
            ui.write("• " + item)

        if report["missing_information"]:
            ui.markdown("**Information still needed**")
            for item in report["missing_information"]:
                ui.write("• " + item)

        ui.caption(
            "Supporting sources: "
            + ", ".join(report["evidence_ids"])
        )

    elif stage == "accounting":
        ui.write(report["summary"])

        a, b, c = ui.columns(3)

        a.metric(
            "Protected operating cash",
            money(report["protected_cash"]),
        )
        b.metric(
            "Committed business funds",
            money(report["committed_cash"]),
        )
        c.metric(
            "Available investment budget",
            money(report["investable_budget"]),
        )

        ui.caption(
            "Investment budget = available cash − operating reserve "
            "− committed funds, floored at zero."
        )

        ui.markdown("**Questions for accounting review**")
        for item in report["review_questions"]:
            ui.write("• " + item)

    elif stage in ("initial", "revision"):
        ui.dataframe(
            allocation_table(report),
            use_container_width=True,
            hide_index=True,
        )

        for item in report["rationale"]:
            ui.caption(
                item["ticker"]
                + " sources: "
                + ", ".join(item["evidence_ids"])
            )

        if stage == "revision":
            ui.markdown("**How the generator addressed the review**")

            for item in report["responses_to_review"]:
                ui.markdown(
                    f"**{item['finding_id']} — "
                    f"{item['decision'].title()}**"
                )
                ui.write(item["reason"])

    else:
        ui.write(report["summary"])

        decisions = {
            "accept": "Accepted by reviewer",
            "revise": "Revision requested",
            "escalate": "Further review required",
        }

        ui.markdown(
            "**Decision: "
            + decisions[report["verdict"]]
            + "**"
        )

        if not report["findings"]:
            ui.write("No actionable findings were returned.")

        for item in report["findings"]:
            ui.markdown(
                f"**{item['finding_id']} — "
                f"{item['severity'].title()} concern**"
            )
            ui.write(item["issue"])
            ui.write("Requested change: " + item["requested_change"])

            ui.caption(
                "Supporting sources: "
                + (
                    ", ".join(item["evidence_ids"])
                    or "Portfolio policy check"
                )
            )

    ui.divider()


def render_result(ui, result):
    ui.header("Your advisory process")

    ui.caption(
        f"Client: {result['context']['client']['name']} · "
        f"Research cutoff: {result['context']['cutoff_date']} · "
        f"Reference: {result['run_id']}"
    )

    ui.write(
        "Each specialist's returned report is shown below, "
        "followed by the final portfolio and historical evaluation."
    )

    if result["mode"] == "simulation":
        ui.warning(
            "REHEARSAL: deterministic outputs, not live AI analysis."
        )

    if result["audit"].get("synthetic_prices"):
        ui.warning(
            "SYNTHETIC PRICES: not actual market performance."
        )

    show_agent_report(ui, "marketing", result["marketing"])
    show_agent_report(ui, "accounting", result["accounting"])

    if "initial" not in result:
        ui.header("No portfolio proposed")
        ui.warning(result.get("message", "No investment budget."))
        return

    show_agent_report(ui, "initial", result["initial"])
    show_agent_report(
        ui,
        "initial_review",
        result["initial_review"],
    )

    if result.get("initial_errors"):
        ui.warning("Initial portfolio constraint checks:")
        for error in result["initial_errors"]:
            ui.write("• " + error)

    if result.get("revised"):
        show_agent_report(ui, "revision", result["final"])
        show_agent_report(
            ui,
            "final_review",
            result["final_review"],
        )
    else:
        ui.info("No revision was performed.")

    if result["status"] != "complete":
        ui.header("Final recommendation pending")
        ui.warning(
            result.get("message", "Further review is required.")
        )

        for error in result.get("final_errors", []):
            ui.write("• " + error)

        ui.caption(
            "The proposal above is not an approved final allocation."
        )
        return

    # Explicit final recommendation
    ui.header("Final Portfolio Recommendation")
    ui.success("The final allocation passed independent review.")
    ui.write(result["final_review"]["summary"])

    budget = result["accounting"]["investable_budget"]
    ui.metric("Investment budget", money(budget))

    table = allocation_table(result["final"], budget)

    table["Change vs initial"] = [
        (
            f"{100 * (weight - result['initial']['weights'].get(ticker, 0)):+.1f} pp"
        )
        for ticker, weight in result["final"]["weights"].items()
    ]

    ui.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
    )

    chart = pd.DataFrame({
        "Initial": result["initial"]["weights"],
        "Final": result["final"]["weights"],
    }).fillna(0)

    ui.bar_chart(chart)

    ui.caption(
        "Weights apply only to the investment budget. "
        "Protected operating cash and committed business funds "
        "are excluded. No trades are executed."
    )

    # Historical evaluation
    ui.header("Historical Performance")

    evaluation = result["evaluation"]
    metrics = evaluation["metrics"]
    reviewed = metrics["Reviewed"]
    baseline = metrics["Equal weight"]

    a, b, c = ui.columns(3)

    a.metric(
        "Final portfolio return",
        f"{reviewed['Return']:.2%}",
    )
    b.metric(
        "Maximum drawdown",
        f"{reviewed['Max drawdown']:.2%}",
    )
    c.metric(
        "Return versus equal weighting",
        f"{100 * (reviewed['Return'] - baseline['Return']):+.2f} pp",
    )

    ui.caption(
        f"Entry close: {result['audit']['entry_date']} · "
        f"Evaluation end: {result['audit']['end_date']}"
    )

    curves = pd.DataFrame(
        evaluation["curves"]
    ).set_index("date")

    curves.index = pd.to_datetime(curves.index)
    ui.line_chart(curves)

    metric_table = pd.DataFrame(metrics).T
    display = metric_table.copy()

    for column in ["Return", "Max drawdown"]:
        display[column] = display[column].map(
            lambda value: f"{value:.2%}"
        )

    for column in ["End value", "Entry fee"]:
        display[column] = display[column].map(
            lambda value: f"${value:,.2f}"
        )

    ui.dataframe(display, use_container_width=True)

    ui.caption(
        "All portfolios use the same dates, capital, and execution "
        "assumptions. Historical performance does not predict "
        "future returns or prove that review improves performance."
    )

    with ui.expander("Evaluation assumptions"):
        for key, value in evaluation["assumptions"].items():
            ui.write(
                key.replace("_", " ").title() + ": " + str(value)
            )


def main():
    st.set_page_config(
        page_title="Boardroom AI",
        page_icon="◈",
        layout="wide",
    )

    st.title("Boardroom AI")
    st.write(
        "Market analysis → cash review → portfolio proposal "
        "→ independent challenge → final recommendation"
    )
    st.caption(
        "Historical research demonstration using internally "
        "prepared data and an illustrative client scenario."
    )

    # Internal files. Customers do not upload CSVs.
    configured_path = os.getenv("BOARDROOM_PRICES_PATH", "").strip()

    if configured_path:
        price_path = Path(configured_path)
        if not price_path.is_absolute():
            price_path = ROOT / price_path
    else:
        price_path = ROOT / "data/all_stocks_5yr.csv"
        if not price_path.exists():
            price_path = ROOT / "data/prices.csv"

    if not price_path.exists():
        st.error(
            "Internal research data is not ready. "
            "Please contact the project team."
        )
        st.stop()

    try:
        prices, fingerprint = load_prices(price_path)

        tickers = [
            ticker.strip().upper()
            for ticker in os.getenv(
                "BOARDROOM_TICKERS",
                "AAPL,MSFT,AMZN,JPM,XOM,JNJ",
            ).split(",")
            if ticker.strip()
        ]

        if not 4 <= len(tickers) <= 8:
            raise ValueError("Configure 4 to 8 stocks.")

        if len(set(tickers)) != len(tickers):
            raise ValueError("Duplicate stocks in the configured universe.")

        missing = [
            ticker
            for ticker in tickers
            if ticker not in prices.columns
        ]

        if missing:
            raise ValueError(f"Stocks absent from internal data: {missing}")

        cutoff = os.getenv("BOARDROOM_CUTOFF_DATE", "2016-12-30")
        end = os.getenv("BOARDROOM_EVALUATION_END", "2017-12-29")
        cap = float(os.getenv("BOARDROOM_MAX_WEIGHT", "0.30"))
        costs = float(os.getenv("BOARDROOM_ENTRY_COST_BPS", "10"))
        mode = os.getenv("BOARDROOM_MODE", "bedrock")

        if mode not in ("bedrock", "simulation"):
            raise ValueError("Invalid execution mode.")

        evidence_path = ROOT / "data/evidence.csv"
        supplemental = (
            evidence_path if evidence_path.exists() else None
        )

    except Exception:
        LOGGER.exception("Internal research setup failed")
        st.error(
            "Internal research settings could not be loaded. "
            "Please ask the project team to inspect the server log."
        )
        st.stop()

    with st.sidebar:
        st.subheader("Research scope")
        st.write("Stocks: " + ", ".join(tickers))
        st.write("Research cutoff: " + cutoff)
        st.write("Evaluation end: " + end)
        st.caption(
            "Data and evaluation settings are maintained internally."
        )

    if mode == "simulation":
        st.warning(
            "REHEARSAL MODE: no AI model calls will be made."
        )

    with st.form("client_profile"):
        st.subheader("Company profile")

        a, b = st.columns(2)

        name = a.text_input(
            "Company name",
            "Demo Manufacturing",
        )
        cash = b.number_input(
            "Available cash ($)",
            min_value=0.0,
            value=1_000_000.0,
            step=10_000.0,
        )
        monthly_outflow = a.number_input(
            "Monthly operating cash outflow ($)",
            min_value=0.0,
            value=100_000.0,
            step=10_000.0,
        )
        reserve_months = b.number_input(
            "Months of operating cash to protect",
            min_value=0,
            max_value=120,
            value=6,
        )
        commitments = a.number_input(
            "Committed business funds ($)",
            min_value=0.0,
            value=200_000.0,
            step=10_000.0,
        )

        business = st.text_area(
            "Business context and objectives",
            (
                "Illustrative manufacturing company considering "
                "market expansion. Independent market-demand "
                "evidence has not yet been supplied."
            ),
        )

        overlap = st.multiselect(
            "Stocks with client-declared business overlap",
            options=tickers,
            default=[],
        )

        st.caption(
            "Business overlap is client-provided context, "
            "not a verified exposure measurement."
        )

        submitted = st.form_submit_button(
            "Generate advisory report",
            type="primary",
        )

    if submitted:
        st.session_state.pop("result", None)

        try:
            client = Client(
                name=name,
                cash=cash,
                monthly_outflow=monthly_outflow,
                reserve_months=reserve_months,
                commitments=commitments,
                business_description=business,
                overlap_tickers=overlap,
                overlap_review_threshold=0.45,
            ).model_dump()

            context, evaluation, audit = prepare(
                prices=prices,
                tickers=tickers,
                cutoff=cutoff,
                end=end,
                client=client,
                cap=cap,
                supplemental=supplemental,
                source=os.getenv(
                    "BOARDROOM_PRICE_SOURCE",
                    "Internally supplied historical prices",
                ),
                fingerprint=fingerprint,
            )

            audit["synthetic_prices"] = (
                os.getenv(
                    "BOARDROOM_SYNTHETIC_PRICES",
                    "false",
                ).lower() == "true"
            )

            with st.status(
                "Advisory team working...",
                expanded=True,
            ) as status:
                result = run(
                    context=context,
                    evaluation=evaluation,
                    audit=audit,
                    mode=mode,
                    cost_bps=costs,
                    emit=st.write,
                    on_report=lambda stage, report: show_agent_report(
                        st,
                        stage,
                        report,
                    ),
                )

                status.update(
                    label=(
                        "Review complete"
                        if result["status"] == "complete"
                        else "Further review required"
                    ),
                    state=(
                        "complete"
                        if result["status"] == "complete"
                        else "error"
                    ),
                    expanded=False,
                )

            st.session_state["result"] = result

            try:
                output_dir = ROOT / "runs"
                output_dir.mkdir(exist_ok=True)

                output_path = (
                    output_dir / f"{result['run_id']}.json"
                )
                output_path.write_text(
                    json.dumps(result, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                LOGGER.exception("Could not save local report")

        except Exception:
            LOGGER.exception("Advisory workflow failed")
            st.error(
                "The review could not be completed. "
                "Please ask the project team to inspect the "
                "server log. Completed reports remain visible "
                "in the progress panel."
            )

    result = st.session_state.get("result")

    if result is not None:
        st.divider()
        st.caption(
            "Results below reflect the last submitted profile."
        )
        render_result(st, result)

        st.download_button(
            "Download complete advisory record",
            data=json.dumps(result, indent=2),
            file_name=f"{result['run_id']}.json",
            mime="application/json",
        )


if __name__ == "__main__":
    main()