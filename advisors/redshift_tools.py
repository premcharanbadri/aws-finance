"""Shared Redshift tools for the advisor agents (text-to-SQL via the Redshift Data API).

Auth: the read-only `advisor_ro` database user, whose password lives in the
aws-finance-hackathon-redshift-advisor secret. That user can only SELECT from the dbt
mart schemas (marketing, accounting); the database enforces read-only, the checks
here just give the model faster, clearer feedback.

Config (env vars, fall back to the CloudFormation stack outputs):
  ADVISOR_SECRET_ARN, REDSHIFT_WORKGROUP, REDSHIFT_DATABASE, AWS_REGION
"""
import os
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache

import boto3
from strands import tool

STACK = "aws-finance-hackathon"
MAX_ROWS = 200
READ_ONLY = re.compile(r"^\s*(select|with)\b", re.I | re.S)
SCHEMA_REF = re.compile(r"\b([a-z_][a-z0-9_]*)\s*\.\s*[a-z_\"]", re.I)


@lru_cache(maxsize=1)
def _config() -> dict:
    region = os.environ.get("AWS_REGION", "us-east-1")
    cfg = {
        "region": region,
        "secret_arn": os.environ.get("ADVISOR_SECRET_ARN"),
        "workgroup": os.environ.get("REDSHIFT_WORKGROUP"),
        "database": os.environ.get("REDSHIFT_DATABASE"),
    }
    if not all(cfg.values()):
        outs = boto3.client("cloudformation", region_name=region).describe_stacks(
            StackName=STACK)["Stacks"][0]["Outputs"]
        outs = {o["OutputKey"]: o["OutputValue"] for o in outs}
        cfg["secret_arn"] = cfg["secret_arn"] or outs["AdvisorSecretArn"]
        cfg["workgroup"] = cfg["workgroup"] or outs["WorkgroupName"]
        cfg["database"] = cfg["database"] or outs["Database"]
    return cfg


def execute(sql: str, max_rows: int = MAX_ROWS) -> tuple[list[str], list[list], int]:
    """Run one statement as advisor_ro. Returns (columns, rows[:max_rows], total_rows)."""
    cfg = _config()
    client = boto3.client("redshift-data", region_name=cfg["region"])
    sid = client.execute_statement(WorkgroupName=cfg["workgroup"], Database=cfg["database"],
                                   SecretArn=cfg["secret_arn"], Sql=sql)["Id"]
    delay = 0.25
    while (d := client.describe_statement(Id=sid))["Status"] not in ("FINISHED", "FAILED", "ABORTED"):
        time.sleep(delay)
        delay = min(delay * 1.5, 2.0)
    if d["Status"] != "FINISHED":
        raise RuntimeError(d.get("Error", d["Status"]))
    if not d.get("HasResultSet"):
        return [], [], 0
    res = client.get_statement_result(Id=sid)
    cols = [c["name"] for c in res["ColumnMetadata"]]
    rows = [[None if v.get("isNull") else next(iter(v.values())) for v in rec]
            for rec in res["Records"][:max_rows]]
    return cols, rows, d.get("ResultRows", len(res["Records"]))


def to_text(cols, rows, total) -> str:
    """Compact pipe-delimited table: cheap in tokens, easy for the model to read."""
    if not cols:
        return "(no rows)"
    lines = [" | ".join(cols)] + [" | ".join("NULL" if v is None else str(v) for v in r) for r in rows]
    if total > len(rows):
        lines.append(f"... {total - len(rows)} more rows not shown; aggregate or add LIMIT.")
    return "\n".join(lines)


@dataclass
class QueryRecord:
    sql: str
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    total_rows: int = 0
    error: str = ""
    seconds: float = 0.0


def make_tools(allowed_schemas: list[str], log: list | None = None):
    """Build get_schema / run_sql tools scoped to `allowed_schemas`.

    `log` (optional list) collects a QueryRecord per run_sql call so a UI can show the
    SQL and result table behind each answer.
    """
    allowed = {s.lower() for s in allowed_schemas}
    schema_list = ", ".join(f"'{s}'" for s in sorted(allowed))

    @tool
    def get_schema(table_name: str = "") -> str:
        """List the tables and columns you can query, with each table's description.

        Call this before writing SQL. Optionally pass a table name (e.g.
        'accounting.cash_flow_monthly' or 'cash_flow_monthly') to see only that table.

        Args:
            table_name: Optional table to filter to.
        """
        filt = ""
        if table_name:
            name = re.sub(r"[^a-z0-9_.]", "", table_name.lower()).split(".")[-1]
            filt = f" AND c.table_name = '{name}'"
        sql = (
            "SELECT c.table_schema || '.' || c.table_name AS table_name, c.column_name, c.data_type, "
            "COALESCE(d.description, '') AS table_description "
            "FROM svv_columns c "
            "LEFT JOIN pg_namespace n ON n.nspname = c.table_schema "
            "LEFT JOIN pg_class t ON t.relname = c.table_name AND t.relnamespace = n.oid "
            "LEFT JOIN pg_description d ON d.objoid = t.oid AND d.objsubid = 0 "
            f"WHERE c.table_schema IN ({schema_list}){filt} "
            "ORDER BY 1, c.ordinal_position"
        )
        try:
            cols, rows, _ = execute(sql, max_rows=2000)
        except Exception as e:  # noqa: BLE001
            return f"ERROR reading schema: {e}"
        if not rows:
            return "No matching tables. Call get_schema() with no arguments to list all tables."
        out, current = [], None
        for table, col, dtype, desc in rows:
            if table != current:
                out.append(f"\n{table}" + (f"  -- {desc}" if desc else ""))
                current = table
            out.append(f"  {col} {dtype}")
        return "\n".join(out).strip()

    @tool
    def run_sql(sql: str) -> str:
        """Run ONE read-only Redshift SQL query (SELECT or WITH) and return the rows.

        Always use schema-qualified table names (e.g. accounting.income_statement_monthly).
        Results are capped at 200 rows, so aggregate in SQL. On an error, read the
        message, fix the SQL and try again.

        Args:
            sql: A single SELECT/WITH statement in Redshift (PostgreSQL-like) syntax.
        """
        q = sql.strip().rstrip(";").strip()
        rec = QueryRecord(sql=q)
        if log is not None:
            log.append(rec)
        if not READ_ONLY.match(q) or ";" in q:
            rec.error = "Only a single SELECT or WITH statement is allowed."
            return "ERROR: " + rec.error
        used = {m.lower() for m in SCHEMA_REF.findall(q)} - {"pg_catalog", "information_schema"}
        bad = sorted(s for s in used if s not in allowed and len(s) > 1)
        # aliases like "c.channel" also match; only reject names that look like real schemas
        bad = [s for s in bad if s in {"raw_marketing", "raw_accounting", "staging", "public",
                                       "marketing", "accounting", "analytics"}]
        if bad:
            rec.error = f"Schema(s) {bad} are not available to this advisor. Allowed: {sorted(allowed)}."
            return "ERROR: " + rec.error
        t0 = time.time()
        try:
            rec.columns, rec.rows, rec.total_rows = execute(q)
        except Exception as e:  # noqa: BLE001 - give the model the DB error to self-correct
            rec.error = str(e)
            return f"ERROR: {e}"
        finally:
            rec.seconds = round(time.time() - t0, 2)
        return to_text(rec.columns, rec.rows, rec.total_rows)

    return [get_schema, run_sql]
