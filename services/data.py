"""
Data layer: a provider interface so agents never touch a concrete source.

Switch implementations with the DATA_PROVIDER env var:
    synthetic (default) | yfinance | redshift (alias: aws)

- SyntheticDataProvider: offline, deterministic (seeded), zero cost. Default
  for development and CI.
- YFinanceDataProvider: real historical prices via yfinance (free), news
  stays synthetic. Stub provided; implement when needed.
- RedshiftDataProvider: company financials from the Amazon Redshift dbt marts
  (accounting.liquidity_snapshot) via the Redshift Data API; prices/universe/
  news inherited from synthetic (no price table in Redshift). Falls back to
  synthetic financials if Redshift is unreachable.

Agents depend only on the DataProvider ABC below.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

import numpy as np


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

class DataProvider(ABC):
    @abstractmethod
    def get_universe(self, industry: str | None = None) -> list[str]:
        """Return candidate tickers, optionally filtered by industry."""

    @abstractmethod
    def get_prices(self, ticker: str, days: int = 252) -> np.ndarray:
        """Return a 1-D array of daily close prices, oldest first."""

    @abstractmethod
    def get_fundamentals(self, ticker: str) -> dict[str, Any]:
        """Return fundamental ratios for a ticker."""

    @abstractmethod
    def get_news(self, ticker: str, limit: int = 5) -> list[dict[str, Any]]:
        """Return recent news items: {headline, sentiment, source}."""

    @abstractmethod
    def get_financials(self, company: str) -> dict[str, Any]:
        """Return the investing company's balance-sheet snapshot for the
        Accounting & Liquidity Reviewer."""


# ---------------------------------------------------------------------------
# Synthetic (default)
# ---------------------------------------------------------------------------

_INDUSTRIES = {
    "technology": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMD"],
    "healthcare": ["JNJ", "PFE", "UNH", "MRK", "ABBV"],
    "energy": ["XOM", "CVX", "COP", "SLB", "EOG"],
    "financials": ["JPM", "BAC", "GS", "MS", "WFC"],
    "consumer": ["AMZN", "WMT", "PG", "KO", "MCD"],
}

_HEADLINES = [
    ("beats earnings expectations, raises guidance", 0.7),
    ("faces regulatory scrutiny over new product", -0.5),
    ("announces strategic partnership", 0.4),
    ("warns of margin pressure next quarter", -0.6),
    ("expands into new markets", 0.5),
    ("CEO transition announced", -0.1),
]

# --- Industry-standard parameters, aligned to a 2015-2024 daily window -------
# ~252 trading days/year over 10 years. Matches what yfinance will return when
# real data replaces synthetic (same shape, units, and lookback semantics).
TRADING_DAYS_PER_YEAR = 252
DEFAULT_HISTORY_DAYS = TRADING_DAYS_PER_YEAR * 10  # 2015-01-01 .. 2024-12-31

# Per-sector annualized (mean_return, volatility, market beta) — industry-
# typical equity values. Returns/vol are annualized; converted to daily below.
_SECTOR_PARAMS = {
    "technology": {"mu": 0.14, "vol": 0.30, "beta": 1.25},
    "healthcare": {"mu": 0.09, "vol": 0.18, "beta": 0.85},
    "energy": {"mu": 0.06, "vol": 0.32, "beta": 1.10},
    "financials": {"mu": 0.10, "vol": 0.24, "beta": 1.20},
    "consumer": {"mu": 0.08, "vol": 0.15, "beta": 0.75},
}
# Broad market factor (S&P-like): ~8% drift, ~16% vol annualized.
_MARKET_MU = 0.08
_MARKET_VOL = 0.16
# Idiosyncratic noise and regime (volatility-clustering) settings.
_REGIME_HIGH_VOL_MULT = 2.0       # turbulent-regime volatility multiplier
_REGIME_SWITCH_PROB = 1.0 / 60    # ~ once a quarter
_FAT_TAIL_DOF = 5                 # Student-t dof for mild fat tails
_MISSING_FUNDAMENTAL_PROB = 0.05  # inject NaNs so agents handle real-world gaps


def _sector_of(ticker: str) -> str:
    for sector, tickers in _INDUSTRIES.items():
        if ticker in tickers:
            return sector
    return "technology"


class SyntheticDataProvider(DataProvider):
    """Industry-standard synthetic data generator.

    Stylized facts baked in to resemble real equity data (2015-2024 daily):
      - A shared MARKET FACTOR so assets co-move (CAPM: r = beta*market + eps).
        This is realistic AND fixes mean-variance over-concentration.
      - VOLATILITY CLUSTERING via a two-state (calm/turbulent) regime.
      - MILD FAT TAILS via Student-t idiosyncratic shocks.
      - Optional MISSING fundamentals (NaN) so downstream code is hardened
        before real data arrives.

    Same interface as before: a drop-in that keeps the yfinance/Redshift swap
    zero-friction. Deterministic given the seed.
    """

    def __init__(self, seed: int = 42, inject_missing: bool = True) -> None:
        self.seed = seed
        self.inject_missing = inject_missing

    def _rng(self, key: str) -> np.random.Generator:
        h = abs(hash(key)) % (2**32)
        return np.random.default_rng(self.seed ^ h)

    def _market_factor(self, days: int) -> np.ndarray:
        """Shared daily market returns with regime-switching volatility.

        Seeded only on 'market' + length so every ticker sees the SAME market
        path (that is what induces cross-asset correlation)."""
        rng = self._rng(f"market::{days}")
        mu_d = _MARKET_MU / TRADING_DAYS_PER_YEAR
        base_vol_d = _MARKET_VOL / np.sqrt(TRADING_DAYS_PER_YEAR)
        rets = np.empty(days)
        turbulent = False
        for i in range(days):
            if rng.random() < _REGIME_SWITCH_PROB:
                turbulent = not turbulent
            vol = base_vol_d * (_REGIME_HIGH_VOL_MULT if turbulent else 1.0)
            shock = rng.standard_t(_FAT_TAIL_DOF) / np.sqrt(_FAT_TAIL_DOF / (_FAT_TAIL_DOF - 2))
            rets[i] = mu_d + vol * shock
        return rets

    def get_universe(self, industry: str | None = None) -> list[str]:
        if industry:
            return _INDUSTRIES.get(industry.lower(), [])
        return [t for tickers in _INDUSTRIES.values() for t in tickers]

    def get_prices(self, ticker: str, days: int = DEFAULT_HISTORY_DAYS) -> np.ndarray:
        """Daily close prices, oldest first. CAPM-style: the ticker's return
        is driven by the shared market factor (scaled by beta) plus a sector-
        calibrated idiosyncratic component."""
        params = _SECTOR_PARAMS[_sector_of(ticker)]
        rng = self._rng(ticker)
        market = self._market_factor(days)

        beta = params["beta"] * float(rng.uniform(0.85, 1.15))
        # Split total variance into systematic (market) and idiosyncratic.
        total_vol_d = params["vol"] / np.sqrt(TRADING_DAYS_PER_YEAR)
        sys_vol_d = beta * (_MARKET_VOL / np.sqrt(TRADING_DAYS_PER_YEAR))
        idio_vol_d = float(np.sqrt(max(total_vol_d**2 - sys_vol_d**2, (0.3 * total_vol_d) ** 2)))

        alpha_d = (params["mu"] - beta * _MARKET_MU) / TRADING_DAYS_PER_YEAR
        idio = idio_vol_d * (
            rng.standard_t(_FAT_TAIL_DOF, size=days)
            / np.sqrt(_FAT_TAIL_DOF / (_FAT_TAIL_DOF - 2))
        )
        rets = alpha_d + beta * market + idio
        start = float(rng.uniform(20, 300))
        return start * np.exp(np.cumsum(rets))

    def get_fundamentals(self, ticker: str) -> dict[str, Any]:
        """Industry-standard fundamental ranges, with occasional missing
        values (NaN) to mirror real-world data gaps."""
        rng = self._rng(ticker + "fund")
        sector = _sector_of(ticker)

        def maybe(value):
            if self.inject_missing and rng.random() < _MISSING_FUNDAMENTAL_PROB:
                return float("nan")
            return value

        # Sector tilts within industry-typical bands.
        pe_lo, pe_hi = (18, 45) if sector == "technology" else (8, 28)
        margin_hi = 0.35 if sector in ("technology", "healthcare") else 0.20
        return {
            "ticker": ticker,
            "sector": sector,
            "pe_ratio": maybe(round(float(rng.uniform(pe_lo, pe_hi)), 2)),
            "profit_margin": maybe(round(float(rng.uniform(0.03, margin_hi)), 3)),
            "revenue_growth": maybe(round(float(rng.uniform(-0.05, 0.25)), 3)),
            "debt_to_equity": maybe(round(float(rng.uniform(0.1, 2.0)), 2)),
            "market_cap_b": round(float(rng.uniform(5, 2500)), 1),
        }


    def get_news(self, ticker: str, limit: int = 5) -> list[dict[str, Any]]:
        rng = self._rng(ticker + "news")
        idx = rng.choice(len(_HEADLINES), size=min(limit, len(_HEADLINES)), replace=False)
        items = []
        for i in idx:
            text, base = _HEADLINES[int(i)]
            jitter = float(rng.uniform(-0.15, 0.15))
            items.append(
                {
                    "headline": f"{ticker} {text}",
                    "sentiment": round(max(-1.0, min(1.0, base + jitter)), 3),
                    "source": rng.choice(["Reuters", "Bloomberg", "WSJ", "FT"]),
                }
            )
        return items

    def get_financials(self, company: str) -> dict[str, Any]:
        rng = self._rng(company + "fin")
        cash = float(rng.uniform(2e6, 5e7))
        current_assets = cash + float(rng.uniform(1e6, 2e7))
        # Current liabilities below current assets so working capital is
        # positive (a solvent going concern) — avoids the demo randomly
        # starting a company in an insolvent state with a $0 budget.
        current_liabilities = float(rng.uniform(0.3, 0.8)) * current_assets
        # Operating cash flow: a monthly expense base with revenue modestly
        # above it (a going concern is typically cash-flow positive). These are
        # the fields the Accounting & Liquidity Reviewer actually needs — prefer
        # them over balance-sheet proxies.
        monthly_expenses = float(rng.uniform(2e5, 2e6))
        monthly_revenue = monthly_expenses * float(rng.uniform(1.0, 1.4))
        return {
            "company": company,
            "cash_and_equivalents": round(cash, 2),
            "current_assets": round(current_assets, 2),
            "current_liabilities": round(current_liabilities, 2),
            "working_capital": round(current_assets - current_liabilities, 2),
            "existing_obligations": round(cash * float(rng.uniform(0.05, 0.20)), 2),
            "debt_covenant_min_cash": round(cash * float(rng.uniform(0.1, 0.3)), 2),
            "monthly_revenue": round(monthly_revenue, 2),
            "monthly_expenses": round(monthly_expenses, 2),
        }


# ---------------------------------------------------------------------------
# Stubs for real providers (same interface; implement when needed)
# ---------------------------------------------------------------------------

class YFinanceDataProvider(SyntheticDataProvider):
    """Real prices via yfinance; news falls back to synthetic.

    Inherits synthetic fundamentals/news/financials so it is usable
    immediately; override get_prices (and optionally get_fundamentals) with
    yfinance calls. Left as a documented extension point.
    """

    def get_prices(self, ticker: str, days: int = 252) -> np.ndarray:  # pragma: no cover
        try:
            import yfinance as yf

            hist = yf.Ticker(ticker).history(period=f"{days}d")["Close"].to_numpy()
            if hist.size:
                return hist
        except Exception:
            pass
        return super().get_prices(ticker, days)


class RedshiftDataProvider(SyntheticDataProvider):
    """Financials from Amazon Redshift (Tyler branch dbt marts); everything
    else inherited from the synthetic provider.

    The Redshift marts (`accounting`, `marketing`) hold the CLIENT company's
    financials and campaign data — not stock prices — so only `get_financials`
    is Redshift-backed here. `get_prices`/`get_universe`/`get_news` stay
    synthetic (use DATA_PROVIDER=yfinance for real prices). Any Redshift error
    (no creds, no network, no stack) falls back to the synthetic financials so
    the app never breaks.

    Query transport is reused from `advisors.redshift_tools.execute`, which
    uses the Redshift Data API with the read-only `advisor_ro` secret.
    """

    #: schema-qualified one-row liquidity snapshot produced by dbt.
    SNAPSHOT_TABLE = "accounting.liquidity_snapshot"

    def __init__(self) -> None:
        super().__init__()
        try:
            from advisors.redshift_tools import execute  # Data API wrapper
            self._execute = execute
            self._available = True
        except Exception:  # noqa: BLE001 - boto3/strands/tools not importable
            self._execute = None
            self._available = False

    def _query_snapshot(self) -> dict[str, Any] | None:
        """Return the latest liquidity snapshot row as a dict, or None."""
        if not self._available or self._execute is None:
            return None
        sql = (
            "SELECT unrestricted_cash_usd, covenant_min_cash_usd, "
            "operating_reserve_6m_usd, open_ap_usd, taxes_due_next_180d_usd, "
            "avg_monthly_burn_3m_usd, avg_monthly_costs_3m_usd, runway_months, "
            "illustrative_investable_cash_usd "
            f"FROM {self.SNAPSHOT_TABLE} LIMIT 1"
        )
        try:
            cols, rows, _ = self._execute(sql, max_rows=1)
        except Exception:  # noqa: BLE001 - fail soft on any Data API error
            return None
        if not cols or not rows:
            return None
        return {c: v for c, v in zip(cols, rows[0])}

    def get_financials(self, company: str) -> dict[str, Any]:
        """Company financials from Redshift, mapped onto the provider contract.

        Falls back to the synthetic financials if Redshift is unavailable.
        """
        snap = self._query_snapshot()
        if snap is None:
            fin = super().get_financials(company)
            fin["source"] = "synthetic (Redshift unavailable)"
            return fin

        def num(key, default=0.0):
            try:
                return float(snap.get(key) or 0.0)
            except (TypeError, ValueError):
                return default

        cash = num("unrestricted_cash_usd")
        covenant_min = num("covenant_min_cash_usd")
        reserve_6m = num("operating_reserve_6m_usd")
        open_ap = num("open_ap_usd")
        taxes_180d = num("taxes_due_next_180d_usd")
        monthly_burn = num("avg_monthly_burn_3m_usd")
        monthly_costs = num("avg_monthly_costs_3m_usd")

        # Near-term obligations the CPA engine should net out = open AP + taxes.
        obligations = open_ap + taxes_180d
        # Approximate current liabilities for the working-capital check.
        current_liabilities = obligations + reserve_6m
        current_assets = cash + max(0.0, cash - current_liabilities)

        return {
            "company": company,
            "cash_and_equivalents": round(cash, 2),
            "current_assets": round(current_assets, 2),
            "current_liabilities": round(current_liabilities, 2),
            "working_capital": round(current_assets - current_liabilities, 2),
            "existing_obligations": round(obligations, 2),
            "debt_covenant_min_cash": round(covenant_min, 2),
            # Operating cash flow derived from the marts: costs are the monthly
            # outflow; revenue = costs - burn (burn is negative op cash flow).
            "monthly_expenses": round(monthly_costs, 2),
            "monthly_revenue": round(max(0.0, monthly_costs - monthly_burn), 2),
            "source": "redshift:accounting.liquidity_snapshot",
        }


# Backwards-compatible alias: the previous stub name still resolves.
AwsDataProvider = RedshiftDataProvider


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_provider() -> DataProvider:
    choice = os.getenv("DATA_PROVIDER", "synthetic").lower()
    if choice == "yfinance":
        return YFinanceDataProvider()
    if choice in ("redshift", "aws"):
        return RedshiftDataProvider()
    return SyntheticDataProvider()
