"""Create clearly synthetic prices for UI rehearsal; never use as market evidence."""
from pathlib import Path
import numpy as np
import pandas as pd

def generate(path):
    rng=np.random.default_rng(2026)
    dates=pd.bdate_range('2016-01-04',periods=420)
    # A and B have strongest research trends; subsequent paths are independent simulated observations.
    data={}
    for i,t in enumerate(['DEMO_A','DEMO_B','DEMO_C','DEMO_D','DEMO_E','DEMO_F']):
        noise=rng.normal(0,.007,len(dates))
        drift=np.full(len(dates),.0018-i*.00025)
        drift[275:]=(.0002 if i<2 else .0005)
        noise[260:273]=.003 if i<2 else -.001
        data[t]=100*np.exp(np.cumsum(drift+noise))
    df=pd.DataFrame(data,index=dates)
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    df.to_csv(path,index_label='Date',float_format='%.6f')
    return df

if __name__=='__main__':
    path=Path(__file__).resolve().parents[1]/'data/synthetic_prices.csv'
    generate(path);print(f'Saved SYNTHETIC rehearsal prices to {path}')
