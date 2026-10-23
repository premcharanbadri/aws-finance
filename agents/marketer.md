# Market Strategist Agent

Module: `agents/marketer.py`
Role in the firm: **Market Strategist** — one of the specialized agents in the
virtual advisory firm. It turns (a) a synthetic client exposure profile,
(b) a synthetic ticker table, and (c) cached, offline market data into an
opinion: sector tilts, client-exposure flags, growth markets, and per-ticker
overlap flags. **Code — not the LLM — clamps and enforces every rule.**

This document is the integration contract for the orchestrator and the other
agents (Investment Analyst, Challenger, Relationship Manager).

---

## 1. Responsibilities & boundaries

- Produces a *view*, not a trade. Its `sector_tilts` are soft tilts in
  `[-0.20, +0.20]`; downstream agents decide how to use them.
- Enforces a **look-ahead cutoff**: nothing dated after `CUTOFF`
  (`2024-12-31`) may reach the LLM or appear in cited evidence.
- Never sends ticker **symbols** or company names to the LLM — only
  `ticker_id`, `sector`, `regions`, and `tags`.
- Pure/offline by default: importing the module has **no side effects**
  (no AWS client, no file reads, no printing, no folder creation, no
  `yfinance` import at import time).

---

## 2. Public API

Import only these four names from other modules:

```python
from agents.marketer import (
    StrategistOutput,   # pydantic model: the structured opinion
    StrategistResult,   # pydantic model: output + effects + warnings
    run_strategist,     # the agent entry point
    stub_strategist,    # deterministic placeholder for early integration
)
```

Everything else (loaders, guards, prompt) is internal and may change.

### `run_strategist(...)`

```python
run_strategist(
    client: ClientProfile,
    ticker_table: pandas.DataFrame,
    market: dict,
    sector_feats: dict,
    cutoff: str,
    optional_macro: dict | None = None,
    caller: Callable[[str], str] | None = None,
) -> StrategistResult
```

- `caller` is an injectable `(prompt) -> str` function. **Pass your own to
  mock the LLM in tests.** If omitted, a Bedrock Converse client is created
  *inside the function* (never at import).
- Parses the LLM reply into `StrategistOutput`, retrying up to `MAX_RETRIES`
  (2) with the validation error appended; on repeated failure it falls back to
  a neutral output and records a warning.
- Always runs `clamp_and_check` last, so the returned `StrategistResult` is
  already sanitized.
- Appends each `(prompt, raw_response)` to `logs/market_strategist_<client_id>.jsonl`
  (the `logs/` folder is created inside the function).

### `stub_strategist(client, cutoff) -> StrategistResult`

Deterministic fixed tilts plus exactly one `"high"` ticker flag, keyed off
`client.client_id` so different clients differ. Always passes
`clamp_and_check`. Use this to integrate before the live agent works.

---

## 3. Output schema (`StrategistResult`)

```jsonc
{
  "output": {
    "agent": "market_strategist",
    "regime_label": "string",
    "regime_evidence": [
      { "fact": "string", "source": "string", "available_on": "YYYY-MM-DD" }
    ],
    "sector_tilts": { "<GICS sector>": 0.0 },      // each in [-0.20, 0.20]
    "growth_markets": ["string"],
    "client_exposures": ["<tag from TAG_VOCAB>"],
    "ticker_flags": {
      "T01": { "overlap_with_client": "low|medium|high", "note": "string" }
    },
    "change_my_view_if": "string",
    "insufficient_evidence": false
  },
  "effects": [
    { "id": "S1", "target": "sector_tilt[Energy]", "effect": "clamped +0.500->+0.200", "reason": "outside +/-0.2" }
  ],
  "warnings": ["string"]
}
```

`effects` is the audit trail of every change code enforced over the LLM output
(S1, S2, …). Downstream agents should read `effects` to see what was clamped,
dropped, or capped, and `warnings` for missing-data notices.

---

## 4. Enforcement rules (applied by `clamp_and_check`)

| Rule | Behavior |
|------|----------|
| Sector tilt clamp | Clamped to `[-TILT_CLAMP, +TILT_CLAMP]` = `[-0.20, 0.20]`; logged as an effect |
| Unknown sector | Dropped from `sector_tilts`; logged |
| Unknown `ticker_id` | Dropped from `ticker_flags` (must be in `TICKER_IDS` T01–T10); logged |
| Unknown tag | Removed from `client_exposures` (must be in `TAG_VOCAB`); logged |
| High overlap | Each `"high"` flag adds a `cap 5% weight` effect (`CAP_HIGH_OVERLAP`) |
| Look-ahead | Any `evidence.available_on > cutoff` raises `ValueError` |

---

## 5. Inputs & data files

| Input | Produced by | Notes |
|-------|-------------|-------|
| `data/client_profiles.json` | shared (CPA agent owns it) | **Do not edit.** Identity + `risk_tolerance` only. |
| `data/strategist_exposures.json` | this agent | Synthetic exposure overlay, one entry per `client_id`. Values are **UNVERIFIED** placeholders. |
| `data/tickers.csv` | this agent | `ticker_id, symbol, sector, regions, tags(|-sep), note`. `note` marked UNVERIFIED. |
| `data/prices.csv` | external / `fetch_prices_to_csv` | Wide: `Date` + one adjusted-close column per symbol. |
| `data/market.csv` | manual (`fetch_prices_to_csv`) | Wide: `Date` + sector ETFs, `SPY`, and `MARKET_SERIES`. **Optional** — if missing, sector/market signals return empty + a warning (no crash). |
| `data/macro/*.csv` | external (optional) | `date,value`; release-lag aware. Missing files warn, never invent. |

Loader helpers (internal, for reference):
`load_client(profiles_path, exposures_path, client_id)`,
`load_ticker_table(path)`, `load_prices(symbol, cutoff, prices_path=None)`,
`load_sector_features(cutoff, market_path=None)`,
`load_market_snapshot(cutoff, market_path=None)`,
`load_optional_macro(cutoff, macro_dir=None)`.

`load_sector_features` and `load_market_snapshot` return a wrapper dict:
`{"features": {...}, "warnings": [...]}` and `{"snapshot": {...}, "warnings": [...]}`.
Pass the **inner** dict as `sector_feats` / `market` to `run_strategist`.

> `data/market.csv` does not exist yet. Generate it with the manual helper:
> `fetch_prices_to_csv(list(SECTOR_ETFS)+["SPY"]+list(MARKET_SERIES), "2015-01-01", "2025-01-01", "data/market.csv")`.

---

## 6. Orchestration

### Call order in the firm
1. **CPA / cash agent** reads `client_profiles.json` (runway, obligations).
2. **Market Strategist** (this agent) → produces sector tilts, exposure flags,
   ticker overlap flags.
3. **Investment Analyst** consumes `sector_tilts` + `ticker_flags` to size
   positions (respecting the 5% cap on `"high"` overlap tickers).
4. **Challenger** reads `change_my_view_if`, `effects`, and `warnings` to
   stress-test the view.
5. **Relationship Manager** turns the result into client-facing narrative.

### Minimal orchestrator snippet

```python
import agents.marketer as ms

client = ms.load_client(
    ms.CLIENT_PROFILES_PATH, ms.STRATEGIST_EXPOSURES_PATH, "acme"
)
tickers = ms.load_ticker_table(ms.TICKERS_PATH)
feats = ms.load_sector_features(ms.CUTOFF)      # {"features":..., "warnings":...}
snap  = ms.load_market_snapshot(ms.CUTOFF)      # {"snapshot":...,  "warnings":...}
macro = ms.load_optional_macro(ms.CUTOFF)       # {"series":...,    "warnings":...}

result = ms.run_strategist(
    client,
    tickers,
    market=snap["snapshot"],
    sector_feats=feats["features"],
    cutoff=ms.CUTOFF,
    optional_macro=macro,
    # caller=my_mock,   # inject to avoid AWS in tests
)

tilts   = result.output.sector_tilts
flags   = result.output.ticker_flags
effects = result.effects      # what the guards changed
warns   = result.warnings     # missing data, fallbacks
```

For early integration before the LLM path is wired, swap
`run_strategist(...)` for `stub_strategist(client, ms.CUTOFF)` — same
`StrategistResult` shape.

### Optional Strands tool
`as_strands_tool()` returns `run_strategist` wrapped as a Strands tool.
Strands is imported lazily inside that function. **Verify the exact Strands
API (decorator name/signature) against your installed version** before relying
on it.

---

## 7. Config knobs (`agents/marketer.py`, section 1)

| Constant | Default | Meaning |
|----------|---------|---------|
| `CUTOFF` | `2024-12-31` | Look-ahead boundary (data ends 2024-12-30). |
| `TICKER_IDS` | `T01`…`T10` | Valid ticker ids. |
| `TILT_CLAMP` | `0.20` | Max abs sector tilt. |
| `CAP_HIGH_OVERLAP` | `0.05` | Weight cap on `"high"` overlap tickers. |
| `MAX_RETRIES` | `2` | LLM parse retries before neutral fallback. |
| `BEDROCK_MODEL_ID` | env or `us.openai.gpt-5.6-luna` | Converse model id. |
| `AWS_REGION` | env or `us-east-1` | Bedrock region. |
| `MAX_TOKENS` / `TEMPERATURE` | `2000` / `0` | Inference config. |

Credentials are **never** in code; the default caller uses the ambient AWS role.

---

## 8. Testing

Tests live at the bottom of `agents/marketer.py` as `test_*` functions and run
with no network and no AWS. `pytest`/`re` are imported lazily so importing the
module never requires pytest.

```bash
python agents/marketer.py        # runs pytest.main([__file__])
# or
pytest agents/marketer.py -q
```

Covered: cutoff filtering, wide-CSV price loading + missing-symbol errors,
release-lag macro exclusion, missing-market-file warnings, all clamp/guard
rules, stub validity, client merge + missing-id errors, mocked-caller success,
3× invalid-output fallback to neutral, two clients producing different effects,
and the no-symbol / no-future-date prompt guarantee.
```
