"""Tests for the Redshift-backed DataProvider (Tyler-branch integration).

No AWS is contacted: we mock advisors.redshift_tools.execute via the provider's
_execute hook, and verify the graceful fallback when Redshift is unavailable.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import services.data as data  # noqa: E402

_SNAPSHOT_COLS = [
    "unrestricted_cash_usd", "covenant_min_cash_usd", "operating_reserve_6m_usd",
    "open_ap_usd", "taxes_due_next_180d_usd", "avg_monthly_burn_3m_usd",
    "avg_monthly_costs_3m_usd", "runway_months", "illustrative_investable_cash_usd",
]
_SNAPSHOT_ROW = [25_000_000.0, 5_000_000.0, 6_000_000.0, 1_500_000.0, 800_000.0,
                 400_000.0, 1_200_000.0, 62.5, 10_700_000.0]


def _redshift_provider_with_data():
    p = data.RedshiftDataProvider()
    p._available = True
    p._execute = lambda sql, max_rows=1: (_SNAPSHOT_COLS, [_SNAPSHOT_ROW], 1)
    return p


def test_factory_selects_redshift(monkeypatch):
    monkeypatch.setenv("DATA_PROVIDER", "redshift")
    assert isinstance(data.get_provider(), data.RedshiftDataProvider)
    monkeypatch.setenv("DATA_PROVIDER", "aws")  # legacy alias still works
    assert isinstance(data.get_provider(), data.RedshiftDataProvider)


def test_financials_mapped_from_snapshot():
    p = _redshift_provider_with_data()
    fin = p.get_financials("AcmeCorp")
    assert fin["source"] == "redshift:accounting.liquidity_snapshot"
    assert fin["cash_and_equivalents"] == pytest.approx(25_000_000)
    assert fin["debt_covenant_min_cash"] == pytest.approx(5_000_000)
    # obligations = open AP + taxes due in 180d
    assert fin["existing_obligations"] == pytest.approx(2_300_000)
    # expenses = avg monthly costs; revenue = costs - burn
    assert fin["monthly_expenses"] == pytest.approx(1_200_000)
    assert fin["monthly_revenue"] == pytest.approx(800_000)


def test_prices_and_universe_still_synthetic():
    """Redshift has no price table; those methods inherit the synthetic data."""
    p = _redshift_provider_with_data()
    assert p.get_prices("AAPL", 60).size == 60
    assert len(p.get_universe("technology")) > 0


def test_graceful_fallback_when_unavailable():
    p = data.RedshiftDataProvider()
    p._available = False  # simulate boto3/creds/stack missing
    fin = p.get_financials("AcmeCorp")
    assert "synthetic" in fin["source"]
    assert fin["cash_and_equivalents"] > 0  # synthetic financials still returned


def test_fallback_on_execute_error():
    p = data.RedshiftDataProvider()
    p._available = True

    def boom(sql, max_rows=1):
        raise RuntimeError("Data API timeout")

    p._execute = boom
    fin = p.get_financials("AcmeCorp")
    assert "synthetic" in fin["source"]


def test_cpa_adapter_uses_redshift_financials():
    import agents.cpa as cpa
    p = _redshift_provider_with_data()
    env = cpa.review("AcmeCorp", p)
    assert env.investable_budget > 0
    assert env.liquidity_ok is True
