"""Tests for the CPA agent against the three sample profiles and edge cases.

These run fully offline: BEDROCK_MODEL_ID is cleared so services.llm.call_llm
short-circuits to None and run_cpa uses its deterministic path. One test also
monkeypatches the LLM to confirm its output is merged on top of the math.
"""
import json
import os
import sys
from pathlib import Path

import pytest

# Make the repo root importable when running pytest from anywhere.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents import cpa_liquidity as cpa  # noqa: E402
from agents.cpa_liquidity import sample_client_profiles  # noqa: E402


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    """Force the deterministic path by removing the Bedrock model id."""
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)


def _by_id(client_id):
    for p in sample_client_profiles():
        if p["client_id"] == client_id:
            return p
    raise KeyError(client_id)


def test_result_schema():
    result = cpa.run_cpa(_by_id("acme"))
    assert set(result) == {
        "investable_budget",
        "required_reserve",
        "runway_months",
        "runway_is_unlimited",
        "min_projected_balance",
        "max_drawdown_tolerance",
        "liquidity_need_pct",
        "risk_flags",
        "cpa_questions",
        "data_quality_flags",
        "summary",
    }
    assert isinstance(result["investable_budget"], float)
    assert isinstance(result["required_reserve"], float)
    assert isinstance(result["risk_flags"], list)
    assert isinstance(result["cpa_questions"], list)
    assert isinstance(result["data_quality_flags"], list)
    assert isinstance(result["summary"], str) and result["summary"]


def test_output_is_strict_json_serializable():
    """Item 1: no Infinity sentinel — output must be valid strict JSON."""
    for p in sample_client_profiles():
        result = cpa.run_cpa(p)
        # allow_nan=False rejects Infinity/NaN, which strict parsers also reject.
        json.dumps(result, allow_nan=False)


def test_acme_timeline_budget():
    # cash 2,000,000; exp 150,000; rev 120,000; moderate => 6mo reserve = 900,000.
    # Revenue haircut 15% => effective rev 102,000, net burn 48,000/mo.
    # 300,000 obligation at month 6. min projected balance (month 12) =
    # 2,000,000 - 12*48,000 - 300,000 = 1,124,000.
    # timeline budget = 1,124,000 - 900,000 = 224,000 (< snapshot 800,000).
    result = cpa.run_cpa(_by_id("acme"))
    assert result["required_reserve"] == pytest.approx(900_000)
    assert result["min_projected_balance"] == pytest.approx(1_124_000)
    assert result["investable_budget"] == pytest.approx(224_000)
    assert result["runway_months"] == pytest.approx(2_000_000 / 48_000)
    assert result["runway_is_unlimited"] is False


def test_tight_zero_budget_and_runway_flag():
    # Pin haircut off so the runway assertion reflects the raw net burn.
    result = cpa.run_cpa(_by_id("tight"), policy={"revenue_haircut": 1.0})
    assert result["investable_budget"] == 0.0
    assert result["runway_months"] == pytest.approx(10.0)
    joined = " ".join(result["risk_flags"]).lower()
    assert "runway" in joined
    assert any("investable budget" in f.lower() for f in result["risk_flags"])
    # low risk tolerance => tightest drawdown constraint, full liquidity need
    assert result["max_drawdown_tolerance"] == pytest.approx(0.10)
    assert result["liquidity_need_pct"] == pytest.approx(1.0)


def test_flush_unlimited_runway_full_budget():
    # net burn 0 => runway_months is None and runway_is_unlimited True (item 1).
    result = cpa.run_cpa(_by_id("flush"))
    assert result["required_reserve"] == pytest.approx(600_000)
    assert result["investable_budget"] == pytest.approx(9_400_000)
    assert result["runway_months"] is None
    assert result["runway_is_unlimited"] is True
    assert not any("cash-flow negative" in f.lower() for f in result["risk_flags"])
    assert result["max_drawdown_tolerance"] == pytest.approx(0.40)


# --- Item 4: timeline catches a mid-horizon dip a snapshot would miss. --------
def test_midhorizon_dip_is_caught():
    profile = {
        "client_id": "dip",
        "company_name": "Dip Co",
        "cash_on_hand": 1_000_000,
        "monthly_expenses": 100_000,
        "expected_monthly_revenue": 100_000,  # net burn 0
        "obligations": [
            # Big spend in month 5, then nothing. Snapshot at horizon-end would
            # look fine (net burn 0), but mid-horizon cash drops sharply.
            {"description": "Acquisition", "amount": 700_000, "due_in_months": 5},
        ],
        "risk_tolerance": "high",  # 3-month reserve = 300,000
        "horizon_months": 12,
    }
    result = cpa.run_cpa(profile, policy={"revenue_haircut": 1.0})
    # min balance = 1,000,000 - 700,000 = 300,000 at month 5.
    assert result["min_projected_balance"] == pytest.approx(300_000)
    # timeline budget = 300,000 - 300,000 = 0; snapshot would have been
    # 1,000,000 - 300,000 - 700,000 = 0 here too, but the dip is flagged.
    assert result["investable_budget"] == pytest.approx(0.0)


# --- Item 2: input validation / data quality flags. ---------------------------
def test_missing_cash_flagged():
    profile = {
        "client_id": "nocash",
        "monthly_expenses": 50_000,
        "expected_monthly_revenue": 60_000,
        "risk_tolerance": "moderate",
        "horizon_months": 12,
    }
    result = cpa.run_cpa(profile)
    assert any("cash_on_hand missing" in f for f in result["data_quality_flags"])
    assert any("cash_on_hand missing" in q for q in result["cpa_questions"])
    assert result["investable_budget"] == 0.0


def test_unknown_risk_tolerance_defaults_and_flags():
    profile = {
        "client_id": "weird",
        "cash_on_hand": 1_000_000,
        "monthly_expenses": 50_000,
        "expected_monthly_revenue": 60_000,
        "risk_tolerance": "banana",
        "horizon_months": 12,
    }
    m = cpa.compute_metrics(profile)
    assert m["reserve_months"] == cpa.DEFAULT_RESERVE_MONTHS
    assert any("unrecognized" in f for f in m["data_quality_flags"])


def test_negative_cash_flagged():
    profile = {
        "client_id": "broke",
        "cash_on_hand": -50_000,
        "monthly_expenses": 20_000,
        "expected_monthly_revenue": 10_000,
        "risk_tolerance": "low",
        "horizon_months": 6,
    }
    result = cpa.run_cpa(profile)
    assert any("negative" in f for f in result["data_quality_flags"])
    assert result["investable_budget"] == 0.0


# --- Item 3: obligation with no due date is treated conservatively. -----------
def test_obligation_without_due_date_counted_and_flagged():
    profile = {
        "client_id": "nodate",
        "cash_on_hand": 1_000_000,
        "monthly_expenses": 50_000,
        "expected_monthly_revenue": 50_000,  # net burn 0
        "obligations": [
            {"description": "Mystery bill", "amount": 200_000},  # no due_in_months
        ],
        "risk_tolerance": "high",  # reserve 150,000
        "horizon_months": 12,
    }
    m = cpa.compute_metrics(profile)
    assert m["obligations_due_within_horizon"] == pytest.approx(200_000)
    assert any("no" in f.lower() and "due" in f.lower() for f in m["data_quality_flags"])


def test_obligation_past_horizon_not_counted():
    profile = {
        "client_id": "far",
        "cash_on_hand": 1_000_000,
        "monthly_expenses": 50_000,
        "expected_monthly_revenue": 50_000,
        "obligations": [
            {"description": "Future loan", "amount": 300_000, "due_in_months": 48},
        ],
        "risk_tolerance": "high",
        "horizon_months": 12,
    }
    m = cpa.compute_metrics(profile)
    assert m["obligations_due_within_horizon"] == 0.0


# --- Item 10: policy override. ------------------------------------------------
def test_policy_override_changes_reserve():
    base = cpa.run_cpa(_by_id("acme"))
    bumped = cpa.run_cpa(
        _by_id("acme"),
        policy={"reserve_months_by_risk": {"moderate": 9}},
    )
    # 9 months * 150,000 = 1,350,000 reserve, up from 900,000.
    assert bumped["required_reserve"] == pytest.approx(1_350_000)
    assert bumped["investable_budget"] < base["investable_budget"]


# --- LLM behaviour. -----------------------------------------------------------
def test_llm_output_is_merged(monkeypatch):
    def fake_call_llm_json(prompt, **kwargs):
        return {
            "summary": "LLM-authored summary.",
            "risk_flags": ["Customer concentration risk (LLM)."],
            "cpa_questions": ["Confirm revenue recognition policy (LLM)."],
        }

    monkeypatch.setattr(cpa, "call_llm_json", fake_call_llm_json)
    result = cpa.run_cpa(_by_id("acme"))
    assert result["summary"] == "LLM-authored summary."
    assert "Customer concentration risk (LLM)." in result["risk_flags"]
    assert "Confirm revenue recognition policy (LLM)." in result["cpa_questions"]
    assert len(result["cpa_questions"]) >= 2


def test_llm_failure_falls_back(monkeypatch):
    monkeypatch.setattr(cpa, "call_llm_json", lambda prompt, **kw: None)
    result = cpa.run_cpa(_by_id("acme"))
    assert result["summary"]
    assert result["investable_budget"] == pytest.approx(224_000)


# --- Item 5: revenue haircut. -------------------------------------------------
def test_revenue_haircut_reduces_budget_and_runway():
    full = cpa.run_cpa(_by_id("acme"), policy={"revenue_haircut": 1.0})
    hair = cpa.run_cpa(_by_id("acme"), policy={"revenue_haircut": 0.80})
    # A deeper haircut means lower effective revenue -> more burn -> less budget.
    assert hair["investable_budget"] < full["investable_budget"]
    assert hair["runway_months"] < full["runway_months"]


def test_haircut_can_flip_to_cash_flow_negative():
    # rev slightly above exp, but an 80% haircut pushes effective rev below exp.
    profile = {
        "client_id": "thin_margin",
        "cash_on_hand": 1_000_000,
        "monthly_expenses": 100_000,
        "expected_monthly_revenue": 110_000,  # gross positive
        "risk_tolerance": "high",
        "horizon_months": 12,
    }
    full = cpa.run_cpa(profile, policy={"revenue_haircut": 1.0})
    hair = cpa.run_cpa(profile, policy={"revenue_haircut": 0.80})  # eff rev 88k < 100k
    assert full["runway_is_unlimited"] is True
    assert hair["runway_is_unlimited"] is False


# --- Item 6: net reserve switch. ----------------------------------------------
def test_net_reserve_lowers_reserve():
    profile = {
        "client_id": "netres",
        "cash_on_hand": 5_000_000,
        "monthly_expenses": 100_000,
        "expected_monthly_revenue": 60_000,
        "risk_tolerance": "moderate",  # 6-month reserve
        "horizon_months": 12,
    }
    gross = cpa.run_cpa(profile, policy={"net_reserve": False, "revenue_haircut": 1.0})
    net = cpa.run_cpa(profile, policy={"net_reserve": True, "revenue_haircut": 1.0})
    # gross reserve = 6 * 100,000 = 600,000.
    assert gross["required_reserve"] == pytest.approx(600_000)
    # net reserve = 6 * (100,000 - 60,000) = 240,000.
    assert net["required_reserve"] == pytest.approx(240_000)
    assert net["investable_budget"] > gross["investable_budget"]


# --- Item 8: LLM free-text obligation parsing. --------------------------------
def test_freetext_obligations_parsed_and_merged(monkeypatch):
    def fake_call_llm_json(prompt, **kwargs):
        # Only respond to the obligation-parsing prompt.
        if "Extract financial obligations" in prompt:
            return {"obligations": [
                {"description": "Tax bill", "amount": 80_000, "due_in_months": 5},
            ]}
        return None  # summary pass uses deterministic fallback

    monkeypatch.setattr(cpa, "call_llm_json", fake_call_llm_json)
    profile = {
        "client_id": "freetext",
        "cash_on_hand": 1_000_000,
        "monthly_expenses": 50_000,
        "expected_monthly_revenue": 50_000,
        "obligations": [],
        "obligations_text": "tax bill of about 80k in March",
        "risk_tolerance": "high",
        "horizon_months": 12,
    }
    result = cpa.run_cpa(profile, policy={"revenue_haircut": 1.0})
    # The parsed 80k obligation should be counted and flagged for confirmation.
    assert any("parsed from free text" in q for q in result["cpa_questions"])


def test_freetext_parser_handles_no_llm(monkeypatch):
    monkeypatch.setattr(cpa, "call_llm_json", lambda prompt, **kw: None)
    assert cpa.parse_freetext_obligations("tax bill of 80k next month") == []


# --- Item 9: summary guardrail. -----------------------------------------------
def test_summary_guardrail_rejects_contradictory_budget(monkeypatch):
    def fake_call_llm_json(prompt, **kwargs):
        if "Extract financial obligations" in prompt:
            return None
        # Claims a wildly different investable budget than the computed one.
        return {"summary": "The investable budget is $50,000,000, invest freely."}

    monkeypatch.setattr(cpa, "call_llm_json", fake_call_llm_json)
    result = cpa.run_cpa(_by_id("acme"))
    # Computed budget is 224,000; the $50M claim must be rejected.
    assert "$50,000,000" not in result["summary"]
    assert any("inconsistent with the computed budget" in q for q in result["cpa_questions"])


def test_summary_guardrail_allows_consistent_summary(monkeypatch):
    def fake_call_llm_json(prompt, **kwargs):
        if "Extract financial obligations" in prompt:
            return None
        return {"summary": "Liquidity is healthy with an investable budget near $224,000."}

    monkeypatch.setattr(cpa, "call_llm_json", fake_call_llm_json)
    result = cpa.run_cpa(_by_id("acme"))
    assert "224,000" in result["summary"]


def test_guardrail_helper_unit():
    # Within tolerance -> not a contradiction.
    assert cpa.summary_contradicts_budget(
        "Investable budget is about $225,000.", 224_000) is False
    # Way off -> contradiction.
    assert cpa.summary_contradicts_budget(
        "You can invest up to $5,000,000.", 224_000) is True
    # A dollar figure in a non-budget sentence is ignored.
    assert cpa.summary_contradicts_budget(
        "The reserve is $900,000.", 224_000) is False


# --- Item 12: typed output. ---------------------------------------------------
def test_typed_output_keys_match_typeddict():
    result = cpa.run_cpa(_by_id("acme"))
    assert set(result.keys()) == set(cpa.CpaResult.__annotations__.keys())
