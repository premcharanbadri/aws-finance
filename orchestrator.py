from datetime import datetime, timezone
from uuid import uuid4

from contracts import Client, MarketReport, Portfolio, Review
from agents import marketer, cpa, portfolio, reviewer
from services.validation import portfolio_errors, validate_evidence_ids
from services.backtest import compare
from services.llm import settings


def run(
    context,
    evaluation,
    audit,
    mode="bedrock",
    cost_bps=10,
    emit=None,
    on_report=None,
):
    if mode not in ("bedrock", "simulation"):
        raise ValueError("Unknown execution mode.")

    context = dict(context)
    context["client"] = Client.model_validate(
        context["client"]
    ).model_dump()

    if any(
        ticker not in context["universe"]
        for ticker in context["client"]["overlap_tickers"]
    ):
        raise ValueError(
            "Client overlap tickers must belong to the allowed universe."
        )

    result = {
        "run_id": uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "model_settings": settings() if mode == "bedrock" else {},
        "context": context,
        "audit": audit,
        "events": [],
        "reports": [],
        "status": "running",
    }

    def event(message):
        result["events"].append(message)
        if emit:
            emit(message)

    def publish(stage, report):
        result["reports"].append({
            "stage": stage,
            "report": report,
        })
        if on_report:
            on_report(stage, report)

    # 1. Market strategist
    event("Market strategist analyzing the company and research evidence.")

    market = MarketReport.model_validate(
        marketer.run(context, mode)
    )
    validate_evidence_ids(market, context)
    marketing = market.model_dump()

    result["marketing"] = marketing
    publish("marketing", marketing)

    # 2. Accounting and liquidity reviewer
    event("Accounting reviewer calculating the investment budget.")

    accounting = cpa.run(context, mode)

    client = context["client"]
    expected_budget = max(
        0.0,
        client["cash"]
        - client["monthly_outflow"] * client["reserve_months"]
        - client["commitments"],
    )

    if abs(accounting["investable_budget"] - expected_budget) > 1e-6:
        raise ValueError(
            "Accounting output conflicts with the client cash policy."
        )

    result["accounting"] = accounting
    publish("accounting", accounting)

    if expected_budget <= 0:
        result.update(
            status="blocked",
            message=(
                "No discretionary investment budget remains after "
                "protecting operating reserves and committed funds."
            ),
        )
        return result

    # 3. Initial portfolio
    event("Portfolio generator preparing the initial allocation.")

    initial = Portfolio.model_validate(
        portfolio.run(
            context,
            marketing,
            accounting,
            mode=mode,
        )
    ).model_dump()

    initial_errors = portfolio_errors(initial, context)

    result["initial"] = initial
    result["initial_errors"] = initial_errors
    publish("initial", initial)

    # 4. Independent review
    event("Independent reviewer checking the initial proposal.")

    initial_review = Review.model_validate(
        reviewer.run(
            context,
            marketing,
            accounting,
            initial,
            initial_errors,
            mode,
        )
    ).model_dump()

    validate_evidence_ids(
        Review.model_validate(initial_review),
        context,
    )

    # A model cannot override hard validation failures.
    if initial_errors and initial_review["verdict"] == "accept":
        initial_review = {
            "verdict": "revise",
            "summary": "Portfolio constraints require correction.",
            "findings": [
                {
                    "finding_id": f"POLICY_{index + 1}",
                    "severity": "major",
                    "issue": error,
                    "evidence_ids": [],
                    "requested_change": "Correct this policy violation.",
                }
                for index, error in enumerate(initial_errors)
            ],
        }

    result["initial_review"] = initial_review
    result["revised"] = False
    publish("initial_review", initial_review)

    final = initial
    final_errors = initial_errors
    final_review = initial_review

    # 5. At most one revision
    if initial_review["verdict"] == "revise":
        event("Portfolio generator responding to the review.")

        final = Portfolio.model_validate(
            portfolio.run(
                context,
                marketing,
                accounting,
                previous=initial,
                review=initial_review,
                mode=mode,
            )
        ).model_dump()

        final_errors = portfolio_errors(
            final,
            context,
            initial_review,
        )

        result["revised"] = True
        publish("revision", final)

        # 6. Final independent review
        event("Independent reviewer checking the revised proposal.")

        final_review = Review.model_validate(
            reviewer.run(
                context,
                marketing,
                accounting,
                final,
                final_errors,
                mode,
            )
        ).model_dump()

        validate_evidence_ids(
            Review.model_validate(final_review),
            context,
        )

        publish("final_review", final_review)

    result.update(
        final=final,
        final_errors=final_errors,
        final_review=final_review,
    )

    if final_errors or final_review["verdict"] != "accept":
        result.update(
            status="escalated",
            message=(
                "Unresolved findings remain. "
                "No final portfolio has been approved."
            ),
        )
        return result

    # Freeze allocations before evaluating future prices.
    event("Portfolio accepted. Running the historical evaluation.")

    variants = {
        "Equal weight": {
            ticker: 1 / len(context["universe"])
            for ticker in context["universe"]
        },
        "Reviewed": final["weights"],
    }

    if not initial_errors:
        variants["Initial"] = initial["weights"]

    result["evaluation"] = compare(
        evaluation,
        variants,
        expected_budget,
        cost_bps,
    )

    result["status"] = "complete"
    event("Historical evaluation completed.")

    return result