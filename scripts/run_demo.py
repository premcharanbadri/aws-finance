import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services.data import load_prices,prepare
from orchestrator import run

root=Path(__file__).resolve().parents[1]
prices,fingerprint=load_prices(root/'data/synthetic_prices.csv')
client={'name':'Synthetic demo client','cash':1000000,'monthly_outflow':100000,
        'reserve_months':6,'commitments':200000,'business_description':'Synthetic manufacturer',
        'overlap_tickers':['DEMO_A','DEMO_B'],'overlap_review_threshold':.45}
cutoff=prices.index[int((len(prices)-1)*.65)].date()
context,evaluation,audit=prepare(prices,list(prices.columns),cutoff,prices.index[-1].date(),
                               client,.30,source='SYNTHETIC rehearsal',fingerprint=fingerprint)
audit['synthetic_prices']=True
result=run(context,evaluation,audit,mode='simulation',emit=print)
(root/'runs').mkdir(exist_ok=True)
path=root/'runs'/f"{result['run_id']}.json"
path.write_text(json.dumps(result,indent=2),encoding='utf-8')
print('Status:',result['status']);print('Revised:',result.get('revised'))
print('Saved:',path)
if result['status']!='complete':raise SystemExit(1)
