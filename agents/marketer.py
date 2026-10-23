from contracts import MarketReport
from services.llm import ask

def run(context, mode='bedrock'):
    if mode == 'simulation':
        report = {'summary': 'REHEARSAL: market context comes from the synthetic client profile.',
            'priority_markets': [],
            'portfolio_implications': ['Review overlap with the client-declared business exposures.'],
            'evidence_ids': ['client_profile'],
            'missing_information': ['Verified market-size, customer and geographic research is not bundled.']}
    else:
        report = ask('Market strategist. Identify priority markets only when supplied evidence supports '
            'them. Explain client business exposure and its relevance to portfolio concentration. '
            'If market evidence is missing, leave priority_markets empty and name the gap.',
            context, MarketReport)
    return MarketReport.model_validate(report).model_dump()
