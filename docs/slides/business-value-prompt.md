# Slide prompt: Business need and value

Paste everything below the line into a slide-generation tool.

---

Create a 7-slide business deck for a hackathon pitch. The audience is business judges, finance leaders and marketing leaders, not engineers. Style: confident and clear, large numbers, minimal jargon (no table or code names), one key message per slide, at most 4 bullets per slide, simple icons. Include speaker notes for each slide (2-4 sentences).

Product: **aws-finance**, a virtual advisory firm. Specialized AI advisors (an accountant and a marketing strategist, with more to come) answer business questions in plain English, using the company's own financial and marketing data, and show their work. The demo company is a fictional consumer fintech (mobile banking and credit card app) with 18 months of synthetic data.

## Slide 1 - Title
"Ask your books. Ask your funnel. Get an answer in seconds." Subtitle: AI advisors that turn company data into decisions.

## Slide 2 - The problem
- Leaders' questions ("How much cash can we safely invest?", "Which channels waste money?") take days: a ticket to the data team, spreadsheets, back-and-forth
- The answers live in silos: the ledger, ad platforms, invoices, budgets
- Costly problems hide in plain sight until month-end close or an audit
- Generic AI chatbots can't be trusted: they don't see your data and they make up numbers

## Slide 3 - The solution
- Pick an advisor, ask a question in plain English
- The advisor looks up the company's real data, calculates the answer and explains it in its own professional language
- Every answer shows the exact queries and numbers behind it: transparent and auditable
- Each advisor sees only the data it needs and can only read it, never change it

## Slide 4 - Meet the advisors
Two cards side by side.
- **CPA advisor**: liquidity and runway, investable cash, debt covenants, budget vs. actual, payables issues, tax deadlines. Always ends with "Questions for a human CPA", so it supports professionals rather than replacing them.
- **Marketing advisor**: cost per funded account by channel, wasted spend, anomaly detection, budget reallocation with expected impact.

Footer: "New advisors (investment analyst, risk reviewer) plug into the same platform."

## Slide 5 - What it found in the demo (proof)
Present as large callout numbers:
- **$11.7M** of cash that could be invested after keeping a 6-month reserve, the lender's minimum and upcoming bills. **20.6 months** of runway.
- **A loan covenant breach in September 2025**: cash fell $394K below the lender's minimum before the Series B fixed it. This is exactly what a lender or auditor would ask about.
- **A $37K invoice paid twice** to an ad platform.
- **22x difference in acquisition cost**: about $14 per funded customer via email vs. about $304 via display ads.
- Caught automatically: a campaign that overspent its budget by 38%, suspected bot traffic, and a tracking outage.

Speaker note: each of these was found by asking a simple question, in seconds, with the supporting numbers shown.

## Slide 6 - Business value
- **Speed**: days of analyst work reduced to seconds
- **Money**: catches duplicate payments, overspend and covenant risk before they become costly
- **Better decisions**: shows where marketing dollars work best, with quantified "what if" reallocations
- **Trust**: transparent, read-only, governed; humans stay in the loop for judgment calls

## Slide 7 - Why it's credible and what's next
- Built on enterprise AWS services: a cloud data warehouse, managed AI models, tested data models that tie out (the ledger balances, revenue reconciles)
- Low cost: serverless, pay only when used, torn down with one command
- Next: more advisors (investment analyst, challenger, relationship manager who combines them into one recommendation), connecting real systems (accounting software, ad platforms), and alerts that push findings proactively
- Closing line: "Every company has the data. Now every leader gets an advisor."

Disclaimer in small text on the last slide: demo uses synthetic data; analysis only, not tax, legal or investment advice.
