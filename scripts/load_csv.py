#!/usr/bin/env python3
"""Load a CSV from S3 into Redshift Serverless via the Redshift Data API (no driver).

  python scripts/load_csv.py raw/marketing/campaigns.csv campaigns [--replace]

- Reads the CSV header from S3 and creates the table (all VARCHAR) if it doesn't exist.
- COPY ... CSV IGNOREHEADER 1 using the IAM role attached to the namespace.
- Prints the row count when done.

Connection settings come from the CloudFormation outputs of the
`aws-finance-hackathon` stack; override with env vars BUCKET, WORKGROUP, DATABASE,
SECRET_ARN, REDSHIFT_ROLE_ARN, AWS_REGION.
"""
import argparse
import csv
import io
import os
import re
import sys
import time

import boto3

STACK = "aws-finance-hackathon"
IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,126}$")


def stack_outputs(region):
    cfn = boto3.client("cloudformation", region_name=region)
    stack = cfn.describe_stacks(StackName=STACK)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def load_config():
    region = os.environ.get("AWS_REGION", "us-east-1")
    keys = {"BUCKET": "BucketName", "WORKGROUP": "WorkgroupName", "DATABASE": "Database",
            "SECRET_ARN": "AdminSecretArn", "REDSHIFT_ROLE_ARN": "RedshiftRoleArn"}
    cfg = {k: os.environ.get(k) for k in keys}
    if not all(cfg.values()):
        outs = stack_outputs(region)
        for env_key, out_key in keys.items():
            cfg[env_key] = cfg[env_key] or outs[out_key]
    cfg["REGION"] = region
    return cfg


def to_column(name):
    """Normalize a header cell into a safe Redshift identifier."""
    col = re.sub(r"[^a-z0-9_]", "_", name.strip().lower()).strip("_") or "col"
    if col[0].isdigit():
        col = "c_" + col
    return col[:127]


def read_header(s3, bucket, key):
    # Range GET: only the first 64 KB is needed for the header line.
    body = s3.get_object(Bucket=bucket, Key=key, Range="bytes=0-65535")["Body"].read()
    first_line = body.decode("utf-8-sig").splitlines()[0]
    cols = [to_column(c) for c in next(csv.reader(io.StringIO(first_line)))]
    seen = {}
    for i, c in enumerate(cols):  # de-duplicate
        if c in seen:
            seen[c] += 1
            cols[i] = f"{c}_{seen[c]}"
        else:
            seen[c] = 0
    return cols


def run(client, cfg, sqls):
    """Run statements as one transaction (batch) and wait. Returns the statement id."""
    resp = client.batch_execute_statement(
        WorkgroupName=cfg["WORKGROUP"], Database=cfg["DATABASE"],
        SecretArn=cfg["SECRET_ARN"], Sqls=sqls,
    )
    sid = resp["Id"]
    while True:
        d = client.describe_statement(Id=sid)
        if d["Status"] == "FINISHED":
            return sid
        if d["Status"] in ("FAILED", "ABORTED"):
            failed = next((s for s in d.get("SubStatements", []) if s.get("Error")), {})
            raise RuntimeError(f"{d['Status']}: {d.get('Error')}\nSQL: {failed.get('QueryString', '')[:500]}")
        time.sleep(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("key", help="S3 key in the bucket, e.g. raw/marketing/campaigns.csv")
    ap.add_argument("table", help="target table name, e.g. campaigns or schema.table")
    ap.add_argument("--replace", action="store_true",
                    help="drop and recreate the table first (makes re-runs idempotent)")
    args = ap.parse_args()

    table = args.table.lower()
    if not all(IDENT.match(p) for p in table.split(".")) or table.count(".") > 1:
        sys.exit(f"invalid table name: {args.table}")

    cfg = load_config()
    s3 = boto3.client("s3", region_name=cfg["REGION"])
    rsd = boto3.client("redshift-data", region_name=cfg["REGION"])

    cols = read_header(s3, cfg["BUCKET"], args.key)
    col_ddl = ",\n  ".join(f'"{c}" VARCHAR(1024)' for c in cols)
    s3_uri = f"s3://{cfg['BUCKET']}/{args.key}".replace("'", "''")
    role = cfg["REDSHIFT_ROLE_ARN"].replace("'", "''")

    sqls = []
    if "." in table:
        sqls.append(f"CREATE SCHEMA IF NOT EXISTS {table.split('.')[0]}")
    if args.replace:
        sqls.append(f"DROP TABLE IF EXISTS {table}")
    sqls.append(f"CREATE TABLE IF NOT EXISTS {table} (\n  {col_ddl}\n)")
    sqls.append(
        f"COPY {table} ({', '.join(chr(34) + c + chr(34) for c in cols)})\n"
        f"FROM '{s3_uri}'\nIAM_ROLE '{role}'\n"
        f"CSV IGNOREHEADER 1 EMPTYASNULL BLANKSASNULL REGION '{cfg['REGION']}'"
    )

    print(f"Loading s3://{cfg['BUCKET']}/{args.key} -> {table} ({len(cols)} columns) ...", flush=True)
    t0 = time.time()
    run(rsd, cfg, sqls)

    # Row count
    resp = rsd.execute_statement(WorkgroupName=cfg["WORKGROUP"], Database=cfg["DATABASE"],
                                 SecretArn=cfg["SECRET_ARN"], Sql=f"SELECT COUNT(*) FROM {table}")
    while (d := rsd.describe_statement(Id=resp["Id"]))["Status"] not in ("FINISHED", "FAILED", "ABORTED"):
        time.sleep(0.5)
    if d["Status"] != "FINISHED":
        sys.exit(f"count failed: {d.get('Error')}")
    count = rsd.get_statement_result(Id=resp["Id"])["Records"][0][0]["longValue"]
    print(f"  {table}: {count:,} rows ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
