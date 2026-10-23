#!/usr/bin/env python3
"""Generate a synthetic marketing dataset for a fictional consumer fintech
(mobile banking + credit card app).

Standard library only, fixed seed -> byte-identical output on every run.
(Faker is not needed for these columns, so it is intentionally not used; this keeps
output identical whether or not Faker is installed.)

Files written to data/marketing/:
  campaigns.csv, daily_campaign_performance.csv, customers.csv, monthly_revenue.csv

Key relationships:
  daily_campaign_performance.campaign_id -> campaigns.campaign_id
  customers.acquisition_campaign_id      -> campaigns.campaign_id (acquisition campaigns only; NULL = organic)
  For every acquisition campaign and date, the number of customers rows equals
  daily_campaign_performance.funded_accounts.
  monthly_revenue is aggregated from customers (month x product x channel).

Planted anomalies (for analysis to find):
  1. CMP-0017 (paid_social) pacing bug: 5x daily spend for 4 weeks in Jul 2025 with
     no conversion lift -> finishes well over budget.
  2. Affiliate channel drop-off in Sep 2025 (top partner paused): volume falls ~90%.
  3. CMP-0033 (display) bot traffic in Mar 2026: CTR spikes ~4x, signups/funded do not.
  4. Tracking outage on 2025-11-14: zero signups/funded recorded across all campaigns.
"""
import csv
import datetime as dt
import math
import os
import random
from collections import defaultdict

SEED = 42
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "marketing")

START = dt.date(2025, 1, 1)
END = dt.date(2026, 6, 30)  # 18 months inclusive
N_DAYS = (END - START).days + 1

TARGET_PAID_CUSTOMERS = 15_500   # funded accounts from acquisition campaigns
N_ORGANIC = 4_500                # organic customers (no campaign)

CHANNELS = ["paid_search", "paid_social", "email", "affiliate", "influencer", "display", "referral"]
PRODUCTS = ["checking", "credit_card", "savings", "personal_loan"]

# Channel economics. ctr in [lo, hi]; cpc in USD; signup = signups/click; fund = funded/signup.
# Designed so cost per funded account differs sharply by channel:
#   referral ~$45, email ~$15, affiliate ~$65, paid_search ~$95,
#   paid_social ~$165, influencer ~$260, display ~$310
CHANNEL = {
    "paid_search": dict(ctr=(0.030, 0.040), cpc=2.80, signup=0.070, fund=0.42, active=0.66, weekend=0.80),
    "paid_social": dict(ctr=(0.008, 0.015), cpc=1.25, signup=0.024, fund=0.32, active=0.50, weekend=1.15),
    "email":       dict(ctr=(0.025, 0.040), cpc=0.30, signup=0.040, fund=0.50, active=0.72, weekend=0.60),
    "affiliate":   dict(ctr=(0.015, 0.025), cpc=1.90, signup=0.060, fund=0.48, active=0.47, weekend=0.95),
    "influencer":  dict(ctr=(0.010, 0.020), cpc=1.50, signup=0.020, fund=0.29, active=0.40, weekend=1.20),
    "display":     dict(ctr=(0.005, 0.008), cpc=0.90, signup=0.011, fund=0.26, active=0.44, weekend=0.90),
    "referral":    dict(ctr=(0.030, 0.040), cpc=4.00, signup=0.150, fund=0.60, active=0.80, weekend=1.05),
}
# Product modifies funding rate (credit approval / loan underwriting is harder).
PRODUCT_FUND_MULT = {"checking": 1.10, "savings": 1.00, "credit_card": 0.85, "personal_loan": 0.70}
# Activation / reactivation campaigns convert existing users, so lower "signup" (re-engagement) rate.
OBJECTIVE_SIGNUP_MULT = {"acquisition": 1.0, "activation": 0.55, "reactivation": 0.40}

# Monthly seasonality: January resolutions + tax season (Feb-Apr) high, summer soft.
SEASON = {1: 1.25, 2: 1.15, 3: 1.20, 4: 1.15, 5: 0.95, 6: 0.85,
          7: 0.80, 8: 0.82, 9: 1.00, 10: 1.00, 11: 1.05, 12: 0.90}
# Conversion intent is also higher in tax season (refunds land in accounts).
INTENT = {1: 1.10, 2: 1.15, 3: 1.20, 4: 1.12, 5: 1.00, 6: 0.95,
          7: 0.92, 8: 0.93, 9: 1.00, 10: 1.00, 11: 1.02, 12: 0.97}

# (state, population weight) - top states plus a long tail
STATES = [("CA", 39), ("TX", 30), ("FL", 22), ("NY", 20), ("PA", 13), ("IL", 12.5), ("OH", 11.8),
          ("GA", 10.9), ("NC", 10.7), ("MI", 10), ("NJ", 9.3), ("VA", 8.6), ("WA", 7.8), ("AZ", 7.4),
          ("TN", 7), ("MA", 7), ("IN", 6.8), ("MO", 6.2), ("MD", 6.2), ("CO", 5.9), ("WI", 5.9),
          ("MN", 5.7), ("SC", 5.3), ("AL", 5.1), ("LA", 4.6), ("KY", 4.5), ("OR", 4.2), ("OK", 4),
          ("CT", 3.6), ("UT", 3.4), ("NV", 3.2), ("IA", 3.2), ("AR", 3), ("KS", 2.9), ("MS", 2.9)]
AGE_BANDS = ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
AGE_WEIGHTS = {
    "default":     [14, 32, 24, 15, 10, 5],
    "paid_social": [26, 38, 20, 9, 5, 2],
    "influencer":  [38, 38, 15, 6, 2, 1],
    "email":       [8, 24, 26, 20, 14, 8],
    "display":     [10, 26, 24, 19, 13, 8],
}

ANOM_OVERSPEND_CAMPAIGN = "CMP-0017"
ANOM_OVERSPEND_WINDOW = (dt.date(2025, 7, 1), dt.date(2025, 7, 28))
ANOM_AFFILIATE_MONTH = (2025, 9)
ANOM_BOT_CAMPAIGN = "CMP-0033"
ANOM_BOT_WINDOW = (dt.date(2026, 3, 2), dt.date(2026, 3, 22))
ANOM_OUTAGE_DAY = dt.date(2025, 11, 14)


def daterange(a, b):
    for i in range((b - a).days + 1):
        yield a + dt.timedelta(days=i)


def binom(rng, n, p):
    """Binomial draw; normal approximation for large n (stdlib has no binomial)."""
    if n <= 0 or p <= 0:
        return 0
    p = min(p, 1.0)
    if n < 60:
        return sum(1 for _ in range(n) if rng.random() < p)
    mu, sd = n * p, math.sqrt(n * p * (1 - p))
    return max(0, min(n, int(round(rng.gauss(mu, sd)))))


def weekend_factor(channel, d):
    return CHANNEL[channel]["weekend"] if d.weekday() >= 5 else 1.0


# --------------------------------------------------------------------------- campaigns
def build_campaigns(rng):
    # (channel, objective, product, always_on) - 40 campaigns
    plan = (
        [("paid_search", "acquisition", p, True) for p in PRODUCTS]
        + [("paid_search", "acquisition", "credit_card", False), ("paid_search", "acquisition", "checking", False),
           ("paid_search", "acquisition", "savings", False), ("paid_search", "acquisition", "personal_loan", False)]
        + [("paid_social", "acquisition", p, False) for p in PRODUCTS]
        + [("paid_social", "acquisition", "checking", True), ("paid_social", "acquisition", "credit_card", False),
           ("paid_social", "activation", "checking", False), ("paid_social", "activation", "credit_card", False)]
        + [("email", "acquisition", "savings", False), ("email", "activation", "checking", True),
           ("email", "activation", "credit_card", False), ("email", "reactivation", "checking", False),
           ("email", "reactivation", "savings", False), ("email", "acquisition", "credit_card", False)]
        + [("affiliate", "acquisition", p, True) for p in ("credit_card", "personal_loan")]
        + [("affiliate", "acquisition", p, False) for p in ("checking", "savings", "credit_card")]
        + [("influencer", "acquisition", p, False) for p in ("checking", "credit_card", "savings", "checking")]
        + [("display", "acquisition", p, False) for p in ("checking", "credit_card", "personal_loan")]
        + [("display", "reactivation", "credit_card", False), ("display", "acquisition", "savings", False)]
        + [("referral", "acquisition", "checking", True), ("referral", "acquisition", "credit_card", True),
           ("referral", "acquisition", "savings", False), ("referral", "acquisition", "checking", False)]
    )
    assert len(plan) == 40, len(plan)
    rng.shuffle(plan)
    # Pin anomaly campaigns to the right channel so their ids are stable and meaningful.
    for pos, channel in ((16, "paid_social"), (32, "display")):
        j = next(i for i, x in enumerate(plan)
                 if x[0] == channel and x[1] == "acquisition" and not x[3] and i != 16)
        plan[pos], plan[j] = plan[j], plan[pos]

    hooks = {
        "checking": ["Early Payday", "No-Fee Checking", "Get Paid 2 Days Early", "Switch & Save", "$0 Overdraft"],
        "credit_card": ["2% Cashback", "Build Credit", "Intro 0% APR", "Rewards Card", "No Annual Fee"],
        "savings": ["High-Yield Savings", "4.25% APY", "Round-Up Savings", "Emergency Fund", "Goal Saver"],
        "personal_loan": ["Debt Consolidation", "Fast Funding Loan", "Low-Rate Loan", "Credit Card Payoff"],
    }
    seasons = [("New Year", 1), ("Tax Refund", 2), ("Spring", 4), ("Summer", 6), ("Back to School", 8),
               ("Fall", 9), ("Holiday", 11)]
    campaigns = []
    for i, (channel, objective, product, always_on) in enumerate(plan, start=1):
        cid = f"CMP-{i:04d}"
        if always_on:
            start, end = START, END
            label = "Always-On"
        else:
            # flight campaigns: start spread across the period, ~10-17 months long
            start = START + dt.timedelta(days=rng.randint(0, N_DAYS - 200))
            end = min(END, start + dt.timedelta(days=rng.randint(300, 520)))
            sname = next(n for n, m in reversed(seasons) if m <= start.month)
            label = f"{sname} {start.year}"
        if cid == ANOM_OVERSPEND_CAMPAIGN:  # make sure it's running in July 2025
            start, end, label = dt.date(2025, 4, 14), dt.date(2025, 12, 31), "Summer 2025"
        if cid == ANOM_BOT_CAMPAIGN:        # make sure it's running in March 2026
            start, end, label = dt.date(2025, 10, 6), dt.date(2026, 6, 30), "Fall 2025"
        name = f"{label} - {rng.choice(hooks[product])} - {channel.replace('_', ' ').title()}"
        campaigns.append(dict(campaign_id=cid, name=name, channel=channel, objective=objective,
                              product=product, start_date=start, end_date=end, budget_usd=0.0))
    return campaigns


def expected_cost_per_funded(c):
    ch = CHANNEL[c["channel"]]
    return ch["cpc"] / (ch["signup"] * OBJECTIVE_SIGNUP_MULT[c["objective"]]
                        * ch["fund"] * PRODUCT_FUND_MULT[c["product"]])


def assign_budgets(rng, campaigns):
    # Relative daily spend weight by channel (search & social get the most money).
    channel_daily = {"paid_search": 1500, "paid_social": 1100, "email": 120, "affiliate": 700,
                     "influencer": 650, "display": 500, "referral": 450}
    for c in campaigns:
        days = (c["end_date"] - c["start_date"]).days + 1
        c["budget_usd"] = channel_daily[c["channel"]] * days * rng.uniform(0.6, 1.4)
    # Scale so acquisition campaigns produce ~TARGET_PAID_CUSTOMERS funded accounts.
    expected = sum(c["budget_usd"] / expected_cost_per_funded(c)
                   for c in campaigns if c["objective"] == "acquisition")
    scale = TARGET_PAID_CUSTOMERS / expected
    for c in campaigns:
        c["budget_usd"] = round(c["budget_usd"] * scale / 500) * 500  # budgets in $500 steps


# --------------------------------------------------------------------------- daily performance
def build_daily(rng, campaigns):
    rows = []
    for c in campaigns:
        ch, cid, channel = CHANNEL[c["channel"]], c["campaign_id"], c["channel"]
        days = list(daterange(c["start_date"], c["end_date"]))
        weights = [SEASON[d.month] * weekend_factor(channel, d) * rng.uniform(0.85, 1.15) for d in days]
        if channel == "affiliate":  # partner paused
            weights = [w * (0.10 if (d.year, d.month) == ANOM_AFFILIATE_MONTH else 1.0)
                       for w, d in zip(weights, days)]
        total_w = sum(weights)
        base_ctr = rng.uniform(*ch["ctr"])
        quality = rng.uniform(0.8, 1.2)  # campaign creative quality
        for d, w in zip(days, weights):
            spend = c["budget_usd"] * w / total_w  # paces to budget over flight
            conv_mult = 1.0
            ctr = base_ctr * rng.uniform(0.9, 1.1)
            if cid == ANOM_OVERSPEND_CAMPAIGN and ANOM_OVERSPEND_WINDOW[0] <= d <= ANOM_OVERSPEND_WINDOW[1]:
                spend *= 5.0           # pacing bug
                conv_mult = 1 / 5.0    # extra spend bought nothing
            # CPC rises with seasonal competition
            cpc = ch["cpc"] * (0.85 + 0.15 * SEASON[d.month]) * rng.uniform(0.9, 1.1)
            clicks = max(0, int(round(spend / cpc)))
            impressions = int(round(clicks / ctr)) if ctr > 0 else 0
            if cid == ANOM_BOT_CAMPAIGN and ANOM_BOT_WINDOW[0] <= d <= ANOM_BOT_WINDOW[1]:
                clicks *= 4            # bot clicks inflate CTR ~4x (CPM-billed, spend unchanged)...
                conv_mult = 0.25       # ...but humans are the same, so conversion rate per click collapses
            p_signup = ch["signup"] * OBJECTIVE_SIGNUP_MULT[c["objective"]] * INTENT[d.month] * quality * conv_mult
            signups = binom(rng, clicks, p_signup)
            funded = binom(rng, signups, ch["fund"] * PRODUCT_FUND_MULT[c["product"]])
            if d == ANOM_OUTAGE_DAY:
                signups = funded = 0   # tracking outage
            rows.append(dict(date=d, campaign_id=cid, impressions=impressions, clicks=clicks,
                             spend_usd=round(spend, 2), signups=signups, funded_accounts=funded))
    rows.sort(key=lambda r: (r["date"], r["campaign_id"]))
    return rows


# --------------------------------------------------------------------------- customers
def funding_amount(rng, product):
    if product == "checking":
        v = rng.lognormvariate(math.log(450), 0.9)
    elif product == "savings":
        v = rng.lognormvariate(math.log(1800), 1.0)
    elif product == "credit_card":  # first statement purchases
        v = rng.lognormvariate(math.log(320), 0.8)
    else:  # personal_loan principal
        v = min(40000, max(1000, rng.lognormvariate(math.log(9000), 0.55)))
        return round(v / 100) * 100.0
    return round(min(v, 50000), 2)


def build_customers(rng, campaigns, daily):
    cmap = {c["campaign_id"]: c for c in campaigns}
    states, sweights = zip(*STATES)
    entries = []  # (signup_date, campaign_id or None, channel, product)
    for r in daily:
        c = cmap[r["campaign_id"]]
        if c["objective"] != "acquisition":
            continue
        entries.extend([(r["date"], c["campaign_id"], c["channel"], c["product"])] * r["funded_accounts"])
    days = list(daterange(START, END))
    dweights = [SEASON[d.month] * INTENT[d.month] for d in days]
    for d in rng.choices(days, weights=dweights, k=N_ORGANIC):
        entries.append((d, None, "organic", rng.choices(PRODUCTS, weights=[45, 25, 22, 8])[0]))
    entries.sort(key=lambda e: (e[0], e[1] or ""))

    customers = []
    for i, (d, cid, channel, product) in enumerate(entries, start=1):
        active_p = CHANNEL[channel]["active"] if channel in CHANNEL else 0.74  # organic users stick
        customers.append(dict(
            customer_id=f"CUS-{i:06d}",
            signup_date=d,
            acquisition_campaign_id=cid or "",
            acquisition_channel=channel,
            state=rng.choices(states, weights=sweights)[0],
            age_band=rng.choices(AGE_BANDS, weights=AGE_WEIGHTS.get(channel, AGE_WEIGHTS["default"]))[0],
            product=product,
            first_funding_amount_usd=funding_amount(rng, product),
            is_active_90d=1 if rng.random() < active_p else 0,
        ))
    return customers


# --------------------------------------------------------------------------- monthly revenue
def month_index(d):
    return d.year * 12 + d.month - 1


def build_monthly_revenue(rng, customers):
    months = sorted({(d.year, d.month) for d in daterange(START, END)})
    last_mi = month_index(END)
    agg = defaultdict(lambda: dict(new_customers=0, interchange=0.0, interest=0.0, fee=0.0))
    for cu in customers:
        product, channel = cu["product"], cu["acquisition_channel"]
        mi0 = month_index(cu["signup_date"])
        agg[(mi0, product, channel)]["new_customers"] += 1
        # Lifetime: active customers churn ~3%/mo, inactive ones fade after 1-3 months
        if cu["is_active_90d"]:
            life = int(rng.expovariate(0.03)) + 3
        else:
            life = rng.randint(1, 3)
        engagement = rng.lognormvariate(0, 0.5)
        amt = cu["first_funding_amount_usd"]
        for k in range(life):
            mi = mi0 + k
            if mi > last_mi:
                break
            # partial first month
            frac = (1 - (cu["signup_date"].day - 1) / 30) if k == 0 else 1.0
            fade = 1.0 if cu["is_active_90d"] else 0.5
            a = agg[(mi, product, channel)]
            if product == "checking":
                a["interchange"] += 7.5 * engagement * frac * fade
                a["fee"] += 1.2 * frac * fade
            elif product == "credit_card":
                a["interchange"] += 14.0 * engagement * frac * fade
                a["interest"] += (amt * 0.06 + 9.0) * engagement * frac * fade
                a["fee"] += 2.5 * frac * fade
            elif product == "savings":
                a["interest"] += amt * 0.0025 * engagement * frac  # net interest margin ~3%/yr
            else:  # personal loan
                a["interest"] += amt * 0.012 * frac  # ~14% APR net of cost of funds
                if k == 0:
                    a["fee"] += amt * 0.04  # origination fee
    rows = []
    channels = CHANNELS + ["organic"]
    for (y, m) in months:
        mi = y * 12 + m - 1
        for product in PRODUCTS:
            for channel in channels:
                a = agg.get((mi, product, channel))
                if not a:
                    continue
                rows.append(dict(month=dt.date(y, m, 1), product=product, channel=channel,
                                 new_customers=a["new_customers"],
                                 interchange_revenue_usd=round(a["interchange"], 2),
                                 interest_revenue_usd=round(a["interest"], 2),
                                 fee_revenue_usd=round(a["fee"], 2)))
    return rows


# --------------------------------------------------------------------------- output
def write_csv(name, rows, fields):
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in r.items()})
    return path


def main():
    rng = random.Random(SEED)
    os.makedirs(OUT_DIR, exist_ok=True)

    campaigns = build_campaigns(rng)
    assign_budgets(rng, campaigns)
    daily = build_daily(rng, campaigns)
    customers = build_customers(rng, campaigns, daily)
    revenue = build_monthly_revenue(rng, customers)

    files = [
        ("campaigns.csv", campaigns,
         ["campaign_id", "name", "channel", "objective", "product", "start_date", "end_date", "budget_usd"],
         "One row per marketing campaign: channel, objective, product, flight dates, budget."),
        ("daily_campaign_performance.csv", daily,
         ["date", "campaign_id", "impressions", "clicks", "spend_usd", "signups", "funded_accounts"],
         "Daily delivery and funnel metrics per campaign over 18 months."),
        ("customers.csv", customers,
         ["customer_id", "signup_date", "acquisition_campaign_id", "acquisition_channel", "state",
          "age_band", "product", "first_funding_amount_usd", "is_active_90d"],
         "One row per funded customer; campaign id empty = organic."),
        ("monthly_revenue.csv", revenue,
         ["month", "product", "channel", "new_customers", "interchange_revenue_usd",
          "interest_revenue_usd", "fee_revenue_usd"],
         "Monthly revenue by product x acquisition channel, aggregated from customers."),
    ]
    print(f"Wrote to {os.path.normpath(OUT_DIR)} (seed={SEED})")
    for name, rows, fields, desc in files:
        write_csv(name, rows, fields)
        print(f"  {name:34} {len(rows):>7,} rows  {desc}")


if __name__ == "__main__":
    main()
