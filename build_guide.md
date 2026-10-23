# Investment Recommendation Multi-Agent System — Build Guide

A hierarchical multi-agent system that recommends which industries/markets a
company should invest in, backing portfolio proposals with market-sentiment
context. Built on the `aws-finance` scaffold using **Streamlit, Python,
Amazon Bedrock, Strands, and AgentCore**.

---

## 1. Architecture

```
                         ┌──────────────────────────┐
          User  ───────► │  Streamlit Platform (app) │
                         └────────────┬──────────────┘
                                      │ query
                         ┌────────────▼──────────────┐
                         │   Coordinator (orchestrator)│
                         │   • CPA-first budgeting     │
                         │   • parallel dispatch       │
                         │   • revision loop           │
                         │   • synthesis + gate        │
                         └──┬─────────┬─────────┬──────┘
             budget first   │         │         │
        ┌───────────────────▼──┐  ┌───▼─────┐  ┌▼───────────────┐
        │ Accounting &         │  │ Invest. │  │ Market         │
        │ Liquidity Reviewer   │  │ Analyst │  │ Strategist     │
        │ (agents/cpa.py)      │  │(portfolio)│ (marketer.py)  │
        │ budget + CPA flags   │  │  │      │  │ sentiment/themes│
        └──────────────────────┘  │  ▼      │  └────────────────┘
                                   │ Predictor (LLM)            
                                   │  └► ML forecast tool       
                                   │ Statistical Inference      
                                   │ (services/inference.py)    
                                   └────────────┘               
                                      │
                         ┌────────────▼──────────────┐
                         │ Compliance Reviewer         │
                         │ (agents/reviewer.py)        │
                         │ groundedness + disclaimer   │
                         └─────────────────────────────┘

        Data access ONLY through services/data.py (DataProvider).
        Agents never touch a raw data source.
```

**Topology rules (enforced in `orchestrator.py`):**
- The Coordinator is the only agent that talks to the branches.
- Branches never talk to each other.
- Analyst and Strategist run in parallel; the CPA runs first to set the budget.
- The Coordinator can loop a proposal back for revision.

### File → component map

| Component | File |
|---|---|
| Platform UI | `app.py` |
| Coordinator | `orchestrator.py` |
| Investment Analyst + Predictor | `agents/portfolio.py` |
| Market Strategist | `agents/marketer.py` |
| Accounting & Liquidity Reviewer | `agents/cpa.py` |
| Compliance Reviewer + groundedness eval | `agents/reviewer.py` |
| Statistical Inference engine | `services/inference.py` |
| Backtest + forecast evaluation | `services/backtest.py` |
| Data layer (interface + providers) | `services/data.py` |
| Shared contracts | `services/schemas.py` |
| Bedrock LLM wrapper | `services/llm.py` |
| End-to-end test | `tests/smoke_test.py` |

---

## 2. Quick start (offline, synthetic data)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Prove the whole pipeline end-to-end (no AWS needed):
.venv/bin/python -m tests.smoke_test

# Run the platform:
.venv/bin/streamlit run app.py
```

With no Bedrock credentials, every LLM call falls back to a deterministic
path, so the system runs fully offline. With AWS credentials + Bedrock access,
the agents use GPT via Bedrock Converse automatically.

Environment variables:

| Var | Default | Purpose |
|---|---|---|
| `DATA_PROVIDER` | `synthetic` | `synthetic` \| `yfinance` \| `aws` |
| `BEDROCK_MODEL_ID` | `us.openai.gpt-5.6-luna` | Bedrock inference profile |
| `AWS_REGION` | `us-east-1` | Bedrock region |

---

## 3. The Statistical Inference Engine

A **deterministic** (non-LLM) pipeline in `services/inference.py`. The
LLM-driven Predictor produces forecasts; this engine turns them into a sized
portfolio:

1. **Confidence calibration** — from prediction-interval width (conformal
   stand-in). Narrow interval relative to signal ⇒ higher confidence.
2. **Risk-adjusted ranking + Bayesian shrinkage** — `score = confidence ×
   (shrunk_return − rf) / volatility`. Shrinkage pulls noisy forecasts toward
   the cross-sectional mean.
3. **Mean-variance optimization** — long-only, capped (`max_weight`),
   fully-invested. Black-Litterman-ready (forecasts are "views").
4. **Budget sizing** — weights × the CPA's investable budget → dollar
   allocations.

**Why the Predictor is an LLM but doesn't invent numbers:** LLMs are poor at
numeric time-series regression. The Predictor is given a *statistical
baseline* (drift/vol from price history) and only adjusts it modestly based on
fundamentals/regime, clamped to ±50% of baseline. Math comes from data; the
LLM supplies context and explanation.

---

## 4. Evaluation (3-layer pyramid)

| Layer | What | Where | Metric |
|---|---|---|---|
| 1. Forecast quality | Does the Predictor beat naive? | `backtest.walk_forward_eval` | directional accuracy, MAE, interval coverage |
| 2. Portfolio | Is the portfolio sound? | `backtest.backtest` + `accept_signal` + `benchmark_equal_weight` | Sharpe, max drawdown, vs. equal-weight |
| 3. Agent output | Is the output grounded & compliant? | `reviewer.evaluate_groundedness` | tickers-grounded, budget-respected, weights-normalized, sentiment-valid |

**Walk-forward only** (never random splits) to avoid look-ahead leakage.
Production: add Ragas / Bedrock model-evaluation for the RAG layer; a formal
backtest harness with benchmark comparison.

---

## 5. Data strategy

Agents depend only on the `DataProvider` interface, so the source is a config
choice and the real-data swap is zero-friction.

| Provider | Status | Prices/Fundamentals | News |
|---|---|---|---|
| `SyntheticDataProvider` | **default, built** | industry-standard generator | canned + sentiment |
| `YFinanceDataProvider` | stub (prices override ready) | yfinance (free) | synthetic |
| `AwsDataProvider` | stub | Redshift | S3 + vector store |

**Synthetic generator (industry-standard, aligned to 2015–2024 daily):**
- 10 years × ~252 trading days = 2,520 daily points (matches yfinance plan).
- **CAPM market factor** → realistic cross-asset correlation (~0.6), which
  also fixes mean-variance over-concentration.
- **Regime-switching volatility** (calm/turbulent) → volatility clustering.
- **Student-t shocks** → fat tails (excess kurtosis > 0).
- **NaN fundamentals injected** → downstream code hardened for real-world gaps.

**Going to real data (2015–2024 yfinance → later Redshift):** keep units and
shape identical to `services/schemas.py`. Required for `AwsDataProvider`:
Redshift endpoint + DB + credentials (via **Secrets Manager ARN**, not raw
creds) and the table/column names for prices and fundamentals; S3 bucket +
vector store (Bedrock Knowledge Base ID or OpenSearch endpoint) for news.

---

## 6. Governance & guardrails (4 layers)

The principle: **the LLM proposes; deterministic gates dispose.**

1. **Data access governance** — agents hold no data credentials; only the
   `DataProvider` is credentialed. Production: read-only, table-scoped IAM for
   the provider; Bedrock-invoke-only for agents. *To add: access-logging
   decorator on the provider.*
2. **I/O guardrails** — `reviewer.py` groundedness checks + mandatory
   disclaimer (built). *To add: Amazon Bedrock Guardrails via
   `guardrailIdentifier` in `llm.py`; treat retrieved docs as untrusted
   content, never instructions.*
3. **Decision/financial governance** — CPA budget cap, `max_weight`
   concentration limit, Sharpe/drawdown accept gate (built). *To add: an
   Investment Policy module (`services/policy.py`): max position, sector
   concentration, prohibited list, min liquidity, max volatility — checked
   before output, feeding the revision loop as a reject reason.*
4. **Observability & audit** — persist each `Recommendation` with its inputs,
   revision history, and gate results; pin model + prompt versions;
   AgentCore Memory + tracing for lineage.

---

## 7. Phased roadmap

**Phase 1 — Local, in-process, synthetic (DONE).**
All agents run in one process; `ThreadPoolExecutor` parallelizes the branches;
synthetic data; smoke test green.

**Phase 2 — Real data, still in-process.**
Implement `YFinanceDataProvider.get_prices` (2015–2024) then `AwsDataProvider`
(Redshift/S3/vector store). No agent changes. Add `services/policy.py` and the
access-logging decorator.

**Phase 3 — Distributed on AgentCore.**
Wrap each agent as its own AgentCore Runtime (pattern in `hackathon-kit/`):

```bash
export AGENTCORE_EXECUTION_ROLE_ARN=$(.venv/bin/python hackathon-kit/setup_agentcore_role.py)
.venv/bin/agentcore configure --entrypoint <agent>.py --name <agent> \
  --region "$AWS_REGION" --deployment-type direct_code_deploy \
  --requirements-file requirements.txt \
  --execution-role "$AGENTCORE_EXECUTION_ROLE_ARN" --non-interactive
.venv/bin/agentcore deploy --agent <agent> --env "BEDROCK_MODEL_ID=$BEDROCK_MODEL_ID"
```

Only the Coordinator's `_dispatch_parallel` changes — from in-process calls to
AgentCore `invoke`. Control flow, schemas, and evaluation stay identical.
Add Bedrock Guardrails, AgentCore Memory, and decision-lineage persistence.

---

## 8. Known simplifications (swap for production)

- MVO uses a diagonal covariance (independence) → use full estimated Σ +
  Black-Litterman (`PyPortfolioOpt`/`cvxpy`); the correlated synthetic data
  already reduces over-concentration.
- Confidence is a heuristic → real conformal prediction on a calibration set.
- Predictor baseline is drift/vol → train XGBoost/TFT on SageMaker, serve as a
  tool the Predictor calls.
- Sentiment is canned → S3 + vector-store RAG with Bedrock Knowledge Bases.
- LLM JSON parsing is lenient → use Bedrock Converse structured/tool output.
