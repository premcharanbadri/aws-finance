from contracts import Review
from services.llm import ask

def run(context, marketing, accounting, proposal, validation_errors, mode='bedrock'):
    """YOUR FILE. Return findings, never silently rewrite the portfolio."""
    if mode == 'simulation':
        findings = []
        for i,error in enumerate(validation_errors):
            findings.append({'finding_id':f'POLICY_{i+1}', 'severity':'major', 'issue':error,
                             'evidence_ids':[], 'requested_change':'Correct the policy validation error.'})
        overlap = sum(proposal['weights'].get(t,0) for t in context['client']['overlap_tickers'])
        target = context['client']['overlap_review_threshold']
        if overlap > target + 1e-8:
            findings.append({'finding_id':'OVERLAP_1','severity':'major',
                'issue':f'Client-declared overlapping stocks total {overlap:.0%}, above the soft review target {target:.0%}.',
                'evidence_ids':['client_profile'],
                'requested_change':'Reduce the overlap if feasible, or document why it should be retained.'})
        output = {'verdict':'revise' if findings else 'accept', 'findings':findings,
                  'summary':'REHEARSAL: deterministic policy and client overlap checks; not AI review.'}
    else:
        output = ask(
            'Independent reviewer. Check evidence support, concentration, client-business '
            'overlap, and consistency with accounting and marketing results. Do not invent objections '
            'or future outcomes. Accept when no material concern is supported. Every substantive '
            'finding requires supplied evidence IDs; Python policy errors may use POLICY_ prefixed '
            'finding IDs and empty evidence_ids. Use unique finding IDs. Return accept, revise or '
            'escalate. Any validation_errors prohibit accept. Do not edit weights. '
            'Copy evidence IDs exactly from the supplied evidence records. '
            'Never invent, rename, abbreviate, or translate evidence IDs. '
            'Before returning, verify that every cited evidence ID exists '
            'in the supplied context.',
            {
                'research': context,
                'marketing': marketing,
                'accounting': accounting,
                'proposal': proposal,
                'validation_errors': validation_errors,
            },
            Review,
        )

    return Review.model_validate(output).model_dump()
