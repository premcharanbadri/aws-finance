from contracts import Portfolio, Review, MarketReport

def validate_evidence_ids(report, context):
    allowed = {e['evidence_id'] for e in context['evidence']}
    if isinstance(report, MarketReport):
        entries = [report.evidence_ids]
    elif isinstance(report, Review):
        entries = [f.evidence_ids for f in report.findings]
    else:
        entries = [r.evidence_ids for r in report.rationale]
    for ids in entries:
        if any(ref not in allowed for ref in ids):
            raise ValueError('Report cites an unknown evidence ID.')
    if isinstance(report, Review):
        for finding in report.findings:
            # Policy validation findings are explicitly identified and may omit source IDs.
            if not finding.evidence_ids and not finding.finding_id.startswith('POLICY_'):
                raise ValueError('Substantive reviewer findings require source IDs.')

def portfolio_errors(proposal, context, previous_review=None):
    p = Portfolio.model_validate(proposal)
    errors = []
    weights = p.weights
    if set(weights) != set(context['universe']):
        errors.append('Include all and only the allowed tickers in weights.')
    if abs(sum(weights.values()) - 1) > 1e-4:
        errors.append('Weights must sum to 1 within tolerance 0.0001.')
    if any(w < 0 or w > context['max_weight'] + 1e-9 for w in weights.values()):
        errors.append('Weights violate the long-only rule or maximum position cap.')
    covered = {r.ticker for r in p.rationale if r.evidence_ids and r.reason.strip()}
    held = {t for t,w in weights.items() if w > 0}
    if not held.issubset(covered):
        errors.append('Every holding needs a rationale and evidence IDs.')
    if any(r.ticker not in weights for r in p.rationale):
        errors.append('Rationale mentions an unknown ticker.')
    if len({r.ticker for r in p.rationale}) != len(p.rationale):
        errors.append('Duplicate rationale tickers.')
    try:
        validate_evidence_ids(p, context)
    except ValueError as exc:
        errors.append(str(exc))
    if previous_review:
        required = {f['finding_id'] for f in previous_review['findings']}
        answered = {r.finding_id for r in p.responses_to_review}
        if required != answered or len(answered) != len(p.responses_to_review):
            errors.append('Revision must address each reviewer finding exactly once.')
    return errors
