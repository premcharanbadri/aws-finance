# Virtual Advisory Firm for Corporate Clients

A platform that coordinates specialized AI agents to help companies align
investment decisions with their financial needs and business strategy.

## Roles (agents)

| Role | Module | Responsibility |
|---|---|---|
| **Relationship Manager** | `orchestrator.recommend` | Coordinates the specialists and delivers one consolidated recommendation. Only this agent talks to the specialists. |
| **Market Strategist** | `agents/marketer.py` | Identifies important markets, growth opportunities, and business exposures (sentiment + themes). |
| **Accounting & Liquidity Reviewer** | `agents/cpa.py` → `agents/cpa_liquidity.py` | Reviews cash requirements, determines the available investment budget, and flags questions for a human CPA. Delegates to the `run_cpa` liquidity engine (reserve/runway/drawdown-tolerance). |
| **Challenger** | `agents/reviewer.challenge` | Finds weak assumptions, concentrated risks, and quant/sentiment conflicts in the proposed portfolio. |
| **Investment Analyst** | `agents/portfolio.py` | Proposes a stock portfolio using historical prices and company financials. |

## Workflow

```
Client profile
  → Accounting & Liquidity Reviewer (budget envelope)
  → Investment Analyst + Market Strategist (in parallel)
  → Challenger critique  →  revision loop (Challenger findings + backtest)
  → Historical backtest: original vs revised vs equal-weight baseline
  → Relationship Manager consolidates → compliance/groundedness gate
```

## Key differentiator

Each specialist's findings directly influence the recommendation. The
Challenger can force a revision even when the backtest passes; the Accounting
reviewer's drawdown tolerance travels with the budget. We compare the
**original** and **revised** portfolios against an **equal-weight baseline**
using **annual return** and **maximum drawdown** (`Recommendation.comparison`).

## Run

```bash
python -m pip install -r requirements.txt
# offline demo (synthetic data, no AWS needed):
DATA_PROVIDER=synthetic python tests/smoke_test.py
python -m pytest Tests/ -q
# UI:
python -m streamlit run app.py
```

Set `BEDROCK_MODEL_ID` (and `AWS_REGION`) to enable live Bedrock/Strands LLM
narration; without them the agents fall back to deterministic logic and still
produce a valid recommendation.

A database/analytics layer (Amazon Redshift + dbt models, CDK infra, and data
loaders) lives under `dbt/`, `infra/`, `advisors/`, and `scripts/` with its own
`Makefile` targets (`load-raw`, `dbt-build`, `advisor-user`, `demo-data`).

