"""Optional Stooq download; explicit network failure, no synthetic fallback."""
import argparse
import io
import urllib.request
from pathlib import Path
import pandas as pd

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--tickers',nargs='+',default=['AAPL','MSFT','AMZN','JPM','XOM','JNJ'])
    parser.add_argument('--start',default='20160101')
    parser.add_argument('--end',default='20171231')
    parser.add_argument('--output',default=str(Path(__file__).resolve().parents[1]/'data/prices.csv'))
    args=parser.parse_args();frames=[]
    for ticker in args.tickers:
        ticker=ticker.upper()
        if not ticker.replace('.','').replace('-','').isalnum():raise ValueError('Invalid ticker')
        print(f'Downloading {ticker}',flush=True)
        url=f'https://stooq.com/q/d/l/?s={ticker.lower()}.us&i=d&d1={args.start}&d2={args.end}'
        req=urllib.request.Request(url,headers={'User-Agent':'BoardroomHackathonDemo/1.0'})
        with urllib.request.urlopen(req,timeout=15) as response:df=pd.read_csv(io.BytesIO(response.read()))
        if not {'Date','Close'}.issubset(df.columns):
            raise RuntimeError('Provider did not return prices. Use a browser download or Kaggle CSV.')
        frames.append(df.set_index('Date')['Close'].rename(ticker))
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    pd.concat(frames,axis=1).dropna().sort_index().to_csv(output,index_label='Date')
    print(f'Saved real downloaded data to {output}. Verify adjustment conventions and source terms.')

if __name__=='__main__':main()
