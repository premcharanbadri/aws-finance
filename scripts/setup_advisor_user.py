#!/usr/bin/env python3
"""Create/refresh the read-only database user the advisor agents connect as.

- Password comes from the aws-finance-hackathon-redshift-advisor secret (CDK-generated).
- The user can read ONLY the dbt mart schemas (marketing, accounting); raw_* and
  staging stay private. Default privileges cover tables dbt creates later; dbt's
  +grants config re-grants on every run as well.
- Idempotent: safe to re-run.
"""
import json
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load_csv import load_config, run  # noqa: E402

MART_SCHEMAS = ["marketing", "accounting"]


def main():
    cfg = load_config()
    region = cfg["REGION"]
    outs = boto3.client("cloudformation", region_name=region).describe_stacks(
        StackName="aws-finance-hackathon")["Stacks"][0]["Outputs"]
    advisor_arn = os.environ.get("ADVISOR_SECRET_ARN") or next(
        o["OutputValue"] for o in outs if o["OutputKey"] == "AdvisorSecretArn")
    secret = json.loads(boto3.client("secretsmanager", region_name=region)
                        .get_secret_value(SecretId=advisor_arn)["SecretString"])
    user, pwd = secret["username"], secret["password"].replace("'", "''")
    rsd = boto3.client("redshift-data", region_name=region)

    exists_sql = f"SELECT 1 FROM pg_user WHERE usename = '{user}'"
    sid = rsd.execute_statement(WorkgroupName=cfg["WORKGROUP"], Database=cfg["DATABASE"],
                                SecretArn=cfg["SECRET_ARN"], Sql=exists_sql)["Id"]
    while (d := rsd.describe_statement(Id=sid))["Status"] not in ("FINISHED", "FAILED", "ABORTED"):
        time.sleep(0.5)
    exists = d.get("ResultRows", 0) > 0

    sqls = [f"ALTER USER {user} PASSWORD '{pwd}'" if exists
            else f"CREATE USER {user} PASSWORD '{pwd}'"]
    for s in MART_SCHEMAS:
        sqls += [
            f"CREATE SCHEMA IF NOT EXISTS {s}",
            f"GRANT USAGE ON SCHEMA {s} TO {user}",
            f"GRANT SELECT ON ALL TABLES IN SCHEMA {s} TO {user}",
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {s} GRANT SELECT ON TABLES TO {user}",
        ]
    # Short query timeout so a runaway agent query can't hog the workgroup.
    sqls.append(f"ALTER USER {user} SET statement_timeout TO 60000")
    try:
        run(rsd, cfg, sqls)
    except RuntimeError as e:  # don't echo the SQL: it contains the password
        sys.exit(str(e).split("\nSQL:")[0])
    print(f"{'Updated' if exists else 'Created'} read-only user {user}: SELECT on {', '.join(MART_SCHEMAS)}")


if __name__ == "__main__":
    main()
