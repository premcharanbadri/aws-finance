"""
Company financials provider.

The investing company's balance sheet is the one input the system CANNOT
derive from market data or a model — only the company knows its own cash,
obligations, and covenants. So we take it as an explicit input rather than
fabricating it.

Resolution order (first that yields data wins):
  1. Explicit dict passed in (e.g. from the Streamlit UI form).
  2. JSON file at COMPANY_FINANCIALS_FILE (default: company_financials.json
     in the project root).
  3. None -> caller should treat the budget as ILLUSTRATIVE, not real.

Required fields (USD):
  cash_and_equivalents, current_assets, current_liabilities,
  existing_obligations, debt_covenant_min_cash
`working_capital` is derived if absent.
"""

from __future__ import annotations

import json
import os
from typing import Any

REQUIRED = [
    "cash_and_equivalents",
    "current_liabilities",
    "existing_obligations",
    "debt_covenant_min_cash",
]

DEFAULT_FILE = os.getenv("COMPANY_FINANCIALS_FILE", "company_financials.json")


class FinancialsResult:
    """Carries the financials plus whether they are real (user-provided) or
    illustrative (sample fallback)."""

    def __init__(self, company: str, data: dict[str, Any], is_real: bool, source: str):
        self.company = company
        self.data = data
        self.is_real = is_real
        self.source = source


def _normalize(company: str, raw: dict[str, Any]) -> dict[str, Any]:
    d = {k: float(raw[k]) for k in REQUIRED if k in raw}
    cash = d.get("cash_and_equivalents", 0.0)
    current_assets = float(raw.get("current_assets", cash))
    current_liabilities = d.get("current_liabilities", 0.0)
    d["company"] = company
    d["current_assets"] = current_assets
    d["working_capital"] = float(
        raw.get("working_capital", current_assets - current_liabilities)
    )
    # Fill any missing required field with 0.0 (conservative).
    for k in REQUIRED:
        d.setdefault(k, 0.0)
    return d


def get_financials(
    company: str = "YourCompany",
    explicit: dict[str, Any] | None = None,
    path: str | None = None,
) -> FinancialsResult:
    """Return the company's financials and whether they are real."""
    # 1. Explicit input (UI form / API).
    if explicit and any(k in explicit for k in REQUIRED):
        return FinancialsResult(company, _normalize(company, explicit), True, "user-input")

    # 2. JSON file.
    file_path = path or DEFAULT_FILE
    if os.path.exists(file_path):
        try:
            with open(file_path) as f:
                raw = json.load(f)
            name = raw.get("company", company)
            return FinancialsResult(name, _normalize(name, raw), True, f"file:{file_path}")
        except Exception:
            pass

    # 3. Illustrative fallback (clearly NOT real).
    sample = {
        "cash_and_equivalents": 20_000_000.0,
        "current_assets": 32_000_000.0,
        "current_liabilities": 12_000_000.0,
        "existing_obligations": 3_000_000.0,
        "debt_covenant_min_cash": 5_000_000.0,
    }
    return FinancialsResult(company, _normalize(company, sample), False, "illustrative-sample")
