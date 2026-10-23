from contracts import AccountingNarrative
from services.llm import ask

def run(context, mode='bedrock'):
    c = context['client']
    reserve = c['monthly_outflow'] * c['reserve_months']
    computed = {'protected_cash': reserve, 'committed_cash': c['commitments'],
                'investable_budget': max(0., c['cash']-reserve-c['commitments']),
                'funding_shortfall': max(0., reserve+c['commitments']-c['cash'])}
    if mode == 'simulation':
        text = {'summary': 'REHEARSAL: client-supplied reserve policy applied through Python arithmetic.',
                'review_questions': ['Confirm that the corporate investment policy permits equities.',
                                     'Have a human CPA review accounting and jurisdiction-specific treatment.']}
    else:
        text = ask('Accounting and liquidity review assistant, not a licensed CPA. Explain '
            'the precomputed reserve and budget; identify missing inputs and human review questions. '
            'Do not issue audit opinions or tax conclusions and do not change the computed amounts.',
            {'client': c, 'computed': computed}, AccountingNarrative)
    return {**AccountingNarrative.model_validate(text).model_dump(), **computed}
