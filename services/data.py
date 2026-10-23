"""Data helpers: pull historical prices and create sample client profiles.

Run `python -m services.data AAPL MSFT JPM` from the repo root to write sample
profiles and prices for the given tickers to data/.
"""
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

DATA_DIR = Path("data")

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def sp500_tickers():
    """Return the current S&P 500 constituents as Yahoo-compatible tickers.

    Scrapes the Wikipedia constituents table. Yahoo Finance expects '-' instead
    of '.' in class-share tickers (e.g. BRK.B -> BRK-B), so we normalize that.
    """
    import pandas as pd

    req = urllib.request.Request(SP500_URL, headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    table = pd.read_html(io.StringIO(html))[0]
    tickers = table["Symbol"].astype(str).str.replace(".", "-", regex=False)
    return sorted(tickers.tolist())


def download_prices(tickers, start="2015-01-01", end="2024-12-31",
                    path=DATA_DIR / "prices.csv", chunk_size=100, retries=3):
    """Pull adjusted close prices from Yahoo Finance and save them as CSV.

    The universe is chosen by the caller (market strategist / portfolio agent)
    based on the client's profile, not fixed here.

    For large universes (e.g. the S&P 500) tickers are downloaded in chunks with
    retries so a single failed batch does not abort the whole run.
    """
    import pandas as pd
    import yfinance as yf

    tickers = list(tickers)
    if not tickers:
        raise ValueError("download_prices needs at least one ticker")

    frames = []
    for i in range(0, len(tickers), chunk_size):
        batch = tickers[i:i + chunk_size]
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                data = yf.download(
                    batch, start=start, end=end, auto_adjust=True,
                    progress=False, threads=True,
                )["Close"]
                # A single-ticker batch returns a Series; make it a frame.
                if isinstance(data, pd.Series):
                    data = data.to_frame(name=batch[0])
                frames.append(data)
                break
            except Exception as err:  # noqa: BLE001 - retry any transient failure
                last_err = err
                time.sleep(2 * attempt)
        else:
            print(f"  WARN: batch {batch[:3]}... failed after {retries} tries: {last_err}")
        print(f"  downloaded {min(i + chunk_size, len(tickers))}/{len(tickers)} tickers")

    if not frames:
        raise RuntimeError("no price data downloaded for any ticker")

    px = pd.concat(frames, axis=1)
    # Drop columns that came back entirely empty (delisted / bad tickers).
    px = px.dropna(axis=1, how="all")
    px = px.sort_index()
    path.parent.mkdir(parents=True, exist_ok=True)
    px.to_csv(path)
    return px


def sample_client_profiles():
    """Hand-made profiles for developing and testing agents without a database."""
    return [
        {
            "client_id": "acme",
            "company_name": "Acme Corp",
            "cash_on_hand": 2_000_000,
            "monthly_expenses": 150_000,
            "expected_monthly_revenue": 120_000,
            "obligations": [
                {"description": "Term loan payment", "amount": 300_000, "due_in_months": 6},
            ],
            "risk_tolerance": "moderate",
            "horizon_months": 12,
        },
        {
            "client_id": "tight",
            "company_name": "Tight Cash Co",
            "cash_on_hand": 400_000,
            "monthly_expenses": 120_000,
            "expected_monthly_revenue": 80_000,
            "obligations": [
                {"description": "Payroll tax", "amount": 90_000, "due_in_months": 2},
                {"description": "Equipment purchase", "amount": 150_000, "due_in_months": 5},
            ],
            "risk_tolerance": "low",
            "horizon_months": 12,
        },
        {
            "client_id": "flush",
            "company_name": "Flush Holdings",
            "cash_on_hand": 10_000_000,
            "monthly_expenses": 200_000,
            "expected_monthly_revenue": 350_000,
            "obligations": [],
            "risk_tolerance": "high",
            "horizon_months": 36,
        },
    ]


def save_sample_profiles(path=DATA_DIR / "client_profiles.json"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sample_client_profiles(), indent=2))
    return path


def load_profiles(path=DATA_DIR / "client_profiles.json"):
    return json.loads(Path(path).read_text())


if __name__ == "__main__":
    print("Wrote", save_sample_profiles())
    args = sys.argv[1:]
    if args and args[0].lower() == "sp500":
        tickers = sp500_tickers()
        print(f"Fetched {len(tickers)} S&P 500 tickers; downloading prices...")
        px = download_prices(tickers)
        print(f"Wrote {DATA_DIR / 'prices.csv'} with {px.shape[1]} tickers, {px.shape[0]} rows")
    elif args:
        download_prices(args)
        print("Wrote", DATA_DIR / "prices.csv")
    else:
        print("No tickers given; skipped price download.")
