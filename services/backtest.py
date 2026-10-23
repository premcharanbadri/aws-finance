"""
Portfolio backtest -> objective accept/reject signal for the Coordinator.

Given proposed weights and historical price series (from the DataProvider),
simulate a buy-and-hold portfolio and compute summary statistics. The
Coordinator uses the Sharpe ratio (and max drawdown) as one concrete,
non-LLM criterion for whether to accept a proposal or trigger a revision.

Deterministic numpy only.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def _portfolio_returns(weights: dict[str, float], prices: dict[str, np.ndarray]) -> np.ndarray:
    """Weighted daily returns across the held tickers (aligned by min length)."""
    tickers = [t for t in weights if t in prices and prices[t].size > 1]
    if not tickers:
        return np.array([])
    min_len = min(prices[t].size for t in tickers)
    rets = []
    w = []
    for t in tickers:
        series = prices[t][-min_len:]
        daily = np.diff(series) / series[:-1]
        rets.append(daily)
        w.append(weights[t])
    w = np.array(w)
    w = w / w.sum() if w.sum() else np.ones(len(w)) / len(w)
    return (np.vstack(rets).T @ w)


def backtest(
    weights: dict[str, float],
    prices: dict[str, np.ndarray],
    risk_free_daily: float = 0.02 / 252,
) -> dict[str, Any]:
    """Return annualized return, volatility, Sharpe, and max drawdown."""
    daily = _portfolio_returns(weights, prices)
    if daily.size == 0:
        return {"ok": False, "reason": "no price data for weighted tickers"}

    ann_return = float(np.mean(daily) * 252)
    ann_vol = float(np.std(daily) * np.sqrt(252))
    sharpe = float((np.mean(daily) - risk_free_daily) / (np.std(daily) + 1e-12) * np.sqrt(252))

    curve = np.cumprod(1 + daily)
    peak = np.maximum.accumulate(curve)
    max_dd = float(np.min(curve / peak - 1.0))

    return {
        "ok": True,
        "annual_return": round(ann_return, 4),
        "annual_volatility": round(ann_vol, 4),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 4),
    }


def accept_signal(
    bt: dict[str, Any],
    min_sharpe: float = 0.5,
    max_allowed_drawdown: float = -0.35,
) -> tuple[bool, str]:
    """Translate backtest stats into an accept/reject decision + reason."""
    if not bt.get("ok"):
        return False, bt.get("reason", "backtest failed")
    if bt["sharpe"] < min_sharpe:
        return False, f"Sharpe {bt['sharpe']} below threshold {min_sharpe}"
    if bt["max_drawdown"] < max_allowed_drawdown:
        return False, f"Max drawdown {bt['max_drawdown']} worse than {max_allowed_drawdown}"
    return True, f"Sharpe {bt['sharpe']}, drawdown {bt['max_drawdown']} acceptable"


def benchmark_equal_weight(prices: dict[str, np.ndarray]) -> dict[str, Any]:
    """Equal-weight portfolio over all tickers as a comparison benchmark."""
    tickers = [t for t, p in prices.items() if p.size > 1]
    if not tickers:
        return {"ok": False, "reason": "no price data"}
    w = {t: 1.0 / len(tickers) for t in tickers}
    return backtest(w, prices)


# ---------------------------------------------------------------------------
# Evaluation Layer 1: forecast quality (ML metrics)
# ---------------------------------------------------------------------------

def walk_forward_eval(
    prices: np.ndarray,
    predict_fn,
    train_window: int = 180,
    horizon: int = 21,
    step: int = 21,
) -> dict[str, Any]:
    """Walk-forward evaluation of a forecasting function.

    Never uses future data to predict the past: at each step we feed
    `predict_fn` only the prices up to time t (the train window) and compare
    its forecast against the realized return over the next `horizon` days.

    `predict_fn(history: np.ndarray) -> dict` must return:
        {expected_return, lower, upper}  (horizon-scaled return)

    Returns directional accuracy, MAE, and prediction-interval coverage —
    the three metrics that validate the Predictor + the inference engine's
    confidence calibration.
    """
    if prices.size < train_window + horizon + 1:
        return {"ok": False, "reason": "insufficient history for walk-forward"}

    hits, abs_errs, covered, n = 0, [], 0, 0
    t = train_window
    while t + horizon < prices.size:
        history = prices[:t]
        realized = float(prices[t + horizon] / prices[t] - 1.0)
        try:
            pred = predict_fn(history)
        except Exception:
            t += step
            continue
        er = float(pred.get("expected_return", 0.0))
        lo = float(pred.get("lower", er))
        hi = float(pred.get("upper", er))

        if (er >= 0) == (realized >= 0):
            hits += 1
        abs_errs.append(abs(er - realized))
        if lo <= realized <= hi:
            covered += 1
        n += 1
        t += step

    if n == 0:
        return {"ok": False, "reason": "no evaluation windows produced"}
    return {
        "ok": True,
        "windows": n,
        "directional_accuracy": round(hits / n, 4),
        "mae": round(float(np.mean(abs_errs)), 4),
        "interval_coverage": round(covered / n, 4),
    }
