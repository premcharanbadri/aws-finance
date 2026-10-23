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
  "investable_budget": float,   # dollars available to invest
  "required_reserve": float,    # cash that must stay liquid
  "runway_months": float,       # cash / monthly burn
  "risk_flags": [str],          # e.g. thin runway, large near-term obligation
  "cpa_questions": [str],       # items for a human CPA to confirm
  "summary": str,               # plain-language explanation
}
```

### Entry point

```python
run_cpa(profile: dict) -> dict
```

### Consumers of the output

| Consumer | Field(s) used | Purpose |
|---|---|---|
| Portfolio agent | `investable_budget` | Sets how much the portfolio may invest |
| Challenger | `risk_flags` | Challenges the portfolio against liquidity risks |
| Relationship Manager | `summary`, `cpa_questions` | Included in the consolidated recommendation |

### Shared dependency

Only `services/llm.py` (the Bedrock/Strands call helper), which is shared by all agents.
