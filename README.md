# aws-finance

## CPA Agent (Accounting & Liquidity Reviewer)

File: `agents/cpa.py`

Reviews the client's cash requirements, determines the available investment budget, and flags questions for a human CPA.

### Independence

The CPA agent is built standalone. Its only input is the **client profile**; it never reads output from any other agent. It can be developed and tested alone with a sample profile.

### Data flow

```
Client profile ──► CPA agent ──► CPA result
                  (cpa.py)           │
                                     ├─► Portfolio agent        uses investable_budget
                                     ├─► Challenger (reviewer)  uses risk_flags
                                     └─► Relationship Manager   uses summary + cpa_questions
```

Steps inside the agent:

1. **Input**: receive the client profile (cash on hand, monthly expenses, upcoming obligations, expected revenue, risk tolerance, time horizon).
2. **Compute (plain Python, deterministic)**: runway in months, required reserve, and investable budget = cash − reserve − obligations due within the horizon, floored at 0.
3. **Reason (LLM via `services/llm.py`)**: interpret free-text fields, explain the budget, and flag risks a CPA would care about.
4. **Output**: return the structured result below.

### Output schema

```python
{
  "investable_budget": float,        # dollars available to invest
  "required_reserve": float,         # cash that must stay liquid
  "runway_months": float | None,     # cash / net burn; None when unlimited
  "runway_is_unlimited": bool,       # True when net burn <= 0 (cash-flow positive)
  "min_projected_balance": float,    # lowest cash point across the horizon
  "max_drawdown_tolerance": float,   # risk constraint for challenger/portfolio
  "liquidity_need_pct": float,       # (reserve + near-term obligations) / cash
  "risk_flags": [str],               # e.g. thin runway, large near-term obligation
  "cpa_questions": [str],            # items for a human CPA to confirm
  "data_quality_flags": [str],       # missing/invalid inputs that were assumed
  "summary": str,                    # plain-language explanation
}
```

`runway_months` is `None` (not `Infinity`) for cash-flow-positive clients so the
result is valid strict JSON; `runway_is_unlimited` tells the UI to show
"unlimited". `max_drawdown_tolerance` and `liquidity_need_pct` are risk
constraints the Challenger and Portfolio agents can consume beyond the budget.

### Entry point

```python
run_cpa(profile: dict, policy: dict | None = None) -> dict
```

`policy` optionally overrides the reserve/threshold placeholders (e.g.
`{"reserve_months_by_risk": {"moderate": 9}}`) so a CPA can see the effect of a
different reserve without editing code.

### Reserve & budget policy (tunable placeholders)

These are hackathon defaults, **not** firm accounting rules — a human CPA would
tune them per engagement. They live as constants at the top of `agents/cpa.py`:

```
effective revenue = expected_monthly_revenue * revenue_haircut   # default 0.85
net burn          = max(monthly_expenses - effective_revenue, 0)
reserve months    = {low: 12, moderate: 6, high: 3}  by risk_tolerance
reserve basis     = gross monthly_expenses  (default) | net of effective revenue
required reserve  = reserve_months * reserve_basis
runway_months     = cash / net_burn        # None when net_burn <= 0 (unlimited)
investable_budget = max(min(snapshot, timeline), 0)
```

Expected revenue is projected, not guaranteed, so it is **haircut** (default to
85%) before the burn/runway/timeline math (`revenue_haircut` in `policy`). The
reserve uses gross expenses by default; set `policy={"net_reserve": True}` to
net the (haircut) revenue out of it.

The budget is **not** a single snapshot. Cash is projected month by month across
the horizon (effective revenue, expenses, and each obligation in the month it
lands), and the budget respects the *minimum* projected balance:

```
snapshot_budget   = cash - required_reserve - obligations_due
timeline_budget   = min_projected_balance - required_reserve
investable_budget = max(min(snapshot_budget, timeline_budget), 0)
```

This catches a client whose cash dips below the reserve mid-horizon and then
recovers — a plain end-of-horizon snapshot would miss it.

`obligations_due` sums every obligation with `due_in_months <= horizon_months`;
an obligation with **no** `due_in_months` is conservatively treated as due within
the horizon and flagged in `data_quality_flags`. A single obligation above 25% of
cash due within 3 months is flagged as a large near-term obligation. Missing or
invalid inputs (e.g. no `cash_on_hand`, unknown `risk_tolerance`) are assumed to
safe defaults and surfaced in `data_quality_flags` plus a CPA question. Adjust
`RESERVE_MONTHS_BY_RISK`, `NEAR_TERM_MONTHS`, `LARGE_OBLIGATION_FRACTION`,
`REVENUE_HAIRCUT`, `NET_RESERVE_DEFAULT`, and `MAX_DRAWDOWN_BY_RISK` in
`agents/cpa.py`, or pass the matching keys in a `policy` dict to `run_cpa`, to
change the policy.

### Free-text obligations and the summary guardrail (LLM)

A profile may carry messy notes in `obligations_text` (or `notes`), e.g.
`"tax bill of about 80k in March"`. When an LLM is configured, the agent asks it
to parse that prose into structured `{description, amount, due_in_months}`
obligations, which are merged **non-authoritatively** (tagged `source:
llm_freetext`) and raised as CPA confirmation questions. The model interprets
text; it never does the budget arithmetic.

As a guardrail, if the LLM's drafted `summary` states an investable dollar
figure that contradicts the computed budget (beyond a 2% tolerance), the agent
**keeps the deterministic summary** and adds a CPA question noting the
discrepancy. The numeric fields are always the deterministic math's.

`run_cpa` returns a `CpaResult` **TypedDict**, so the orchestrator catches key
mismatches early.

### LLM configuration (graceful fallback)

The LLM reasoning pass is optional and fail-soft. `services/llm.py` invokes
Amazon Bedrock through the **Strands Agents SDK** (`strands`) — the agent
framework this project is built on — and falls back to a direct `boto3` Bedrock
call if Strands is not installed. It reads:

- `BEDROCK_MODEL_ID` — the Bedrock model to invoke (an Anthropic Claude model id
  your AWS account has access to). **If unset, the agent skips the LLM entirely**
  and returns results from the deterministic math only.
- `AWS_REGION` — Bedrock region (defaults to `us-east-1`).

If Strands/Bedrock, credentials, or the model are unavailable — or the call
errors — the agent still returns a valid result from the deterministic path.
This keeps it testable offline and demo-safe. The numeric fields always come
from the deterministic math; the LLM only enriches `summary`, `risk_flags`, and
`cpa_questions`.

Run the agent against the sample profiles:

```
python -m agents.cpa
```

Run the tests:

```
pytest Tests/
```

### Consumers of the output

| Consumer | Field(s) used | Purpose |
|---|---|---|
| Portfolio agent | `investable_budget`, `max_drawdown_tolerance` | Sets how much the portfolio may invest and the drawdown it must respect |
| Challenger | `risk_flags`, `liquidity_need_pct` | Challenges the portfolio against liquidity risks |
| Relationship Manager | `summary`, `cpa_questions`, `data_quality_flags` | Included in the consolidated recommendation |

### Shared dependency

Only `services/llm.py` (the Bedrock/Strands call helper), which is shared by all agents.
