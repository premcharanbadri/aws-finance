"""
Data layer: a provider interface so agents never touch a concrete source.

Switch implementations with the DATA_PROVIDER env var:
    synthetic (default) | yfinance | aws

- SyntheticDataProvider: offline, deterministic (seeded), zero cost. Default
  for development and CI.
- YFinanceDataProvider: real historical prices via yfinance (free), news
  stays synthetic. Stub provided; implement when needed.
- AwsDataProvider: Redshift (prices/fundamentals) + S3/vector store (news) +
  SageMaker Feature Store. Stub provided; implement when infra exists.

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
        current_liabilities = float(rng.uniform(1e6, 3e7))
        current_assets = cash + float(rng.uniform(1e6, 2e7))
        return {
            "company": company,
            "cash_and_equivalents": round(cash, 2),
            "current_assets": round(current_assets, 2),
            "current_liabilities": round(current_liabilities, 2),
            "working_capital": round(current_assets - current_liabilities, 2),
            "existing_obligations": round(float(rng.uniform(5e5, 1e7)), 2),
            "debt_covenant_min_cash": round(cash * float(rng.uniform(0.1, 0.4)), 2),
        }


# ---------------------------------------------------------------------------
# Real data providers (same interface as synthetic)
# ---------------------------------------------------------------------------

class YFinanceDataProvider(SyntheticDataProvider):
    """Real market prices (and best-effort fundamentals) via yfinance.

    - get_prices: real split/dividend-adjusted daily closes over a date range.
      Defaults to the 2015-01-01 .. 2024-12-31 window; a `days` argument is
      honored by returning the last `days` observations of that window.
    - get_fundamentals: real values from yfinance `.info` where available,
      falling back to the synthetic fields the Predictor expects.
    - get_news / get_financials: remain synthetic (no free, reliable source;
      the investing company's balance sheet is not a public-markets concept).

    On-disk caching avoids re-hitting the network on every run. Any failure
    (offline, rate-limit, delisted ticker) degrades gracefully to the
    synthetic generator so the pipeline never hard-fails.
    """

    START = os.getenv("YF_START", "2015-01-01")
    END = os.getenv("YF_END", "2024-12-31")

    def __init__(self, seed: int = 42, inject_missing: bool = False) -> None:
        super().__init__(seed=seed, inject_missing=inject_missing)
        self._price_cache: dict[str, np.ndarray] = {}
        self._cache_dir = os.getenv("YF_CACHE_DIR", "/tmp/yf_cache")
        os.makedirs(self._cache_dir, exist_ok=True)

    def _cache_path(self, ticker: str) -> str:
        safe = "".join(c for c in ticker if c.isalnum() or c in "._-")
        return os.path.join(self._cache_dir, f"{safe}_{self.START}_{self.END}.npy")

    def _load_full_series(self, ticker: str) -> np.ndarray:
        """Full 2015-2024 adjusted close series (cached), or synthetic on failure."""
        if ticker in self._price_cache:
            return self._price_cache[ticker]
        path = self._cache_path(ticker)
        if os.path.exists(path):
            arr = np.load(path)
            self._price_cache[ticker] = arr
            return arr
        try:
            import warnings

            import yfinance as yf

            warnings.filterwarnings("ignore")
            hist = yf.Ticker(ticker).history(
                start=self.START, end=self.END, auto_adjust=True
            )["Close"].to_numpy()
            arr = np.asarray(hist, dtype=float)
            arr = arr[~np.isnan(arr)]
            if arr.size < 2:
                raise ValueError(f"no usable price data for {ticker}")
            np.save(path, arr)
            self._price_cache[ticker] = arr
            return arr
        except Exception:
            # Graceful fallback keeps the pipeline running offline / on error.
            arr = super().get_prices(ticker, days=DEFAULT_HISTORY_DAYS)
            self._price_cache[ticker] = arr
            return arr

    def get_prices(self, ticker: str, days: int = DEFAULT_HISTORY_DAYS) -> np.ndarray:
        full = self._load_full_series(ticker)
        if days and days < full.size:
            return full[-days:]
        return full

    def get_fundamentals(self, ticker: str) -> dict[str, Any]:
        base = super().get_fundamentals(ticker)  # synthetic defaults / shape
        try:
            import yfinance as yf

            info = yf.Ticker(ticker).info or {}
            if info.get("trailingPE") is not None:
                base["pe_ratio"] = round(float(info["trailingPE"]), 2)
            if info.get("profitMargins") is not None:
                base["profit_margin"] = round(float(info["profitMargins"]), 3)
            if info.get("revenueGrowth") is not None:
                base["revenue_growth"] = round(float(info["revenueGrowth"]), 3)
            if info.get("debtToEquity") is not None:
                base["debt_to_equity"] = round(float(info["debtToEquity"]) / 100.0, 2)
            if info.get("marketCap") is not None:
                base["market_cap_b"] = round(float(info["marketCap"]) / 1e9, 1)
            base["source"] = "yfinance"
        except Exception:
            base["source"] = "synthetic-fallback"
        return base


class AwsDataProvider(YFinanceDataProvider):
    """Production provider: real market prices via yfinance, and the company's
    budget/financials derived from the Redshift GL warehouse.

    Division of data domains:
      - Stock prices / fundamentals  -> yfinance (public market data; the
        warehouse does not hold equity prices).
      - Company financials / budget  -> Redshift liquidity_snapshot via
        Text-to-SQL (handled in agents/cpa.py when DATA_PROVIDER=aws).
      - News / sentiment             -> synthetic for now (S3 + vector-store
        RAG is the documented next step).

    Inherits yfinance price/fundamental behavior; get_financials pulls the
    real GL snapshot so even direct callers get warehouse-backed numbers.
    """

    def get_financials(self, company: str) -> dict[str, Any]:
        try:
            from services.financials import get_financials_from_redshift

            return get_financials_from_redshift(company).data
        except Exception:
            return super().get_financials(company)



# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_provider() -> DataProvider:
    choice = os.getenv("DATA_PROVIDER", "synthetic").lower()
    if choice == "yfinance":
        return YFinanceDataProvider()
    if choice == "aws":
        return AwsDataProvider()
    return SyntheticDataProvider()
