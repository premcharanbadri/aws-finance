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

TABLES  := campaigns daily_campaign_performance customers monthly_revenue

.PHONY: venv bootstrap synth deploy outputs data demo-data destroy

venv: $(VENV)/.installed
$(VENV)/.installed: infra/requirements.txt
	python3.11 -m venv $(VENV)
	$(VENV)/bin/pip install -q -r infra/requirements.txt
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

data:
	python3 scripts/generate_marketing_data.py

# Re-runnable: regenerates (deterministic), re-uploads, and drop+reloads each table.
demo-data: venv data
	$(eval BUCKET := $(shell aws cloudformation describe-stacks --stack-name $(STACK) \
	  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text))
	aws s3 sync data/marketing/ s3://$(BUCKET)/raw/marketing/ --exclude '*' --include '*.csv'
	@set -e; for t in $(TABLES); do \
	  $(PY) scripts/load_csv.py raw/marketing/$$t.csv $$t --replace; \
	done

destroy: venv
	$(CDK) destroy --force
