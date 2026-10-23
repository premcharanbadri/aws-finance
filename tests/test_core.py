import io
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from contracts import Client, Portfolio, Review
from services.data import load_prices,prepare,load_evidence
from services.validation import portfolio_errors
from services.backtest import evaluate
from orchestrator import run

class CoreTests(unittest.TestCase):
    def setup_context(self):
        prices,_=load_prices(ROOT/'data/synthetic_prices.csv')
        client=Client(name='Fixture',cash=1000000,monthly_outflow=100000,
            reserve_months=6,commitments=200000,business_description='Synthetic',
            overlap_tickers=['DEMO_A','DEMO_B'],overlap_review_threshold=.45).model_dump()
        cutoff=prices.index[int((len(prices)-1)*.65)].date()
        return prepare(prices,list(prices.columns),cutoff,prices.index[-1].date(),client,.3)

    def test_full_rehearsal_and_serialization(self):
        context,evaluation,audit=self.setup_context()
        r=run(context,evaluation,audit,'simulation')
        self.assertEqual(r['status'],'complete')
        self.assertTrue(r['revised'])
        self.assertEqual(r['accounting']['investable_budget'],200000)
        self.assertEqual(set(r['evaluation']['metrics']),{'Initial','Reviewed','Equal weight'})
        self.assertEqual(portfolio_errors(r['final'],context,r['initial_review']),[])
        json.dumps(r,allow_nan=False)

    def test_no_future_price_in_research(self):
        context,evaluation,_=self.setup_context()
        self.assertTrue((evaluation.index>pd.Timestamp(context['cutoff_date'])).all())
        for item in context['evidence']:
            if item['evidence_id'].startswith('price_'):
                self.assertLessEqual(pd.Timestamp(item['available_through']),pd.Timestamp(context['cutoff_date']))
        self.assertNotIn('evaluation',context)

    def test_buy_hold_and_fee(self):
        px=pd.DataFrame({'A':[100.,200.,100.],'B':[100.,100.,200.]},
                        index=pd.bdate_range('2020-01-01',periods=3))
        curve,metrics=evaluate(px,{'A':.5,'B':.5},1000,10)
        np.testing.assert_allclose(curve.to_numpy(),[999,1498.5,1498.5])
        self.assertAlmostEqual(metrics['Return'],.4985)
        self.assertAlmostEqual(metrics['Max drawdown'],-.001)
        self.assertAlmostEqual(metrics['Entry fee'],1)

    def test_drawdown(self):
        px=pd.DataFrame({'A':[100.,120.,60.]},index=pd.bdate_range('2020-01-01',periods=3))
        _,m=evaluate(px,{'A':1.},100,0)
        self.assertAlmostEqual(m['Max drawdown'],-.5)

    def test_csv_formats(self):
        dates=pd.bdate_range('2020-01-01',periods=30)
        wide=pd.DataFrame({'Date':dates,'A':np.arange(30)+100,'B':np.arange(30)+200})
        long=wide.melt('Date',var_name='Name',value_name='close')
        a,_=load_prices(wide.to_csv(index=False).encode())
        b,_=load_prices(long.to_csv(index=False).encode())
        np.testing.assert_allclose(a.to_numpy(),b.to_numpy())
        with self.assertRaises(ValueError):load_prices(pd.concat([long,long.iloc[:1]]).to_csv(index=False).encode())

    def test_supplemental_cutoff(self):
        raw=('evidence_id,ticker,published_at,metric,value,period,source_url\n'
             'early,DEMO_A,2016-01-01,revenue,100,FY2015,https://example.com/early\n'
             'late,DEMO_A,2020-01-01,revenue,200,FY2019,https://example.com/late\n').encode()
        records,audit=load_evidence(raw,'2017-01-01',['DEMO_A'])
        self.assertEqual([r['evidence_id'] for r in records],['early'])
        self.assertEqual(audit['excluded_after_cutoff'],1)

    def test_blocked_budget_and_infeasible_cap(self):
        context,evaluation,audit=self.setup_context()
        context['client']['cash']=1
        r=run(context,evaluation,audit,'simulation')
        self.assertEqual(r['status'],'blocked')
        self.assertNotIn('initial',r)
        prices,_=load_prices(ROOT/'data/synthetic_prices.csv')
        with self.assertRaises(ValueError):
            prepare(prices,list(prices.columns),context['cutoff_date'],prices.index[-1],context['client'],.1)

    def test_unknown_evidence_and_bad_weights(self):
        context,evaluation,audit=self.setup_context()
        r=run(context,evaluation,audit,'simulation')
        p=deepcopy(r['initial']);p['weights']['DEMO_A']=1
        self.assertTrue(portfolio_errors(p,context))
        p=deepcopy(r['initial']);p['rationale'][0]['evidence_ids']=['invented']
        self.assertTrue(portfolio_errors(p,context))

    def test_unsupported_review_does_not_get_silently_approved(self):
        context,evaluation,audit=self.setup_context()
        review={'verdict':'escalate','summary':'Human review required','findings':[]}
        with patch('agents.reviewer.run',return_value=review):
            r=run(context,evaluation,audit,'simulation')
        self.assertEqual(r['status'],'escalated')
        self.assertNotIn('evaluation',r)

    def test_policy_veto_and_revision_limit(self):
        context,evaluation,audit=self.setup_context()
        invalid={'weights':{t:.5 for t in context['universe']},
                 'rationale':[{'ticker':t,'reason':'fixture','evidence_ids':[f'price_{t}']}
                              for t in context['universe']], 'responses_to_review':[]}
        accept={'verdict':'accept','summary':'fixture','findings':[]}
        with patch('agents.portfolio.run',return_value=invalid) as generator,patch('agents.reviewer.run',return_value=accept):
            r=run(context,evaluation,audit,'simulation')
        self.assertEqual(r['status'],'escalated')
        self.assertEqual(generator.call_count,2)
        self.assertNotIn('evaluation',r)

if __name__=='__main__':unittest.main()
