#!/usr/bin/env python3
"""CDK app for the aws-finance hackathon data layer (S3 + Redshift Serverless)."""
import os

import aws_cdk as cdk

from stack import AwsFinanceHackathonStack

app = cdk.App()

account = os.environ.get("CDK_DEFAULT_ACCOUNT")
region = app.node.try_get_context("region") or "us-east-1"

stack = AwsFinanceHackathonStack(
    app,
    "aws-finance-hackathon",
    env=cdk.Environment(account=account, region=region),
    description="aws-finance hackathon: S3 raw bucket + Redshift Serverless",
)

# Tag every taggable resource in the stack (and the stack itself).
cdk.Tags.of(app).add("resource-owner", "tyler-revering")
cdk.Tags.of(app).add("project", "aws-finance-hackathon")

app.synth()
