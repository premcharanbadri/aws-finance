#!/usr/bin/env python3
"""Generate synthetic accounting data for the same fictional consumer fintech whose
marketing data lives in data/marketing/ (run generate_marketing_data.py first).

Standard library only, fixed seed -> identical output on every run.
All money is handled in integer cents so every journal balances exactly.

Files written to data/accounting/:
  chart_of_accounts.csv  general_ledger.csv  ap_invoices.csv
  tax_obligations.csv    debt_facilities.csv budget.csv

Consistency with the marketing data:
  - Revenue journals = monthly_revenue.csv totals by month x product.
  - Marketing expense (6100/6110) comes from ad-platform AP invoices whose amounts equal
    monthly spend by channel in daily_campaign_performance.csv.
  - Personal-loan originations = first_funding_amount_usd of personal_loan customers.
  - Hosting and bank-partner costs scale with the cumulative customer count.

Planted findings for a CPA-style review:
  1. Duplicate AP invoice: Meta Ads Oct 2025 invoice keyed and paid twice.
  2. Minimum-cash covenant ($7.5M) on the venture debt breached at the Sep 2025 month-end,
     cured by the $35M Series B on 2025-10-15.
  3. Q3 2025 California sales/use tax paid 12 days late with a penalty.
  4. Overdue AP at period end: a disputed influencer invoice and a 90+ day legal invoice.
  5. Marketing GL ties to platform spend except the duplicate Meta invoice (+$37.3K);
     CMP-0017's July overspend is visible per campaign in marketing.dim_campaigns.
  6. Revenue below plan most months; Mar 2026 payroll spike from annual bonuses.
  7. Warehouse facility utilization approaching its 90% limit.
"""
import csv
import datetime as dt
import math
import os
import random
from collections import defaultdict

SEED = 7
HERE = os.path.dirname(os.path.abspath(__file__))
MKT_DIR = os.path.normpath(os.path.join(HERE, "..", "data", "marketing"))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "data", "accounting"))

START = dt.date(2025, 1, 1)
END = dt.date(2026, 6, 30)
MONTHS = [(y, m) for y in (2025, 2026) for m in range(1, 13) if dt.date(y, m, 1) <= END]

ACCOUNTS = [
    # account_id, name, type, subtype, normal_balance
    (1000, "Cash - Operating", "asset", "cash", "debit"),
    (1010, "Cash - Restricted (card program collateral)", "asset", "restricted_cash", "debit"),
    (1100, "Interchange Receivable", "asset", "receivable", "debit"),
    (1200, "Loans Receivable - Personal Loans", "asset", "loans", "debit"),
    (1210, "Allowance for Credit Losses", "asset", "contra_asset", "credit"),
    (1400, "Prepaid Expenses", "asset", "prepaid", "debit"),
    (2000, "Accounts Payable", "liability", "payable", "credit"),
    (2200, "Taxes Payable", "liability", "tax_payable", "credit"),
    (2300, "Warehouse Credit Facility", "liability", "debt", "credit"),
    (2400, "Venture Debt", "liability", "debt", "credit"),
    (3000, "Common Stock & APIC", "equity", "paid_in_capital", "credit"),
    (3100, "Retained Earnings (Accumulated Deficit)", "equity", "retained_earnings", "credit"),
    (4000, "Interchange Revenue", "revenue", "revenue", "credit"),
    (4100, "Interest Revenue", "revenue", "revenue", "credit"),
    (4200, "Fee Revenue", "revenue", "revenue", "credit"),
    (5000, "Card Processing & Network Costs", "expense", "cost_of_revenue", "debit"),
    (5100, "Sponsor Bank Fees", "expense", "cost_of_revenue", "debit"),
    (5200, "Provision for Credit Losses", "expense", "cost_of_revenue", "debit"),
    (5300, "Interest Expense", "expense", "interest_expense", "debit"),
    (6000, "Salaries & Wages", "expense", "operating_expense", "debit"),
    (6010, "Payroll Taxes & Benefits", "expense", "operating_expense", "debit"),
    (6100, "Marketing - Paid Acquisition", "expense", "operating_expense", "debit"),
    (6110, "Marketing - Referral Bonuses", "expense", "operating_expense", "debit"),
    (6200, "Cloud Hosting", "expense", "operating_expense", "debit"),
    (6210, "Software & SaaS", "expense", "operating_expense", "debit"),
    (6300, "Rent & Facilities", "expense", "operating_expense", "debit"),
    (6400, "Professional Fees", "expense", "operating_expense", "debit"),
    (6500, "Taxes & Licenses", "expense", "operating_expense", "debit"),
    (6900, "Insurance & Other G&A", "expense", "operating_expense", "debit"),
]

MARKETING_VENDORS = {  # channel -> (vendor, GL account)
    "paid_search": ("Google Ads", 6100),
    "paid_social": ("Meta Ads", 6100),
    "email": ("Inbox Engine", 6100),
    "affiliate": ("ClearPath Affiliates", 6100),
    "influencer": ("Loop Creators Agency", 6100),
    "display": ("Vantage DSP", 6100),
    "referral": ("Rewardly Payouts", 6110),
}

# department -> (headcount Jan 2025, headcount Jun 2026, monthly salary per head)
PAYROLL = {
    "engineering": (22, 34, 15_500),
    "marketing": (6, 9, 12_000),
    "operations": (5, 8, 9_000),
    "customer_support": (8, 16, 5_500),
    "finance": (3, 5, 12_500),
    "g_and_a": (4, 6, 14_000),
}
BENEFITS_RATE = 0.21

VENTURE_DEBT = 10_000_000
VENTURE_RATE = 0.11
VENTURE_MIN_CASH = 7_500_000
WAREHOUSE_ADVANCE = 0.80
WAREHOUSE_RATE = 0.095
WAREHOUSE_MAX_UTIL = 0.90
SERIES_B = (dt.date(2025, 10, 15), 35_000_000)
RESTRICTED_CASH = 2_000_000
OPENING_TAX_PAYABLE = 85_000  # 2024 Delaware franchise tax accrued before Jan 2025
OPENING_APIC = 52_000_000


def cents(x):
    return int(round(x * 100))


def fmt(c):
    assert c >= 0, c
    return f"{c // 100}.{c % 100:02d}"


def month_end(y, m):
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return nxt - dt.timedelta(days=1)


def ym(d):
    return (d.year, d.month)


class Ledger:
    """Collects journals, then assigns chronological journal ids on finalize()."""

    def __init__(self):
        self.pending = []

    def post(self, date, source, description, lines, ref="", vendor=""):
        """lines: (account_id, department, product, debit_cents, credit_cents)."""
        lines = [l for l in lines if l[3] or l[4]]
        dr, cr = sum(l[3] for l in lines), sum(l[4] for l in lines)
        assert dr == cr, (description, dr, cr)
        if dr:
            self.pending.append((date, len(self.pending), source, description, lines, ref, vendor))

    def cash_by_month_end(self, account=1000):
        """Month-end balance of an account from pending journals (no opening entry)."""
        delta = defaultdict(int)
        for date, _, _, _, lines, _, _ in self.pending:
            for acct, _, _, d, c in lines:
                if acct == account:
                    delta[ym(date)] += d - c
        out, bal = {}, 0
        for y, m in MONTHS:
            bal += delta[(y, m)]
            out[(y, m)] = bal
        return out

    def finalize(self):
        rows = []
        for n, (date, _, source, desc, lines, ref, vendor) in enumerate(sorted(self.pending, key=lambda p: (p[0], p[1])), 1):
            for i, (acct, dept, product, d, c) in enumerate(lines, 1):
                rows.append(dict(journal_id=f"JE-{n:06d}", line_number=i, entry_date=date,
                                 period=f"{date:%Y-%m}", account_id=acct, department=dept,
                                 product=product, vendor=vendor, description=desc,
                                 debit_usd=fmt(d), credit_usd=fmt(c), source=source, reference_id=ref))
        return rows


def read_csv(name):
    with open(os.path.join(MKT_DIR, name), newline="") as f:
        return list(csv.DictReader(f))


def main():
    rng = random.Random(SEED)
    if not os.path.exists(os.path.join(MKT_DIR, "campaigns.csv")):
        raise SystemExit("Run scripts/generate_marketing_data.py first")
    os.makedirs(OUT_DIR, exist_ok=True)

    # ------------------------------------------------------------ marketing inputs
    campaigns = {c["campaign_id"]: c for c in read_csv("campaigns.csv")}
    spend = defaultdict(float)  # (y, m, channel) -> USD
    for r in read_csv("daily_campaign_performance.csv"):
        d = dt.date.fromisoformat(r["date"])
        spend[(d.year, d.month, campaigns[r["campaign_id"]]["channel"])] += float(r["spend_usd"])
    revenue = defaultdict(lambda: [0.0, 0.0, 0.0])  # (y, m, product) -> interchange, interest, fee
    for r in read_csv("monthly_revenue.csv"):
        d = dt.date.fromisoformat(r["month"])
        v = revenue[(d.year, d.month, r["product"])]
        v[0] += float(r["interchange_revenue_usd"])
        v[1] += float(r["interest_revenue_usd"])
        v[2] += float(r["fee_revenue_usd"])
    originations = defaultdict(float)
    new_customers = defaultdict(int)
    for r in read_csv("customers.csv"):
        d = dt.date.fromisoformat(r["signup_date"])
        new_customers[ym(d)] += 1
        if r["product"] == "personal_loan":
            originations[ym(d)] += float(r["first_funding_amount_usd"])
    cum_customers, total = {}, 0
    for k in MONTHS:
        total += new_customers[k]
        cum_customers[k] = total

    # Campaign budgets prorated by calendar day -> monthly marketing budget by GL account
    mkt_budget = defaultdict(float)
    for c in campaigns.values():
        s, e = dt.date.fromisoformat(c["start_date"]), dt.date.fromisoformat(c["end_date"])
        per_day = float(c["budget_usd"]) / ((e - s).days + 1)
        acct = MARKETING_VENDORS[c["channel"]][1]
        for y, m in MONTHS:
            lo, hi = max(s, dt.date(y, m, 1)), min(e, month_end(y, m))
            if hi >= lo:
                mkt_budget[(y, m, acct)] += per_day * ((hi - lo).days + 1)

    L = Ledger()
    ap_rows, tax_rows, budget_rows = [], [], []

    def budget(y, m, dept, acct, amount):
        budget_rows.append(dict(month=dt.date(y, m, 1), department=dept, account_id=acct,
                                budget_usd=round(amount / 100) * 100))

    # Invoices that are deliberately left unpaid at period end
    infl_months = [k for k in MONTHS if spend[(k[0], k[1], "influencer")] > 0 and k <= (2026, 4)]
    unpaid_overrides = {("Loop Creators Agency", infl_months[-1]): "disputed",
                        ("Calder & Rowe LLP", (2026, 2)): "open"}

    def add_invoice(vendor, account, dept, inv_date, amount_c, desc, product="",
                    pay_days=None, terms=30, vinv=None):
        ap_id = f"AP-{len(ap_rows) + 1:05d}"
        due = inv_date + dt.timedelta(days=terms)
        if pay_days is None:
            pay_days = max(0, terms + rng.randint(-8, 4))
        status = unpaid_overrides.get((vendor, ym(inv_date)))
        paid = inv_date + dt.timedelta(days=pay_days)
        if status or paid > END:
            paid, status = None, status or "open"
        else:
            status = "paid"
        code = "".join(w[0] for w in vendor.split()).upper()
        vinv = vinv or f"{code}-{inv_date:%Y%m}-{rng.randint(1000, 9999)}"
        ap_rows.append(dict(invoice_id=ap_id, vendor=vendor, vendor_invoice_number=vinv,
                            gl_account_id=account, department=dept, invoice_date=inv_date,
                            due_date=due, amount_usd=fmt(amount_c), paid_date=paid or "",
                            status=status, description=desc))
        L.post(inv_date, "ap_invoice", desc,
               [(account, dept, product, amount_c, 0), (2000, dept, "", 0, amount_c)], ap_id, vendor)
        if paid:
            L.post(paid, "ap_payment", f"Payment {vinv}",
                   [(2000, dept, "", amount_c, 0), (1000, "corporate", "", 0, amount_c)], ap_id, vendor)
        return vinv

    loans_outstanding = 0.0
    warehouse_drawn = 0.0
    peak_warehouse = 0.0
    for i, (y, m) in enumerate(MONTHS):
        me = month_end(y, m)
        mid = dt.date(y, m, 15)

        # ---------------- revenue
        rev_lines, ic_total, cash_total = [], 0, 0
        plan_mult = rng.uniform(1.04, 1.13)  # plan was optimistic
        rev_by_acct = defaultdict(int)
        for product in ("checking", "credit_card", "savings", "personal_loan"):
            ic, it, fe = (cents(v) for v in revenue[(y, m, product)])
            ic_total += ic
            cash_total += it + fe
            rev_lines += [(4000, "corporate", product, 0, ic), (4100, "corporate", product, 0, it),
                          (4200, "corporate", product, 0, fe)]
            rev_by_acct[4000] += ic
            rev_by_acct[4100] += it
            rev_by_acct[4200] += fe
        L.post(me, "revenue", f"Revenue recognition {y}-{m:02d}",
               [(1100, "corporate", "", ic_total, 0), (1000, "corporate", "", cash_total, 0)] + rev_lines)
        for acct, amt in rev_by_acct.items():
            budget(y, m, "corporate", acct, amt / 100 * plan_mult)
        if (y, m) != MONTHS[-1]:  # interchange settles on the 10th of next month
            settle = dt.date(y + (m == 12), m % 12 + 1, 10)
            L.post(settle, "settlement", f"Card network interchange settlement {y}-{m:02d}",
                   [(1000, "corporate", "", ic_total, 0), (1100, "corporate", "", 0, ic_total)])

        # ---------------- cost of revenue
        proc = ic_total * 0.22 + rev_by_acct[4200] * 0.04
        add_invoice("CoreProcess Inc", 5000, "operations", me, int(proc),
                    f"Card processing & network fees {y}-{m:02d}")
        budget(y, m, "operations", 5000, proc / 100 * 0.97)
        bank_fee = 22_000 + 0.35 * cum_customers[(y, m)]
        add_invoice("Harbor Sponsor Bank", 5100, "operations", me, cents(bank_fee),
                    f"Sponsor bank program fees {y}-{m:02d}")
        budget(y, m, "operations", 5100, bank_fee)

        # ---------------- marketing (ad platform invoices = platform-reported spend)
        for channel, (vendor, acct) in MARKETING_VENDORS.items():
            amt = cents(spend[(y, m, channel)])
            if not amt:
                continue
            desc = f"{channel.replace('_', ' ').title()} media {y}-{m:02d}"
            vinv = add_invoice(vendor, acct, "marketing", me, amt, desc)
            if (vendor, y, m) == ("Meta Ads", 2025, 10):  # same invoice re-keyed and paid twice
                add_invoice(vendor, acct, "marketing", me + dt.timedelta(days=3), amt, desc, vinv=vinv)
        for acct in (6100, 6110):
            budget(y, m, "marketing", acct, mkt_budget[(y, m, acct)])

        # ---------------- payroll (semi-monthly, cash) + March 2026 annual bonus
        raise_mult = 1.03 if y == 2026 else 1.0
        for pay_date in (mid, me):
            lines, total = [], 0
            for dept, (h0, h1, sal) in PAYROLL.items():
                hc = round(h0 + (h1 - h0) * i / (len(MONTHS) - 1))
                gross = cents(hc * sal * raise_mult / 2 * rng.uniform(0.99, 1.01))
                if (y, m) == (2026, 3) and pay_date == mid:
                    gross += cents(hc * sal * 12 * 0.08)  # annual bonus
                ben = int(gross * BENEFITS_RATE)
                lines += [(6000, dept, "", gross, 0), (6010, dept, "", ben, 0)]
                total += gross + ben
            L.post(pay_date, "payroll", f"Payroll {pay_date}", lines + [(1000, "corporate", "", 0, total)])
        for dept, (h0, h1, sal) in PAYROLL.items():
            hc = round(h0 + (h1 - h0) * i / (len(MONTHS) - 1))
            gross = hc * sal * raise_mult + (hc * sal * 12 * 0.08 * 0.8 if (y, m) == (2026, 3) else 0)
            budget(y, m, dept, 6000, gross)
            budget(y, m, dept, 6010, gross * BENEFITS_RATE)

        # ---------------- operating expenses via AP
        cloud = (38_000 + 1.10 * cum_customers[(y, m)]) * rng.uniform(0.97, 1.06)
        add_invoice("Amazon Web Services", 6200, "engineering", me, cents(cloud), f"Cloud hosting {y}-{m:02d}")
        budget(y, m, "engineering", 6200, (38_000 + 1.0 * cum_customers[(y, m)]))
        for vendor, dept, base in (("Snowpeak CRM", "customer_support", 17_500), ("Atlas Security Suite", "engineering", 9_200)):
            add_invoice(vendor, 6210, dept, dt.date(y, m, 1), cents(base * (1.05 if y == 2026 else 1)),
                        f"Subscription {y}-{m:02d}")
            budget(y, m, dept, 6210, base)
        add_invoice("Larkspur Properties", 6300, "g_and_a", dt.date(y, m, 1), cents(48_000),
                    f"Office rent {y}-{m:02d}", terms=0, pay_days=rng.randint(0, 3))
        budget(y, m, "g_and_a", 6300, 48_000)
        legal = rng.uniform(12_000, 30_000)
        add_invoice("Calder & Rowe LLP", 6400, "g_and_a", me, cents(legal), f"Legal services {y}-{m:02d}")
        prof_budget = 20_000
        if m == 3:
            audit = 180_000 if y == 2025 else 195_000
            add_invoice("Merriam Audit Partners", 6400, "finance", me, cents(audit), f"Annual audit FY{y - 1}")
            prof_budget += 175_000
        elif m in (6, 9, 12):
            add_invoice("Merriam Audit Partners", 6400, "finance", me, cents(25_000), f"Quarterly review {y}-Q{m // 3}")
            prof_budget += 25_000
        budget(y, m, "g_and_a", 6400, prof_budget)

        # Insurance: annual premium prepaid in January, amortized monthly
        if m == 1:
            add_invoice("Bastion Insurance Co", 1400, "g_and_a", dt.date(y, 1, 2), cents(150_000),
                        f"D&O / cyber / E&O annual premium {y}")
        L.post(me, "adjustment", f"Insurance amortization {y}-{m:02d}",
               [(6900, "g_and_a", "", cents(12_500), 0), (1400, "g_and_a", "", 0, cents(12_500))])
        budget(y, m, "g_and_a", 6900, 12_500)

        # ---------------- personal loans + warehouse facility
        orig = cents(originations[(y, m)])
        repay = int(loans_outstanding * 0.028)
        chargeoff = int(loans_outstanding * 0.0035) if i >= 4 else 0
        draw = int(orig * WAREHOUSE_ADVANCE)
        paydown = int(repay * WAREHOUSE_ADVANCE)
        wh_interest = int(warehouse_drawn * WAREHOUSE_RATE / 12)
        L.post(me, "loans", f"Personal loan originations {y}-{m:02d}",
               [(1200, "operations", "personal_loan", orig, 0), (1000, "corporate", "", 0, orig)])
        L.post(me, "loans", f"Personal loan principal collections {y}-{m:02d}",
               [(1000, "corporate", "", repay, 0), (1200, "operations", "personal_loan", 0, repay)])
        L.post(me, "loans", f"Provision for credit losses {y}-{m:02d}",
               [(5200, "operations", "personal_loan", int(orig * 0.045), 0), (1210, "operations", "personal_loan", 0, int(orig * 0.045))])
        L.post(me, "loans", f"Loan charge-offs {y}-{m:02d}",
               [(1210, "operations", "personal_loan", chargeoff, 0), (1200, "operations", "personal_loan", 0, chargeoff)])
        L.post(me, "financing", f"Warehouse draw {y}-{m:02d}",
               [(1000, "corporate", "", draw, 0), (2300, "corporate", "", 0, draw)], vendor="Atlas Credit Partners")
        L.post(me, "financing", f"Warehouse paydown {y}-{m:02d}",
               [(2300, "corporate", "", paydown, 0), (1000, "corporate", "", 0, paydown)], vendor="Atlas Credit Partners")
        L.post(me, "financing", f"Warehouse interest {y}-{m:02d}",
               [(5300, "corporate", "", wh_interest, 0), (1000, "corporate", "", 0, wh_interest)], vendor="Atlas Credit Partners")
        vd_interest = cents(VENTURE_DEBT * VENTURE_RATE / 12)
        L.post(me, "financing", f"Venture debt interest {y}-{m:02d}",
               [(5300, "corporate", "", vd_interest, 0), (1000, "corporate", "", 0, vd_interest)], vendor="Granite Peak Capital")
        budget(y, m, "operations", 5200, orig / 100 * 0.045)
        budget(y, m, "corporate", 5300, (wh_interest + vd_interest) / 100)
        loans_outstanding += orig - repay - chargeoff
        warehouse_drawn += draw - paydown
        peak_warehouse = max(peak_warehouse, warehouse_drawn)

    # ---------------- equity raise
    L.post(SERIES_B[0], "financing", "Series B preferred stock issuance",
           [(1000, "corporate", "", cents(SERIES_B[1]), 0), (3000, "corporate", "", 0, cents(SERIES_B[1]))])

    # ---------------- taxes
    def add_tax(tax_type, juris, period, due, amount, accrue_on, paid=None, penalty=0.0, status=None):
        oid = f"TAX-{len(tax_rows) + 1:04d}"
        amt = cents(amount)
        if accrue_on:
            L.post(accrue_on, "tax", f"Accrue {tax_type} {juris} {period}",
                   [(6500, "finance", "", amt, 0), (2200, "finance", "", 0, amt)], oid)
        if paid:
            L.post(paid, "tax", f"Pay {tax_type} {juris} {period}",
                   [(2200, "finance", "", amt, 0), (1000, "corporate", "", 0, amt)], oid)
        if penalty:
            L.post(paid, "tax", f"Late payment penalty {tax_type} {juris} {period}",
                   [(6500, "finance", "", cents(penalty), 0), (1000, "corporate", "", 0, cents(penalty))], oid)
        status = status or ("paid_late" if paid and paid > due else "paid" if paid else "open")
        tax_rows.append(dict(obligation_id=oid, tax_type=tax_type, jurisdiction=juris, tax_period=period,
                             due_date=due, amount_usd=fmt(amt), paid_date=paid or "",
                             penalty_usd=fmt(cents(penalty)), status=status,
                             accrued_in_gl="false" if status == "upcoming" else "true"))

    add_tax("state_franchise", "DE", "2024", dt.date(2025, 3, 1), OPENING_TAX_PAYABLE, None, dt.date(2025, 2, 26))
    add_tax("state_franchise", "CA", "2025", dt.date(2025, 4, 15), 800, dt.date(2025, 4, 10), dt.date(2025, 4, 10))
    add_tax("state_franchise", "DE", "2025", dt.date(2026, 3, 1), 92_500, dt.date(2025, 12, 31), dt.date(2026, 2, 27))
    add_tax("state_franchise", "CA", "2026", dt.date(2026, 4, 15), 800, dt.date(2026, 4, 14), dt.date(2026, 4, 14))
    add_tax("state_franchise", "DE", "2026", dt.date(2027, 3, 1), 98_000, None, None, status="upcoming")
    for q_end in [month_end(y, m) for y, m in MONTHS if m in (3, 6, 9, 12)]:
        due = month_end(q_end.year + (q_end.month == 12), q_end.month % 12 + 1)
        period = f"{q_end.year}-Q{q_end.month // 3}"
        for juris, base in (("CA", 5_200), ("NY", 2_900), ("TX", 2_100)):
            amount = round(base * rng.uniform(0.85, 1.25) * (1.15 if q_end.year == 2026 else 1), 2)
            if due > END:
                add_tax("sales_use", juris, period, due, amount, q_end)
            elif (juris, period) == ("CA", "2025-Q3"):
                add_tax("sales_use", juris, period, due, amount, q_end, due + dt.timedelta(days=12),
                        penalty=round(amount * 0.10, 2))
            else:
                add_tax("sales_use", juris, period, due, amount, q_end,
                        due - dt.timedelta(days=rng.randint(1, 6)))

    # ---------------- opening balances: pick opening cash so the venture-debt covenant
    # (min unrestricted cash) is breached by ~$450K at the low point before the Series B.
    cash_path = L.cash_by_month_end(1000)
    low = min(cash_path[k] for k in [(2025, 8), (2025, 9)])
    opening_cash = math.ceil((cents(VENTURE_MIN_CASH - 450_000) - low) / cents(100_000)) * cents(100_000)
    credits = cents(VENTURE_DEBT) + cents(OPENING_TAX_PAYABLE) + cents(OPENING_APIC)
    debits = opening_cash + cents(RESTRICTED_CASH)
    L.post(START, "opening", "Opening balances 2025-01-01",
           [(1000, "corporate", "", opening_cash, 0), (1010, "corporate", "", cents(RESTRICTED_CASH), 0),
            (3100, "corporate", "", credits - debits, 0),
            (2400, "corporate", "", 0, cents(VENTURE_DEBT)), (2200, "finance", "", 0, cents(OPENING_TAX_PAYABLE)),
            (3000, "corporate", "", 0, cents(OPENING_APIC))], vendor="")
    L.pending.sort(key=lambda p: (p[0], p[2] != "opening", p[1]))
    L.pending = [(p[0], i, *p[2:]) for i, p in enumerate(L.pending)]
    gl = L.finalize()

    commitment = math.ceil(peak_warehouse / 100 / 0.88 / 1_000_000) * 1_000_000
    debt = [
        dict(facility_id="VD-001", lender="Granite Peak Capital", facility_type="venture_debt",
             gl_account_id=2400, commitment_usd=VENTURE_DEBT, interest_rate_pct=VENTURE_RATE * 100,
             advance_rate_pct="", origination_date="2024-01-31", maturity_date="2028-01-31",
             min_unrestricted_cash_usd=VENTURE_MIN_CASH, max_utilization_pct=""),
        dict(facility_id="WH-001", lender="Atlas Credit Partners", facility_type="warehouse_line",
             gl_account_id=2300, commitment_usd=commitment, interest_rate_pct=WAREHOUSE_RATE * 100,
             advance_rate_pct=WAREHOUSE_ADVANCE * 100, origination_date="2024-11-15",
             maturity_date="2027-06-30", min_unrestricted_cash_usd=5_000_000,
             max_utilization_pct=WAREHOUSE_MAX_UTIL * 100),
    ]
    coa = [dict(account_id=a, account_name=n, account_type=t, account_subtype=s, normal_balance=nb)
           for a, n, t, s, nb in ACCOUNTS]

    files = [
        ("chart_of_accounts.csv", coa, "GL accounts with type (asset/liability/equity/revenue/expense)."),
        ("general_ledger.csv", gl, "Journal lines (debit/credit) for every transaction, Jan 2025 - Jun 2026."),
        ("ap_invoices.csv", ap_rows, "Vendor invoices with due/paid dates and status (aging source)."),
        ("tax_obligations.csv", tax_rows, "Tax filings: due dates, amounts, payment status, penalties."),
        ("debt_facilities.csv", debt, "Credit facilities with rates, maturities and covenants."),
        ("budget.csv", budget_rows, "Monthly plan by department x GL account (budget vs actual)."),
    ]
    print(f"Wrote to {OUT_DIR} (seed={SEED})")
    for name, rows, desc in files:
        with open(os.path.join(OUT_DIR, name), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow({k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in r.items()})
        print(f"  {name:24} {len(rows):>6,} rows  {desc}")


if __name__ == "__main__":
    main()
