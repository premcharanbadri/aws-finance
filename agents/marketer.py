"""Market Strategist agent for the virtual advisory firm hackathon project.

This single module is the "Market Strategist". It reads a synthetic client
profile, a synthetic ticker table, and cached (offline) market data, then asks
an LLM for sector tilts, client-exposure flags, and per-ticker overlap flags.
Code - never the LLM - clamps and enforces every rule. No data dated after
``CUTOFF`` is allowed to reach the LLM (no look-ahead bias).

Public API (imported by the other agents):
    StrategistOutput, StrategistResult, run_strategist, stub_strategist

Hard rules obeyed here:
  * Importing this module has NO side effects: no AWS client, no file reads,
    no printing, no folder creation, and no ``yfinance`` import at top level.
  * This module imports nothing from other project modules.
  * Tests live at the bottom as ``test_*`` functions; ``__main__`` runs pytest.
  * Strands is imported lazily, inside the wrapper function only.
"""

from __future__ import annotations

# ===== 1. CONFIG =====
import os
from typing import Callable, Literal, Optional

# All tunables live here.
CUTOFF = "2024-12-31"  # YYYY-MM-DD. The one look-ahead boundary for the whole agent.

TICKER_IDS = ["T01", "T02", "T03", "T04", "T05", "T06", "T07", "T08", "T09", "T10"]

TILT_CLAMP = 0.20  # sector tilts are clamped to [-TILT_CLAMP, +TILT_CLAMP]
CAP_HIGH_OVERLAP = 0.05  # weight cap applied to any ticker flagged "high" overlap
MAX_RETRIES = 2  # LLM parse retries before falling back to a neutral output

BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.openai.gpt-5.6-luna")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
MAX_TOKENS = 2000
TEMPERATURE = 0

# The 11 GICS sectors.
SECTORS = [
    "Information Technology",
    "Financials",
    "Energy",
    "Health Care",
    "Consumer Discretionary",
    "Consumer Staples",
    "Industrials",
    "Utilities",
    "Materials",
    "Real Estate",
    "Communication Services",
]

# Sector SPDR ETFs -> GICS sector. BENCH is the broad-market benchmark.
SECTOR_ETFS = {
    "XLK": "Information Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLI": "Industrials",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}
BENCH = "SPY"

# Macro/market series. Editable config; symbols and units are NOT verified.
MARKET_SERIES = {
    "^TNX": "us10y_yield",
    "^IRX": "us3m_yield",
    "^VIX": "vix",
    "DX-Y.NYB": "usd_index",
    "CL=F": "oil_wti",
}

# Controlled vocabulary of business / exposure tags.
TAG_VOCAB = [
    "cloud_spend",
    "consumer_discretionary",
    "rate_sensitive",
    "usd_strong_negative",
    "energy_price_sensitive",
    "china_exposure",
    "europe_exposure",
    "defensive_staples",
    "healthcare_demand",
    "credit_cycle",
    "ai_capex",
    "commodity_input",
]

# Optional macro files: filename stem -> (output label, release_lag_days).
# Editable, unverified. The release lag models publication delay vs. the
# reference period, so observations are only "available" lag days later.
OPTIONAL_MACRO = {
    "CPIAUCSL": ("cpi", 14),
    "UNRATE": ("unemployment", 7),
}

PRICES_DIR = "data/prices"
MACRO_DIR = "data/macro"
LOG_DIR = "logs"

# Default data file locations (wide-format, matching the on-disk layout).
PRICES_PATH = "data/prices.csv"  # Date + one adjusted-close column per symbol
MARKET_PATH = "data/market.csv"  # Date + sector ETFs, SPY, and MARKET_SERIES symbols
CLIENT_PROFILES_PATH = "data/client_profiles.json"  # shared; owned by the CPA agent
STRATEGIST_EXPOSURES_PATH = "data/strategist_exposures.json"  # synthetic overlay
TICKERS_PATH = "data/tickers.csv"


# ===== 2. SCHEMAS =====
from pydantic import BaseModel, Field, field_validator


class Evidence(BaseModel):
    """A single dated fact the LLM may cite to justify a regime claim."""

    fact: str
    source: str
    available_on: str  # YYYY-MM-DD; must be <= CUTOFF after guards run.


class TickerFlag(BaseModel):
    """How much a candidate ticker overlaps the client's existing exposure."""

    overlap_with_client: Literal["low", "medium", "high"]
    note: str = ""


class StrategistOutput(BaseModel):
    """The structured opinion emitted by the strategist (post-validation)."""

    agent: Literal["market_strategist"] = "market_strategist"
    regime_label: str = ""
    regime_evidence: list[Evidence] = Field(default_factory=list)
    sector_tilts: dict[str, float] = Field(default_factory=dict)
    growth_markets: list[str] = Field(default_factory=list)
    client_exposures: list[str] = Field(default_factory=list)
    ticker_flags: dict[str, TickerFlag] = Field(default_factory=dict)
    change_my_view_if: str = ""
    insufficient_evidence: bool = False


class EffectLogEntry(BaseModel):
    """One deterministic change code applied over the LLM output."""

    id: str  # "S1", "S2", ...
    target: str
    effect: str
    reason: str


class StrategistResult(BaseModel):
    """The agent's output plus the audit trail of enforced changes."""

    output: StrategistOutput
    effects: list[EffectLogEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ClientProfile(BaseModel):
    """Merged client view used by the strategist.

    Shared identity fields (``client_id``, ``name``, optional ``risk_tolerance``)
    come from ``client_profiles.json``; the synthetic exposure overlay comes
    from ``strategist_exposures.json``. ``synthetic`` must be true.
    """

    client_id: str
    name: str
    synthetic: bool
    revenue_regions: dict[str, float]  # region -> share, summing to about 1.0
    supplier_regions: dict[str, float] = Field(default_factory=dict)
    currency_exposure: list[str] = Field(default_factory=list)
    rate_sensitivity: Literal["low", "medium", "high"]
    strategy_notes: str = ""
    business_tags: list[str] = Field(default_factory=list)
    # Optional prompt context carried over from the shared profile file.
    risk_tolerance: Optional[str] = None

    @field_validator("synthetic")
    @classmethod
    def _must_be_synthetic(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("client profile must set synthetic=true")
        return v

    @field_validator("business_tags")
    @classmethod
    def _tags_in_vocab(cls, v: list[str]) -> list[str]:
        unknown = [t for t in v if t not in TAG_VOCAB]
        if unknown:
            raise ValueError(f"business_tags not in TAG_VOCAB: {unknown}")
        return v

    @field_validator("revenue_regions")
    @classmethod
    def _regions_sum_about_one(cls, v: dict[str, float]) -> dict[str, float]:
        total = sum(v.values())
        if not (0.95 <= total <= 1.05):
            raise ValueError(f"revenue_regions shares sum to {total:.3f}, expected ~1.0")
        return v


# ===== 3. LOADERS =====
# Deterministic; no network access. ``filter_to_cutoff`` is the only place the
# cutoff is enforced, and every loader routes through it.
import json

import pandas as pd


def filter_to_cutoff(df: "pd.DataFrame", date_col: str, cutoff: str) -> "pd.DataFrame":
    """Drop rows whose ``date_col`` is strictly after ``cutoff``.

    This is the single choke point for look-ahead protection. ``date_col`` is
    parsed as a date; the comparison is inclusive of ``cutoff``.
    """
    if date_col not in df.columns:
        raise ValueError(f"expected column {date_col!r} not found; have {list(df.columns)}")
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col]).dt.normalize()
    bound = pd.to_datetime(cutoff).normalize()
    return out[out[date_col] <= bound].reset_index(drop=True)


def load_prices(symbol: str, cutoff: str, prices_path: Optional[str] = None) -> "pd.DataFrame":
    """Load one symbol's adjusted-close series from the wide price file.

    Reads ``prices_path`` (default ``PRICES_PATH``), a wide CSV with a ``Date``
    column plus one adjusted-close column per symbol. Selects just ``Date`` and
    ``symbol``, drops NaN rows, sorts, renames to ``date``/``close``, then
    applies ``filter_to_cutoff``. Raises a clear error naming the symbol if its
    column is absent, or the file if it is missing.
    """
    path = prices_path or PRICES_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"price file not found: {path} (generate it with fetch_prices_to_csv)"
        )
    header = pd.read_csv(path, nrows=0)
    if "Date" not in header.columns:
        raise ValueError(f"{path}: missing required 'Date' column; have {list(header.columns)}")
    if symbol not in header.columns:
        raise ValueError(f"{path}: no price column for symbol {symbol!r}")
    df = pd.read_csv(path, usecols=["Date", symbol])
    df = df.dropna(subset=[symbol])
    df = df.rename(columns={"Date": "date", symbol: "close"})
    df = filter_to_cutoff(df, "date", cutoff)
    return df.sort_values("date").reset_index(drop=True)


def _trailing_return(df: "pd.DataFrame", months: int) -> Optional[float]:
    """Simple return over the last ``months`` of ~21 trading days each.

    Returns ``None`` if history is shorter than the requested window.
    """
    window = months * 21
    if len(df) <= window:
        return None
    first = df["close"].iloc[-1 - window]
    last = df["close"].iloc[-1]
    if first == 0 or pd.isna(first) or pd.isna(last):
        return None
    return float(last / first - 1.0)


def _annualized_vol_63d(df: "pd.DataFrame") -> Optional[float]:
    """63-day annualized volatility of daily returns, or None if too short."""
    if len(df) <= 63:
        return None
    rets = df["close"].pct_change().dropna().iloc[-63:]
    if len(rets) < 63:
        return None
    return float(rets.std(ddof=1) * (252 ** 0.5))


def _load_market_symbol(
    symbol: str, cutoff: str, market_path: str
) -> Optional["pd.DataFrame"]:
    """Load one symbol's series from the wide market file, or None if absent.

    Returns a cutoff-filtered ``date``/``close`` frame, or ``None`` when the
    file or the symbol's column is missing (caller records a warning).
    """
    if not os.path.exists(market_path):
        return None
    header = pd.read_csv(market_path, nrows=0)
    if "Date" not in header.columns or symbol not in header.columns:
        return None
    df = pd.read_csv(market_path, usecols=["Date", symbol]).dropna(subset=[symbol])
    df = df.rename(columns={"Date": "date", symbol: "close"})
    df = filter_to_cutoff(df, "date", cutoff)
    return df.sort_values("date").reset_index(drop=True)


def load_sector_features(cutoff: str, market_path: Optional[str] = None) -> dict:
    """Trailing 3/6/12m return, return vs SPY, and 63d vol per sector ETF + SPY.

    Reads the wide ``market_path`` (default ``MARKET_PATH``). Null (``None``)
    for any window longer than the available history. If the file is missing,
    returns ``{"features": {}, "warnings": [...]}`` without crashing and never
    invents values. Individual ETF columns that are absent are skipped with a
    warning.
    """
    path = market_path or MARKET_PATH
    warnings: list[str] = []
    if not os.path.exists(path):
        return {"features": {}, "warnings": [f"market file missing, no sector features: {path}"]}

    bench = _load_market_symbol(BENCH, cutoff, path)
    if bench is None or bench.empty:
        warnings.append(f"{path}: benchmark {BENCH!r} missing; sector relatives unavailable")
        bench_ret = {m: None for m in (3, 6, 12)}
    else:
        bench_ret = {m: _trailing_return(bench, m) for m in (3, 6, 12)}

    features: dict[str, dict] = {}
    for etf, sector in SECTOR_ETFS.items():
        df = _load_market_symbol(etf, cutoff, path)
        if df is None or df.empty:
            warnings.append(f"{path}: sector ETF {etf!r} ({sector}) column missing; skipped")
            continue
        r = {m: _trailing_return(df, m) for m in (3, 6, 12)}
        rel = {
            m: (None if r[m] is None or bench_ret[m] is None else r[m] - bench_ret[m])
            for m in (3, 6, 12)
        }
        features[etf] = {
            "sector": sector,
            "ret_3m": r[3],
            "ret_6m": r[6],
            "ret_12m": r[12],
            "rel_spy_3m": rel[3],
            "rel_spy_6m": rel[6],
            "rel_spy_12m": rel[12],
            "vol_63d_annualized": _annualized_vol_63d(df),
        }

    if bench is not None and not bench.empty:
        features[BENCH] = {
            "sector": "benchmark",
            "ret_3m": bench_ret[3],
            "ret_6m": bench_ret[6],
            "ret_12m": bench_ret[12],
            "rel_spy_3m": 0.0 if bench_ret[3] is not None else None,
            "rel_spy_6m": 0.0 if bench_ret[6] is not None else None,
            "rel_spy_12m": 0.0 if bench_ret[12] is not None else None,
            "vol_63d_annualized": _annualized_vol_63d(bench),
        }
    return {"features": features, "warnings": warnings}


def load_market_snapshot(cutoff: str, market_path: Optional[str] = None) -> dict:
    """Latest value, ~3m change, curve proxy, and SPY vs 200d mean.

    Reads the wide ``market_path`` (default ``MARKET_PATH``). Yields are NOT
    rescaled; the unit assumption is recorded per entry. Each entry carries
    ``available_on`` = its last observation date (<= cutoff). If the file is
    missing, returns ``{"snapshot": {}, "warnings": [...]}`` without crashing.
    Returns ``{"snapshot": {...}, "warnings": [...]}``.
    """
    path = market_path or MARKET_PATH
    warnings: list[str] = []
    if not os.path.exists(path):
        return {"snapshot": {}, "warnings": [f"market file missing, no snapshot: {path}"]}

    snapshot: dict[str, dict] = {}
    values_latest: dict[str, Optional[float]] = {}

    for symbol, label in MARKET_SERIES.items():
        df = _load_market_symbol(symbol, cutoff, path)
        if df is None or df.empty:
            warnings.append(f"{path}: market series {symbol!r} ({label}) column missing; skipped")
            continue
        latest = float(df["close"].iloc[-1])
        values_latest[label] = latest
        snapshot[label] = {
            "latest": latest,
            "change_3m": _trailing_return(df, 3),
            "available_on": pd.to_datetime(df["date"].iloc[-1]).strftime("%Y-%m-%d"),
            "unit_assumption": (
                "yield reported in raw CSV units; NOT rescaled (e.g. ^TNX may be x10)"
                if "yield" in label
                else "raw CSV units; unverified"
            ),
        }

    if "us10y_yield" in values_latest and "us3m_yield" in values_latest:
        snapshot["curve_proxy_10y_minus_3m"] = {
            "latest": values_latest["us10y_yield"] - values_latest["us3m_yield"],
            "unit_assumption": "difference of raw yield units; NOT rescaled",
        }

    spy = _load_market_symbol(BENCH, cutoff, path)
    if spy is None or spy.empty:
        warnings.append(f"{path}: benchmark {BENCH!r} missing; no 200d-mean signal")
    else:
        mean_200 = float(spy["close"].iloc[-200:].mean())
        last_spy = float(spy["close"].iloc[-1])
        snapshot["spy_vs_200d_mean"] = {
            "latest": last_spy,
            "mean_200d": mean_200,
            "above_200d_mean": bool(last_spy >= mean_200),
            "available_on": pd.to_datetime(spy["date"].iloc[-1]).strftime("%Y-%m-%d"),
        }
    return {"snapshot": snapshot, "warnings": warnings}


def load_optional_macro(cutoff: str, macro_dir: Optional[str] = None) -> dict:
    """Load each present OPTIONAL_MACRO file (columns: date, value).

    ``available_on`` = observation date + release_lag_days; only rows with
    ``available_on <= cutoff`` are kept. A missing file yields a warning (never
    an invented value). Reads from ``macro_dir`` (default ``MACRO_DIR``).
    Returns ``{"series": {...}, "warnings": [...]}``.
    """
    base = macro_dir or MACRO_DIR
    series: dict[str, dict] = {}
    warnings: list[str] = []
    for stem, (label, lag_days) in OPTIONAL_MACRO.items():
        path = os.path.join(base, f"{stem}.csv")
        if not os.path.exists(path):
            warnings.append(f"optional macro file missing, skipped: {path}")
            continue
        df = pd.read_csv(path)
        for col in ("date", "value"):
            if col not in df.columns:
                raise ValueError(f"{path}: missing required column {col!r}; have {list(df.columns)}")
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df["available_on"] = df["date"] + pd.to_timedelta(lag_days, unit="D")
        bound = pd.to_datetime(cutoff).normalize()
        df = df[df["available_on"] <= bound].sort_values("available_on").reset_index(drop=True)
        if df.empty:
            warnings.append(f"no {label} observation available on/before cutoff {cutoff}")
            continue
        last = df.iloc[-1]
        series[label] = {
            "value": float(last["value"]),
            "observation_date": pd.to_datetime(last["date"]).strftime("%Y-%m-%d"),
            "available_on": pd.to_datetime(last["available_on"]).strftime("%Y-%m-%d"),
            "release_lag_days": lag_days,
        }
    return {"series": series, "warnings": warnings}


def load_ticker_table(path: str) -> "pd.DataFrame":
    """Load and validate the ticker table.

    Columns: ticker_id, symbol, sector, regions, tags ("|"-separated), note.
    Validates ticker_id in TICKER_IDS, sector in SECTORS, each tag in TAG_VOCAB.
    Raises a clear error naming the offending value.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"ticker table not found: {path}")
    df = pd.read_csv(path, dtype=str).fillna("")
    required = ["ticker_id", "symbol", "sector", "regions", "tags", "note"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"{path}: missing required column {col!r}; have {list(df.columns)}")

    for _, row in df.iterrows():
        tid = row["ticker_id"].strip()
        if tid not in TICKER_IDS:
            raise ValueError(f"{path}: unknown ticker_id {tid!r}; allowed {TICKER_IDS}")
        if row["sector"].strip() not in SECTORS:
            raise ValueError(f"{path}: unknown sector {row['sector']!r} for {tid}; allowed {SECTORS}")
        for tag in [t for t in row["tags"].split("|") if t.strip()]:
            if tag.strip() not in TAG_VOCAB:
                raise ValueError(f"{path}: unknown tag {tag!r} for {tid}; allowed {TAG_VOCAB}")
    return df.reset_index(drop=True)


def load_client(profiles_path: str, exposures_path: str, client_id: str) -> ClientProfile:
    """Merge a shared client profile with its synthetic exposure overlay.

    Identity and optional prompt context (``client_id``, ``name``,
    ``risk_tolerance``) come from ``profiles_path`` (``client_profiles.json``,
    an array); the exposure fields come from ``exposures_path``
    (``strategist_exposures.json``, keyed by ``client_id``). Nothing is inferred
    or defaulted: a clear error is raised if ``client_id`` is missing from
    either file. ``synthetic`` must be true.
    """
    if not os.path.exists(profiles_path):
        raise FileNotFoundError(f"client profiles file not found: {profiles_path}")
    if not os.path.exists(exposures_path):
        raise FileNotFoundError(f"strategist exposures file not found: {exposures_path}")

    with open(profiles_path, "r", encoding="utf-8") as fh:
        profiles = json.load(fh)
    with open(exposures_path, "r", encoding="utf-8") as fh:
        exposures = json.load(fh)

    profile = next((p for p in profiles if p.get("client_id") == client_id), None)
    if profile is None:
        ids = [p.get("client_id") for p in profiles]
        raise ValueError(f"{profiles_path}: client_id {client_id!r} not found; have {ids}")

    overlay = exposures.get(client_id)
    if overlay is None:
        keys = [k for k in exposures.keys() if not k.startswith("_")]
        raise ValueError(f"{exposures_path}: client_id {client_id!r} not found; have {keys}")

    # Shared fields are identity/context only; exposure fields are the overlay.
    merged = {
        "client_id": client_id,
        "name": profile.get("company_name") or profile.get("name") or client_id,
        "risk_tolerance": profile.get("risk_tolerance"),
        **overlay,  # revenue_regions, supplier_regions, currency_exposure, etc.
    }
    try:
        return ClientProfile.model_validate(merged)
    except Exception as exc:  # pydantic ValidationError, re-raised with file names
        raise ValueError(
            f"invalid merged client {client_id!r} from {profiles_path} + {exposures_path}: {exc}"
        ) from exc


def fetch_prices_to_csv(
    symbols: list[str], start: str, end: str, out_path: str
) -> None:
    """MANUAL helper to populate a wide price/market cache. Never called by the agent.

    Lazily imports yfinance, downloads daily data for all ``symbols`` at once,
    flattens the MultiIndex columns, and writes a wide CSV: a ``Date`` column
    plus one adjusted-close column per symbol. Use this to produce both
    ``data/prices.csv`` and ``data/market.csv`` (ETFs + MARKET_SERIES symbols).
    """
    # Lazy import so the module has no yfinance dependency at import time.
    import yfinance as yf  # noqa: WPS433 (intentional local import)

    data = yf.download(
        symbols, start=start, end=end, auto_adjust=True, progress=False
    )
    if data is None or data.empty:
        print("WARNING: no data returned; nothing written")
        return

    # Keep adjusted close. With auto_adjust=True the 'Close' level is adjusted.
    if isinstance(data.columns, pd.MultiIndex):
        # Flatten MultiIndex: select the 'Close' field -> one column per symbol.
        close = data["Close"].copy()
    else:
        # Single symbol: build a one-column frame named after that symbol.
        close = data[["Close"]].rename(columns={"Close": symbols[0]})

    close = close.reset_index().rename(columns={"Date": "Date"})
    close["Date"] = pd.to_datetime(close["Date"]).dt.strftime("%Y-%m-%d")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    close.to_csv(out_path, index=False)
    # NOTE: yfinance's `end` date may be EXCLUSIVE. Always re-filter downstream
    # with filter_to_cutoff; do not rely on `end` to enforce the cutoff.


# ===== 4. GUARDS =====
# Code - not the LLM - enforces every rule here.


def neutral_output() -> StrategistOutput:
    """A safe do-nothing opinion: flat tilts, no flags, insufficient evidence."""
    return StrategistOutput(
        regime_label="undetermined",
        regime_evidence=[],
        sector_tilts={s: 0.0 for s in SECTORS},
        growth_markets=[],
        client_exposures=[],
        ticker_flags={},
        change_my_view_if="More reliable data within the cutoff becomes available.",
        insufficient_evidence=True,
    )


def clamp_and_check(
    output: StrategistOutput, ticker_table: "pd.DataFrame", cutoff: str
) -> StrategistResult:
    """Clamp and sanitize an LLM output, returning the audited result.

    Rules enforced:
      * sector tilts clamped to [-TILT_CLAMP, +TILT_CLAMP]; unknown sectors dropped
      * ticker_flags for IDs not in TICKER_IDS dropped
      * client_exposures not in TAG_VOCAB removed
      * each change logged as S1, S2, ... in ``effects``
      * ValueError if any evidence.available_on > cutoff
      * each "high" overlap flag adds a CAP_HIGH_OVERLAP weight-cap effect
    """
    effects: list[EffectLogEntry] = []
    warnings: list[str] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"S{counter}"

    # Look-ahead guard on cited evidence.
    bound = pd.to_datetime(cutoff).normalize()
    for ev in output.regime_evidence:
        if pd.to_datetime(ev.available_on).normalize() > bound:
            raise ValueError(
                f"evidence available_on {ev.available_on} is after cutoff {cutoff}: {ev.fact!r}"
            )

    # Sector tilts: drop unknown sectors, clamp the rest.
    clean_tilts: dict[str, float] = {}
    for sector, value in output.sector_tilts.items():
        if sector not in SECTORS:
            effects.append(
                EffectLogEntry(id=next_id(), target=f"sector_tilt[{sector}]",
                               effect="dropped", reason="sector not in SECTORS")
            )
            continue
        clamped = max(-TILT_CLAMP, min(TILT_CLAMP, float(value)))
        if clamped != float(value):
            effects.append(
                EffectLogEntry(id=next_id(), target=f"sector_tilt[{sector}]",
                               effect=f"clamped {value:+.3f}->{clamped:+.3f}",
                               reason=f"outside +/-{TILT_CLAMP}")
            )
        clean_tilts[sector] = clamped
    output.sector_tilts = clean_tilts

    # Ticker flags: drop unknown IDs.
    clean_flags: dict[str, TickerFlag] = {}
    for tid, flag in output.ticker_flags.items():
        if tid not in TICKER_IDS:
            effects.append(
                EffectLogEntry(id=next_id(), target=f"ticker_flags[{tid}]",
                               effect="dropped", reason="ticker_id not in TICKER_IDS")
            )
            continue
        clean_flags[tid] = flag
    output.ticker_flags = clean_flags

    # Client exposures must be in the tag vocabulary.
    clean_exposures: list[str] = []
    for tag in output.client_exposures:
        if tag not in TAG_VOCAB:
            effects.append(
                EffectLogEntry(id=next_id(), target=f"client_exposures[{tag}]",
                               effect="removed", reason="tag not in TAG_VOCAB")
            )
            continue
        clean_exposures.append(tag)
    output.client_exposures = clean_exposures

    # Cap any "high" overlap ticker.
    for tid, flag in output.ticker_flags.items():
        if flag.overlap_with_client == "high":
            effects.append(
                EffectLogEntry(
                    id=next_id(),
                    target=f"ticker_flags[{tid}]",
                    effect=f"cap {CAP_HIGH_OVERLAP:.0%} weight",
                    reason=flag.note or "high overlap with client exposure",
                )
            )

    return StrategistResult(output=output, effects=effects, warnings=warnings)


# ===== 5. STUB =====


def stub_strategist(client: ClientProfile, cutoff: str) -> StrategistResult:
    """Deterministic placeholder so teammates can integrate before the LLM works.

    Returns fixed tilts and exactly one fixed "high" ticker flag, keyed off the
    client id so two clients differ. Always passes ``clamp_and_check``.
    """
    # Deterministic per-client "high" flag: last char of client_id picks a slot.
    idx = sum(ord(c) for c in client.client_id) % len(TICKER_IDS)
    high_ticker = TICKER_IDS[idx]

    output = StrategistOutput(
        regime_label="stubbed regime (not a real opinion)",
        regime_evidence=[
            Evidence(
                fact="deterministic stub evidence",
                source="stub_strategist",
                available_on=cutoff,
            )
        ],
        sector_tilts={
            "Information Technology": 0.10,
            "Financials": -0.05,
            "Energy": 0.00,
            "Health Care": 0.05,
        },
        growth_markets=["stub-market"],
        client_exposures=client.business_tags[:2],
        ticker_flags={
            high_ticker: TickerFlag(
                overlap_with_client="high",
                note=f"stub high-overlap flag for {client.client_id}",
            )
        },
        change_my_view_if="This is a stub; replace with run_strategist output.",
        insufficient_evidence=False,
    )
    # Build an empty ticker table since clamp_and_check only reads TICKER_IDS.
    empty_table = pd.DataFrame(
        columns=["ticker_id", "symbol", "sector", "regions", "tags", "note"]
    )
    return clamp_and_check(output, empty_table, cutoff)


# ===== 6. PROMPT =====

SYSTEM_PROMPT = """You are the Market Strategist for a virtual advisory firm.

Follow these rules exactly:
- Use ONLY the provided client profile, ticker table, market snapshot, and sector features.
- Do NOT use any outside knowledge about specific companies or markets.
- Use only the sector names and the tag vocabulary provided in the input.
- Every regime claim MUST cite an evidence item drawn from the provided data,
  including its available_on date. Do not cite anything not in the input.
- Output exactly ONE JSON object matching the StrategistOutput schema and nothing
  else: no prose, no markdown, no code fences.
- If the data is insufficient to form an opinion, set insufficient_evidence=true
  and leave all sector_tilts at 0.
- Always include a concrete change_my_view_if statement.
- sector_tilts are soft tilts; the firm clamps them, so stay within a modest range.
"""


def build_prompt(
    client: ClientProfile,
    ticker_table: "pd.DataFrame",
    market: dict,
    sector_feats: dict,
    cutoff: str,
    optional_macro: Optional[dict] = None,
) -> str:
    """Assemble the user prompt.

    Tickers are described ONLY by ticker_id, sector, regions, and tags. The
    symbol and any company name are deliberately withheld from the LLM.
    """
    tickers_safe = []
    for _, row in ticker_table.iterrows():
        tickers_safe.append(
            {
                "ticker_id": str(row["ticker_id"]).strip(),
                "sector": str(row["sector"]).strip(),
                "regions": str(row["regions"]).strip(),
                "tags": [t.strip() for t in str(row["tags"]).split("|") if t.strip()],
            }
        )

    payload = {
        "cutoff": cutoff,
        "allowed_sectors": SECTORS,
        "allowed_tags": TAG_VOCAB,
        "allowed_ticker_ids": TICKER_IDS,
        "client_profile": client.model_dump(),
        "tickers": tickers_safe,  # no symbols, no company names
        "market_snapshot": market,
        "sector_features": sector_feats,
        "optional_macro": (optional_macro or {}).get("series", {}) if optional_macro else {},
        "output_schema_hint": {
            "agent": "market_strategist",
            "regime_label": "str",
            "regime_evidence": [{"fact": "str", "source": "str", "available_on": "YYYY-MM-DD"}],
            "sector_tilts": {"<sector>": "float in [-0.2, 0.2]"},
            "growth_markets": ["str"],
            "client_exposures": ["<tag from allowed_tags>"],
            "ticker_flags": {"<ticker_id>": {"overlap_with_client": "low|medium|high", "note": "str"}},
            "change_my_view_if": "str",
            "insufficient_evidence": "bool",
        },
    }
    return (
        "Here is all of the data you may use. Return exactly one JSON object.\n\n"
        + json.dumps(payload, indent=2, default=str)
    )


# ===== 7. AGENT =====


def _default_bedrock_caller(prompt: str) -> str:
    """Default LLM caller: Bedrock Converse. The client is created HERE, not at import."""
    import boto3  # local import keeps module import side-effect free

    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    response = client.converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": TEMPERATURE},
    )
    return "".join(
        part.get("text", "") for part in response["output"]["message"]["content"]
    )


def _strip_code_fences(text: str) -> str:
    """Remove a leading/trailing markdown code fence if the model added one."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def _append_log(client_id: str, prompt: str, raw: str) -> None:
    """Append one (prompt, raw response) record to the per-client JSONL log.

    The log directory is created HERE, inside the function, never at import.
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"market_strategist_{client_id}.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"prompt": prompt, "raw_response": raw}) + "\n")


def run_strategist(
    client: ClientProfile,
    ticker_table: "pd.DataFrame",
    market: dict,
    sector_feats: dict,
    cutoff: str,
    optional_macro: Optional[dict] = None,
    caller: Optional[Callable[[str], str]] = None,
) -> StrategistResult:
    """Run the strategist end to end.

    ``caller`` is an injectable ``(prompt) -> str`` function so tests can mock
    the LLM. If omitted, the default Bedrock caller is used. The raw output is
    parsed into ``StrategistOutput`` (retrying up to ``MAX_RETRIES`` with the
    validation error appended); on repeated failure it falls back to
    ``neutral_output()`` with a warning. ``clamp_and_check`` always runs last.
    """
    call = caller or _default_bedrock_caller
    base_prompt = build_prompt(client, ticker_table, market, sector_feats, cutoff, optional_macro)

    warnings: list[str] = []
    if optional_macro and optional_macro.get("warnings"):
        warnings.extend(optional_macro["warnings"])

    prompt = base_prompt
    last_error = ""
    parsed: Optional[StrategistOutput] = None

    for attempt in range(MAX_RETRIES + 1):
        raw = call(prompt)
        _append_log(client.client_id, prompt, raw)
        candidate = _strip_code_fences(raw)
        try:
            parsed = StrategistOutput.model_validate_json(candidate)
            break
        except Exception as exc:  # pydantic ValidationError / JSON errors
            last_error = str(exc)
            prompt = (
                base_prompt
                + "\n\nYour previous reply could not be parsed. "
                + "Fix it and return ONLY the JSON object. Error:\n"
                + last_error
            )

    if parsed is None:
        warnings.append(f"LLM output invalid after {MAX_RETRIES + 1} attempts; used neutral_output. Last error: {last_error}")
        result = clamp_and_check(neutral_output(), ticker_table, cutoff)
        result.warnings.extend(warnings)
        return result

    result = clamp_and_check(parsed, ticker_table, cutoff)
    result.warnings.extend(warnings)
    return result


# ===== 8. OPTIONAL STRANDS WRAPPER =====


def as_strands_tool():
    """Return ``run_strategist`` wrapped as a Strands tool.

    Strands is imported lazily so this module works without it installed.
    NOTE: the exact Strands API (decorator name, signature) must be checked
    against the installed version; adjust the import/usage below accordingly.
    """
    from strands import tool  # lazy import; verify against installed strands version

    @tool
    def market_strategist_tool(
        client: ClientProfile,
        ticker_table: "pd.DataFrame",
        market: dict,
        sector_feats: dict,
        cutoff: str,
        optional_macro: Optional[dict] = None,
    ) -> StrategistResult:
        """Market Strategist agent exposed as a Strands tool."""
        return run_strategist(
            client, ticker_table, market, sector_feats, cutoff, optional_macro
        )

    return market_strategist_tool


# ===== 9. TESTS =====
# No network, no AWS. Run with: python agents/marketer.py  (or pytest).
# NOTE: pytest and re are imported lazily (inside the tests / __main__ block) so
# that importing this module as the Market Strategist never requires pytest.
# Tests pass paths/dirs explicitly instead of patching module globals.


def _sample_ticker_table() -> "pd.DataFrame":
    return pd.DataFrame(
        [
            {"ticker_id": "T01", "symbol": "AAAA", "sector": "Information Technology",
             "regions": "US", "tags": "ai_capex|cloud_spend", "note": "x"},
            {"ticker_id": "T02", "symbol": "BBBB", "sector": "Energy",
             "regions": "US", "tags": "energy_price_sensitive", "note": "x"},
        ]
    )


def _sample_client(client_id: str = "client_test") -> ClientProfile:
    return ClientProfile(
        client_id=client_id,
        name="Test Co",
        synthetic=True,
        revenue_regions={"US": 1.0},
        supplier_regions={"US": 1.0},
        currency_exposure=["USD"],
        rate_sensitivity="high",
        strategy_notes="n/a",
        business_tags=["cloud_spend", "rate_sensitive"],
    )


def test_filter_to_cutoff_drops_later_rows():
    df = pd.DataFrame({"date": ["2024-12-30", "2024-12-31", "2025-01-02"], "close": [1, 2, 3]})
    out = filter_to_cutoff(df, "date", "2024-12-31")
    assert list(out["close"]) == [1, 2]


def test_load_prices_wide_csv_filters_cutoff(tmp_path):
    # Tiny wide CSV with a NaN gap and rows after the cutoff.
    p = tmp_path / "prices.csv"
    p.write_text(
        "Date,AAAA,BBBB\n"
        "2024-12-30,10.0,1.0\n"
        "2024-12-31,11.0,\n"      # BBBB missing on this day
        "2025-01-02,12.0,3.0\n"   # after cutoff
    )
    df = load_prices("AAAA", "2024-12-31", prices_path=str(p))
    assert list(df["date"].dt.strftime("%Y-%m-%d")) == ["2024-12-30", "2024-12-31"]
    assert list(df["close"]) == [10.0, 11.0]
    # BBBB's NaN row is dropped, and the post-cutoff row is excluded.
    df_b = load_prices("BBBB", "2024-12-31", prices_path=str(p))
    assert list(df_b["date"].dt.strftime("%Y-%m-%d")) == ["2024-12-30"]


def test_load_prices_missing_symbol_names_it(tmp_path):
    import pytest

    p = tmp_path / "prices.csv"
    p.write_text("Date,AAAA\n2024-12-30,10.0\n")
    with pytest.raises(ValueError, match="ZZZZ"):
        load_prices("ZZZZ", "2024-12-31", prices_path=str(p))


def test_optional_macro_release_lag_excludes_post_cutoff(tmp_path):
    macro = tmp_path / "macro"
    macro.mkdir()
    # Observation on the cutoff but with a 14-day release lag => available after cutoff.
    (macro / "CPIAUCSL.csv").write_text("date,value\n2024-12-31,3.1\n2024-12-01,3.0\n")
    res = load_optional_macro("2024-12-31", macro_dir=str(macro))
    # The 12-31 obs (available 2025-01-14) is excluded; 12-01 (available 12-15) kept.
    assert res["series"]["cpi"]["observation_date"] == "2024-12-01"


def test_market_loaders_missing_file_warn_not_crash(tmp_path):
    missing = str(tmp_path / "no_such_market.csv")
    feats = load_sector_features(CUTOFF, market_path=missing)
    snap = load_market_snapshot(CUTOFF, market_path=missing)
    assert feats["features"] == {} and feats["warnings"]
    assert snap["snapshot"] == {} and snap["warnings"]


def test_clamp_tilt_and_logs():
    out = StrategistOutput(
        sector_tilts={"Information Technology": 0.9},
        regime_evidence=[Evidence(fact="f", source="s", available_on="2024-01-01")],
    )
    res = clamp_and_check(out, _sample_ticker_table(), CUTOFF)
    assert res.output.sector_tilts["Information Technology"] == TILT_CLAMP
    assert any("clamped" in e.effect for e in res.effects)


def test_clamp_drops_unknown_sector_ticker_and_tag():
    out = StrategistOutput(
        sector_tilts={"Information Technology": 0.1, "Made Up Sector": 0.1},
        ticker_flags={
            "T01": TickerFlag(overlap_with_client="low"),
            "ZZZ": TickerFlag(overlap_with_client="low"),
        },
        client_exposures=["cloud_spend", "not_a_tag"],
    )
    res = clamp_and_check(out, _sample_ticker_table(), CUTOFF)
    assert "Made Up Sector" not in res.output.sector_tilts
    assert "ZZZ" not in res.output.ticker_flags
    assert res.output.client_exposures == ["cloud_spend"]


def test_clamp_raises_on_future_evidence():
    import pytest

    out = StrategistOutput(
        regime_evidence=[Evidence(fact="f", source="s", available_on="2025-06-01")],
    )
    with pytest.raises(ValueError):
        clamp_and_check(out, _sample_ticker_table(), CUTOFF)


def test_clamp_high_flag_creates_cap_effect():
    out = StrategistOutput(
        ticker_flags={"T01": TickerFlag(overlap_with_client="high", note="too much")}
    )
    res = clamp_and_check(out, _sample_ticker_table(), CUTOFF)
    assert any("cap" in e.effect for e in res.effects)


def test_stub_passes_clamp_and_check():
    res = stub_strategist(_sample_client(), CUTOFF)
    assert isinstance(res, StrategistResult)
    assert any(e.effect.startswith("cap") for e in res.effects)


def test_load_client_merges_and_errors_on_missing(tmp_path):
    import pytest

    profiles = tmp_path / "client_profiles.json"
    exposures = tmp_path / "strategist_exposures.json"
    profiles.write_text(json.dumps([
        {"client_id": "acme", "company_name": "Acme Corp", "risk_tolerance": "moderate"},
    ]))
    exposures.write_text(json.dumps({
        "acme": {
            "synthetic": True,
            "revenue_regions": {"US": 1.0},
            "supplier_regions": {"US": 1.0},
            "currency_exposure": ["USD"],
            "rate_sensitivity": "medium",
            "strategy_notes": "UNVERIFIED placeholder, human must check",
            "business_tags": ["cloud_spend"],
        },
    }))
    client = load_client(str(profiles), str(exposures), "acme")
    assert client.name == "Acme Corp"
    assert client.risk_tolerance == "moderate"
    assert client.rate_sensitivity == "medium"
    # Missing in exposures file.
    with pytest.raises(ValueError, match="tight"):
        load_client(str(profiles), str(exposures), "tight")


def test_run_strategist_with_mocked_caller_returns_valid_output():
    good = StrategistOutput(
        regime_label="risk-on",
        regime_evidence=[Evidence(fact="SPY above 200d", source="snapshot", available_on="2024-12-30")],
        sector_tilts={"Information Technology": 0.1},
        ticker_flags={"T01": TickerFlag(overlap_with_client="medium")},
        change_my_view_if="vol spikes",
    )

    def caller(_prompt: str) -> str:
        return good.model_dump_json()

    res = run_strategist(_sample_client(), _sample_ticker_table(), {}, {}, CUTOFF, caller=caller)
    assert res.output.regime_label == "risk-on"
    assert res.output.sector_tilts["Information Technology"] == 0.1


def test_run_strategist_falls_back_to_neutral_on_bad_output():
    calls = {"n": 0}

    def bad_caller(_prompt: str) -> str:
        calls["n"] += 1
        return "this is not json"

    res = run_strategist(_sample_client(), _sample_ticker_table(), {}, {}, CUTOFF, caller=bad_caller)
    assert calls["n"] == MAX_RETRIES + 1
    assert res.output.insufficient_evidence is True
    assert all(v == 0.0 for v in res.output.sector_tilts.values())
    assert any("neutral_output" in w for w in res.warnings)


def test_two_clients_produce_different_effects():
    def caller_for(high_ticker: str) -> Callable[[str], str]:
        def _c(_prompt: str) -> str:
            out = StrategistOutput(
                ticker_flags={high_ticker: TickerFlag(overlap_with_client="high", note="x")},
            )
            return out.model_dump_json()
        return _c

    res_a = run_strategist(_sample_client("client_a"), _sample_ticker_table(), {}, {}, CUTOFF, caller=caller_for("T01"))
    res_b = run_strategist(_sample_client("client_b"), _sample_ticker_table(), {}, {}, CUTOFF, caller=caller_for("T02"))
    caps_a = [e.target for e in res_a.effects if e.effect.startswith("cap")]
    caps_b = [e.target for e in res_b.effects if e.effect.startswith("cap")]
    assert caps_a != caps_b


def test_prompt_hides_symbols_and_has_no_future_dates():
    import re

    table = _sample_ticker_table()
    prompt = build_prompt(_sample_client(), table, {}, {}, CUTOFF)
    # No real symbol from the ticker table leaks into the prompt.
    for sym in table["symbol"]:
        assert sym not in prompt
    # No date later than CUTOFF appears anywhere in the prompt text.
    cutoff_dt = pd.to_datetime(CUTOFF)
    for found in re.findall(r"\d{4}-\d{2}-\d{2}", prompt):
        assert pd.to_datetime(found) <= cutoff_dt, f"future date leaked: {found}"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
