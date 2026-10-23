import hashlib
import io
import json
import numpy as np
import pandas as pd

def read_bytes(source):
    if isinstance(source, bytes):
        return source
    if hasattr(source, 'getvalue'):
        return source.getvalue()
    if hasattr(source, 'read'):
        return source.read()
    with open(source, 'rb') as file:
        return file.read()

def load_prices(source):
    raw_bytes = read_bytes(source)
    df = pd.read_csv(io.BytesIO(raw_bytes))
    df.columns = [str(c).strip() for c in df.columns]
    names = {c.lower(): c for c in df.columns}
    date = names.get('date')
    ticker = names.get('ticker') or names.get('name') or names.get('symbol')
    if not date:
        raise ValueError('Price CSV needs a date or Date column.')
    df[date] = pd.to_datetime(df[date], errors='raise', utc=True).dt.tz_convert(None).dt.normalize()
    if df[date].isna().any():
        raise ValueError('Missing dates are not allowed.')
    if ticker:
        close = names.get('close')
        if not close:
            raise ValueError('Long CSV needs date, ticker/Name/symbol, and close columns.')
        if df[ticker].isna().any():
            raise ValueError('Missing tickers are not allowed.')
        df[ticker] = df[ticker].astype(str).str.strip().str.upper()
        if df.duplicated([date, ticker]).any():
            raise ValueError('Duplicate date/ticker pairs; clean the source instead of averaging them.')
        prices = df.pivot(index=date, columns=ticker, values=close)
    else:
        prices = df.set_index(date)
    prices.columns = [str(c).strip().upper() for c in prices.columns]
    if prices.index.has_duplicates or prices.columns.has_duplicates:
        raise ValueError('Duplicate dates or ticker columns.')
    try:
        prices = prices.apply(pd.to_numeric, errors='raise').sort_index()
    except (ValueError, TypeError) as exc:
        raise ValueError('Wide format must contain only Date and numeric ticker price columns.') from exc
    observed = prices.to_numpy(dtype=float)
    if np.isinf(observed).any() or (prices <= 0).any().any():
        raise ValueError('Prices must be finite and positive; blanks may be dropped with a visible warning.')
    if len(prices) < 25:
        raise ValueError('Need at least 25 dated price rows.')
    return prices, hashlib.sha256(raw_bytes).hexdigest()

def load_evidence(source, cutoff, tickers):
    if source is None:
        return [], {'included': 0, 'excluded_after_cutoff': 0}
    df = pd.read_csv(io.BytesIO(read_bytes(source)), dtype=str, keep_default_na=False)
    required = {'evidence_id','ticker','published_at','metric','value','period','source_url'}
    if not required.issubset(df.columns):
        raise ValueError('Evidence CSV requires: ' + ', '.join(sorted(required)))
    if any((df[c].str.strip() == '').any() for c in required):
        raise ValueError('Evidence fields cannot be blank.')
    if df.evidence_id.duplicated().any():
        raise ValueError('Evidence IDs must be unique.')
    if not df.source_url.str.startswith('https://').all():
        raise ValueError('Evidence source_url must be an https URL.')
    published = pd.to_datetime(df.published_at, errors='raise', utc=True).dt.tz_convert(None)
    if published.isna().any():
        raise ValueError('Evidence publication dates are required.')
    # Date-only cutoff means end of that calendar day, no trading before next session.
    eligible = published < pd.Timestamp(cutoff) + pd.Timedelta(days=1)
    selected = df.ticker.str.upper().isin(tickers + ['ALL'])
    filtered = df.loc[eligible & selected].copy()
    filtered['ticker'] = filtered.ticker.str.upper()
    return filtered.to_dict('records'), {'included': len(filtered),
        'excluded_after_cutoff': int((~eligible).sum())}

def prepare(prices, tickers, cutoff, end, client, cap, supplemental=None, source='Uploaded CSV', fingerprint=''):
    if not 4 <= len(tickers) <= 8 or len(set(tickers)) != len(tickers):
        raise ValueError('Choose 4 to 8 unique tickers.')
    if not 0 < cap <= 1 or cap * len(tickers) < 1 - 1e-9:
        raise ValueError('Position cap is infeasible for this universe.')
    cutoff, end = pd.Timestamp(cutoff).normalize(), pd.Timestamp(end).normalize()
    if end <= cutoff:
        raise ValueError('Evaluation end must follow the research cutoff.')
    if not set(tickers).issubset(prices.columns):
        raise ValueError('Selected ticker missing from data.')
    raw = prices.loc[prices.index <= end, tickers]
    clean = raw.dropna()
    train = clean.loc[clean.index <= cutoff].tail(253)
    evaluation = clean.loc[(clean.index > cutoff) & (clean.index <= end)]
    if len(train) < 22 or len(evaluation) < 2:
        raise ValueError('Need 22 common research sessions and 2 evaluation sessions after cutoff.')
    evidence = [{'evidence_id': 'client_profile', 'source': 'Synthetic user-provided demo profile',
                 'description': client}]
    for t in tickers:
        series = train[t]
        daily = series.pct_change().dropna()
        evidence.append({'evidence_id': f'price_{t}', 'ticker': t, 'source': source,
            'available_through': str(train.index[-1].date()),
            'research_start': str(train.index[0].date()),
            'return_20_sessions': float(series.iloc[-1] / series.iloc[-21] - 1),
            'window_return': float(series.iloc[-1] / series.iloc[0] - 1),
            'annualized_volatility': float(daily.std() * np.sqrt(252)),
            'historical_max_drawdown': float((series / series.cummax() - 1).min())})
    extra, evidence_stats = load_evidence(supplemental, cutoff, tickers)
    ids = {e['evidence_id'] for e in evidence}
    if any(e['evidence_id'] in ids for e in extra):
        raise ValueError('Supplemental IDs collide with reserved client_profile or price_TICKER IDs.')
    evidence.extend(extra)
    context = {'client': client, 'universe': tickers, 'max_weight': cap,
               'cutoff_date': str(cutoff.date()), 'evidence': evidence,
               'correlations': train.pct_change().dropna().corr().fillna(0).to_dict()}
    audit = {'source': source, 'price_sha256': fingerprint, 'dropped_incomplete_dates': len(raw)-len(clean),
             'research_sessions': len(train), 'evaluation_sessions': len(evaluation),
             'entry_date': str(evaluation.index[0].date()), 'end_date': str(evaluation.index[-1].date()),
             'supplemental': evidence_stats}
    return context, evaluation, audit
