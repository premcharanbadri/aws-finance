#!/usr/bin/env python3
"""Load a CSV from S3 into Redshift Serverless via the Redshift Data API (no driver).

  python scripts/load_csv.py raw/marketing/campaigns.csv campaigns [--replace]

- Reads the CSV from S3, infers column names from the header and types from the
  values (BIGINT / DECIMAL / DATE / TIMESTAMP / VARCHAR), and creates the table if it
  doesn't exist. Use --all-varchar to make every column VARCHAR instead.
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


def read_sample(s3, bucket, key, max_bytes=50 * 1024 * 1024):
    """Return (columns, rows) from the CSV. Reads the whole file up to max_bytes
    (our files are a few MB, so type inference sees every value)."""
    obj = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{max_bytes - 1}")
    text = obj["Body"].read().decode("utf-8-sig")
    truncated = obj.get("ContentLength", 0) >= max_bytes
    if truncated:  # drop a possibly partial last line
        text = text[: text.rfind("\n")]
    reader = csv.reader(io.StringIO(text))
    cols = [to_column(c) for c in next(reader)]
    seen = {}
    for i, c in enumerate(cols):  # de-duplicate
        if c in seen:
            seen[c] += 1
            cols[i] = f"{c}_{seen[c]}"
        else:
            seen[c] = 0
    return cols, list(reader), truncated


INT_RE = re.compile(r"^-?\d{1,18}$")
DEC_RE = re.compile(r"^-?\d{1,18}\.(\d{1,10})$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?$")


def infer_type(values):
    """Pick the narrowest Redshift type that fits every non-empty value."""
    vals = [v.strip() for v in values if v is not None and v.strip() != ""]
    if not vals:
        return "VARCHAR(256)"
    if all(INT_RE.match(v) for v in vals):
        return "BIGINT"
    if all(INT_RE.match(v) or DEC_RE.match(v) for v in vals):
        scale = max((len(m.group(1)) for v in vals if (m := DEC_RE.match(v))), default=0)
        scale = max(2, scale)
        return f"DECIMAL({min(38, 18 + scale)},{scale})"
    if all(DATE_RE.match(v) for v in vals):
        return "DATE"
    if all(TS_RE.match(v) for v in vals):
        return "TIMESTAMP"
    width = max(len(v.encode()) for v in vals)
    return f"VARCHAR({min(65535, max(256, width * 2))})"  # headroom for later loads


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
            raise RuntimeError(f"{d['Status']}: {d.get('Error')}\nSQL: {failed.get('QueryString', '')[:500]}\n"
                               "For COPY errors run: SELECT * FROM sys_load_error_detail "
                               "ORDER BY start_time DESC LIMIT 10;")
        time.sleep(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("key", help="S3 key in the bucket, e.g. raw/marketing/campaigns.csv")
    ap.add_argument("table", help="target table name, e.g. campaigns or schema.table")
    ap.add_argument("--replace", action="store_true",
                    help="drop and recreate the table first (makes re-runs idempotent)")
    ap.add_argument("--all-varchar", action="store_true",
                    help="skip type inference and create every column as VARCHAR")
    args = ap.parse_args()

    table = args.table.lower()
    if not all(IDENT.match(p) for p in table.split(".")) or table.count(".") > 1:
        sys.exit(f"invalid table name: {args.table}")

    cfg = load_config()
    s3 = boto3.client("s3", region_name=cfg["REGION"])
    rsd = boto3.client("redshift-data", region_name=cfg["REGION"])

    cols, rows, truncated = read_sample(s3, cfg["BUCKET"], args.key)
    if args.all_varchar:
        types = ["VARCHAR(1024)"] * len(cols)
    else:
        types = [infer_type([r[i] if i < len(r) else "" for r in rows]) for i in range(len(cols))]
        if truncated:
            print("  note: file > 50 MB, types inferred from the first 50 MB", flush=True)
    col_ddl = ",\n  ".join(f'"{c}" {t}' for c, t in zip(cols, types))
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
