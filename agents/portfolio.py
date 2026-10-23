from contracts import Portfolio
from services.llm import ask
from services.simulation import allocation

def run(context, marketing, accounting, previous=None, review=None, mode='bedrock'):
    """YOUR FILE. Keep this signature and the Portfolio output contract."""
    if mode == 'simulation':
        weights = allocation(context, revised=review is not None)
        output = {'weights': weights,
            'rationale': [{'ticker':t, 'reason':'REHEARSAL: pre-cutoff momentum ranking with position caps.',
                          'evidence_ids':[f'price_{t}']} for t,w in weights.items() if w > 0],
            'responses_to_review': [{'finding_id':f['finding_id'], 'decision':'accepted',
                'reason':'Recomputed the rehearsal allocation with the client overlap review target.'}
                for f in (review or {}).get('findings', [])]}
    else:
        output = ask('Portfolio generator. Return weights for ALL and ONLY the allowed tickers. '
            'Long-only, sum to 1, obey max_weight. The sleeve is the approved investable_budget; '
            'protected cash is excluded. Give every positive holding a rationale with supplied evidence IDs. '
            'Use financial or macro evidence only if supplied. Do not predict numerical returns. '
            'When review is supplied, address every finding_id exactly once in responses_to_review, '
            'accepting or rejecting with evidence-based reasons. A soft client overlap target is a '
            'review consideration, not an absolute policy limit.',
            {'research':context, 'marketing':marketing, 'accounting':accounting,
             'previous':previous, 'review':review}, Portfolio)
    return Portfolio.model_validate(output).model_dump()
