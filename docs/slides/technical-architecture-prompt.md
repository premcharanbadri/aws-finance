# Slide prompt: Technical architecture

Paste everything below the line into a slide-generation tool.

---

Create a 7-slide technical architecture deck for a hackathon demo. The audience is engineers and technical judges. Style: clean, dark-on-light, one idea per slide, diagrams over text, at most 5 bullets per slide, monospace font for table and command names. Include speaker notes for each slide (2-4 sentences).

Project: **aws-finance**, a virtual advisory firm where industry-specific AI advisors (CPA, Marketing) answer business questions by writing and running SQL against a governed AWS data warehouse. All data is synthetic, for a fictional consumer fintech (mobile banking and credit card app).

## Slide 1 - Title
"aws-finance: Industry AI advisors on a governed AWS data layer." Subtitle: Strands Agents + Amazon Bedrock + Redshift Serverless + dbt.

## Slide 2 - End-to-end architecture (diagram)
Draw a left-to-right flow diagram:

1. **Synthetic data generators** (Python, standard library, fixed seed; marketing and accounting datasets)
2. **Amazon S3** `aws-finance-hackathon-bucket` (`raw/marketing/`, `raw/accounting/`)
3. **Redshift COPY** via the Redshift Data API, using an IAM role attached to the namespace -> `raw_marketing.*`, `raw_accounting.*` (all VARCHAR)
4. **dbt** -> `staging` (typed views) -> marts: `marketing.*`, `accounting.*` (tables, tested, documented)
5. **Strands agents** (CPA, Marketing) with shared tools `get_schema` and `run_sql`, connecting as the read-only `advisor_ro` user through the Redshift Data API
6. **Amazon Bedrock** model (`us.openai.gpt-5.6-luna` inference profile) for reasoning and SQL generation
7. **Streamlit UI**: pick an advisor, chat, see the SQL and result rows behind every answer

Show Secrets Manager beside Redshift (admin and advisor credentials). Label everything "us-east-1".

## Slide 3 - Infrastructure as code (AWS CDK, Python)
One CDK stack, `aws-finance-hackathon`:
- Redshift Serverless namespace and workgroup, 4 RPU base capacity (the minimum), default VPC, publicly accessible
- S3 bucket, auto-deleted with its contents on teardown
- IAM role granting Redshift read access to the bucket
- Two Secrets Manager secrets with generated passwords: admin (for loading and dbt) and `advisor_ro` (for agents)
- Every resource tagged `resource-owner=tyler-revering`
- Deploy in about 2 minutes; `make destroy` removes everything

## Slide 4 - Data modeling with dbt
- 3 layers: raw (VARCHAR from CSV) -> staging (10 typed views) -> marts (15 tables)
- Marketing marts: `dim_campaigns`, `fct_daily_campaign_performance`, `dim_customers`, `channel_monthly_performance`, `monthly_marketing_summary`
- Accounting marts: `fct_gl_entries`, `monthly_account_balances`, `income_statement_monthly`, `balance_sheet_monthly`, `cash_flow_monthly`, `budget_vs_actual`, `fct_ap_invoices`, `tax_calendar`, `debt_covenants_monthly`, `liquidity_snapshot`
- 33 data tests, including custom accounting tests: every journal balances, and assets = liabilities + equity every month
- `persist_docs` writes model descriptions into Redshift so the agents can read them
- dbt grants SELECT on the marts to `advisor_ro` on every run
- Result: `dbt build` passes 59/59

## Slide 5 - Agent design (one toolset, many personas)
Diagram: a single `make_tools(allowed_schemas)` module feeding two agents.
- `get_schema`: lists allowed tables and columns, including dbt descriptions
- `run_sql`: a single read-only SELECT/WITH, schema allowlist, 200-row cap; database errors are returned to the model so it can fix its SQL and retry
- A persona = system prompt (role, metric definitions, what to check) + allowed schemas
  - **CPA**: reads `accounting` and `marketing`; ends every answer with "Questions for a human CPA"
  - **Marketing**: reads `marketing` only
- Adding a new advisor is one dictionary entry
- Every query is logged and shown in the UI for transparency

## Slide 6 - Security and governance (defense in depth)
Layered diagram, outside -> inside:
1. Application checks: SELECT-only, single statement, schema allowlist
2. Database permissions: `advisor_ro` has SELECT on `marketing` and `accounting` only. Verified: it is denied on `raw_*`, `staging`, CREATE and DELETE
3. 60-second statement timeout on the agent user
4. Secrets in Secrets Manager; nothing secret in git
5. Hackathon trade-offs, stated honestly: public endpoint, port 5439 open, default VPC. Production next steps: private VPC, an IP allowlist, IAM-federated database users, Bedrock Guardrails

## Slide 7 - Reproducibility and demo
- One command rebuilds everything: `make demo-data` (generate -> S3 -> raw -> dbt -> grants)
- Deterministic data: fixed seeds, byte-identical CSVs on every run
- Commands: `make deploy`, `make demo-data`, `make advisors`, `make destroy`
- Example answer from the live system: the CPA agent found $11.7M of illustrative investable cash, 20.6 months of runway, and a September 2025 minimum-cash covenant breach of $394K, using 5 SQL queries in about 14 seconds
- Pull-safe repo layout: self-contained folders (`infra/`, `dbt/`, `advisors/`, `scripts/`) that don't modify teammates' files
