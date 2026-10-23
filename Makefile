# aws-finance hackathon data layer
REGION  ?= us-east-1
STACK   := aws-finance-hackathon
VENV    := .venv
PY      := $(VENV)/bin/python
export AWS_REGION := $(REGION)
export AWS_DEFAULT_REGION := $(REGION)
export CDK_DEFAULT_ACCOUNT ?= $(shell aws sts get-caller-identity --query Account --output text 2>/dev/null)
CDK     := cd infra && PATH=$(CURDIR)/$(VENV)/bin:$$PATH cdk
TAGS    := --tags resource-owner=tyler-revering --tags project=aws-finance-hackathon

MKT_TABLES  := campaigns daily_campaign_performance customers monthly_revenue
ACCT_TABLES := chart_of_accounts general_ledger ap_invoices tax_obligations debt_facilities budget

# dbt connects over TCP with the admin login (pulled from Secrets Manager at run time).
stack_out = $(shell aws cloudformation describe-stacks --stack-name $(STACK) \
  --query "Stacks[0].Outputs[?OutputKey=='$(1)'].OutputValue" --output text)
DBT_ENV = REDSHIFT_HOST=$(call stack_out,WorkgroupEndpoint) \
  REDSHIFT_PASSWORD="$$(aws secretsmanager get-secret-value --secret-id $(call stack_out,AdminSecretArn) \
    --query SecretString --output text | $(CURDIR)/$(PY) -c 'import json,sys;print(json.load(sys.stdin)["password"])')"
DBT = @cd dbt && $(DBT_ENV) $(CURDIR)/$(VENV)/bin/dbt

.PHONY: venv bootstrap synth deploy outputs data demo-data load-raw advisor-user \
        dbt-build dbt-docs advisors destroy

venv: $(VENV)/.installed
$(VENV)/.installed: infra/requirements.txt advisors/requirements.txt
	python3.11 -m venv $(VENV)
	$(VENV)/bin/pip install -q -r infra/requirements.txt -r advisors/requirements.txt
	touch $@

bootstrap: venv
	$(CDK) bootstrap aws://$(CDK_DEFAULT_ACCOUNT)/$(REGION) $(TAGS)

synth: venv
	$(CDK) synth --quiet

deploy: venv
	$(CDK) deploy --require-approval never

outputs:
	@aws cloudformation describe-stacks --stack-name $(STACK) \
	  --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output table

# Deterministic synthetic data (accounting reads the marketing files).
data:
	python3 scripts/generate_marketing_data.py
	python3 scripts/generate_accounting_data.py

# Upload CSVs and (re)load raw_marketing.* / raw_accounting.* (all VARCHAR; dbt types them).
load-raw: venv
	aws s3 sync data/marketing/  s3://$(call stack_out,BucketName)/raw/marketing/  --exclude '*' --include '*.csv'
	aws s3 sync data/accounting/ s3://$(call stack_out,BucketName)/raw/accounting/ --exclude '*' --include '*.csv'
	@set -e; for t in $(MKT_TABLES); do \
	  $(PY) scripts/load_csv.py raw/marketing/$$t.csv raw_marketing.$$t --replace --all-varchar; done
	@set -e; for t in $(ACCT_TABLES); do \
	  $(PY) scripts/load_csv.py raw/accounting/$$t.csv raw_accounting.$$t --replace --all-varchar; done

# Read-only DB user for the agents (SELECT on marketing.* and accounting.* only).
advisor-user: venv
	$(PY) scripts/setup_advisor_user.py

# Staging views + marketing/accounting marts + tests.
dbt-build: venv
	$(DBT) build --profiles-dir . --fail-fast

dbt-docs: venv
	$(DBT) docs generate --profiles-dir . && $(DBT) docs serve --profiles-dir . --port 8081

# Full re-runnable pipeline: generate -> S3 -> raw schemas -> dbt marts -> grants.
demo-data: data load-raw advisor-user dbt-build

# Streamlit advisor chat (CPA / Marketing) on http://localhost:8501
advisors: venv
	ADVISOR_SECRET_ARN=$(call stack_out,AdvisorSecretArn) $(VENV)/bin/streamlit run advisors/ui.py

destroy: venv
	$(CDK) destroy --force
