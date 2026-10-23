"""Deterministic rehearsal only. No model call and no prediction claim."""
import numpy as np

def allocation(context, revised=False):
    universe = context['universe']
    records = {e['ticker']:e for e in context['evidence'] if e['evidence_id'].startswith('price_')}
    score = np.array([max(.01, 1 + records[t]['return_20_sessions'] * 10) for t in universe])
    cap = context['max_weight']
    overlap = set(context['client']['overlap_tickers'])
    # Greedy momentum allocation for a visibly inspectable rehearsal policy.
    weights = dict.fromkeys(universe, 0.)
    remaining = 1.
    group_remaining = context['client']['overlap_review_threshold'] if revised else 1.
    for index in np.argsort(-score):
        t = universe[index]
        amount = min(cap, remaining, group_remaining if t in overlap else remaining)
        weights[t] = float(amount)
        remaining -= amount
        if t in overlap:
            group_remaining -= amount
    if remaining > 1e-7:
        # Feasibility of the soft overlap request is not guaranteed. Preserve a valid portfolio,
        # then let the reviewer escalate unresolved concerns instead of violating hard constraints.
        for t in universe:
            amount = min(cap-weights[t], remaining)
            weights[t] += amount
            remaining -= amount
    return weights
