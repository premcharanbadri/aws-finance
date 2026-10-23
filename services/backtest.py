import numpy as np
import pandas as pd

def evaluate(prices, weights, budget, cost_bps=10):
    """Entry at first post-cutoff close; fixed shares thereafter; one entry fee."""
    if not np.isfinite(budget) or budget <= 0 or not 0 <= cost_bps < 10000:
        raise ValueError('Positive budget and entry cost between 0 and 10000 bps required.')
    if set(weights) != set(prices.columns) or len(prices) < 2:
        raise ValueError('Weight universe and evaluation prices must match.')
    if abs(sum(weights.values()) - 1) > 1e-4 or any(w < 0 for w in weights.values()):
        raise ValueError('Invalid long-only portfolio.')
    # Tiny serialization rounding differences are disclosed and resolved at execution only.
    # Allocation validation has already passed; this is never used to repair invalid proposals.
    executable = pd.Series(weights, dtype=float)
    executable /= executable.sum()
    capital = budget * (1 - cost_bps / 10000)
    curve = prices.div(prices.iloc[0]).mul(executable).sum(axis=1) * capital
    peak = np.maximum(curve.cummax().to_numpy(), budget)
    metrics = {'Return': float(curve.iloc[-1] / budget - 1),
               'Max drawdown': float((curve.to_numpy() / peak - 1).min()),
               'End value': float(curve.iloc[-1]), 'Entry fee': float(budget-capital)}
    return curve, metrics

def compare(prices, portfolios, budget, cost_bps):
    curves, metrics = {}, {}
    for name, weights in portfolios.items():
        curves[name], metrics[name] = evaluate(prices, weights, budget, cost_bps)
    frame = pd.DataFrame(curves)
    records = [{'date': str(index.date()), **{k:float(v) for k,v in row.items()}}
               for index,row in frame.iterrows()]
    return {'curves': records, 'metrics': metrics,
            'assumptions': {'entry': 'First available close strictly after cutoff',
                'rebalancing': 'None; fixed shares', 'entry_cost_bps': cost_bps,
                'price_treatment': 'Uploaded price series; dividends not added separately',
                'reserve': 'Excluded from sleeve performance; assumed zero return for total-capital display',
                'rounding': 'Validated weights within 0.0001 of 1 are scaled to 1 at execution'}}
