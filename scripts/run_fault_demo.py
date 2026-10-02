"""F03: inject, detect and roll back duplicate orders in session-local tables only."""

import argparse
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import uuid4

import psycopg2
from psycopg2 import sql
from pydantic import BaseModel, Field

from scripts.run_dbt import prepare_environment
from scripts.run_pipeline import pipeline_lock

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_TABLES = {("raw", "raw_orders"), ("pg_temp", "f03_orders")}


class QualityEvidence(BaseModel):
    observed_at: str
    relation: str
    query: str
    rows: int = Field(ge=0)
    duplicate_excess: int = Field(ge=0)
    null_ids: int = Field(ge=0)
    revenue_usd: Decimal
    duplicate_samples: list[dict]


class FaultReport(BaseModel):
    run_id: str
    scenario: Literal["F03"] = "F03"
    scope: Literal["session_temporary_table"] = "session_temporary_table"
    status: Literal["recovered"] = "recovered"
    injected_copies: int = Field(ge=1, le=100)
    selected_order_id: int
    expected_revenue_increase: Decimal
    observed_revenue_increase: Decimal
    before: QualityEvidence
    fault: QualityEvidence
    restored: QualityEvidence
    source_fingerprint_before: str
    source_fingerprint_after: str
    recovery: Literal["ROLLBACK TO SAVEPOINT before_fault"] = "ROLLBACK TO SAVEPOINT before_fault"


def detect(cursor, schema: str, table: str) -> QualityEvidence:
    """Fixed read-only queries; table identifiers are constrained and quoted."""
    if (schema, table) not in ALLOWED_TABLES:
        raise ValueError("Only the demo source and session sandbox are supported")
    relation = sql.Identifier(schema, table)
    statement = sql.SQL("""SELECT count(*), count(order_id) - count(DISTINCT order_id),
        count(*) FILTER (WHERE order_id IS NULL),
        coalesce(sum(amount) FILTER (WHERE status = 'completed'), 0)
        FROM {}""").format(relation)
    cursor.execute(statement)
    rows, excess, nulls, revenue = cursor.fetchone()
    samples = sql.SQL("""SELECT order_id, count(*) FROM {} WHERE order_id IS NOT NULL
        GROUP BY order_id HAVING count(*) > 1 ORDER BY order_id LIMIT 10""").format(relation)
    cursor.execute(samples)
    return QualityEvidence(
        observed_at=datetime.now(timezone.utc).isoformat(),
        relation=f"{schema}.{table}",
        query=statement.as_string(cursor) + ";\n" + samples.as_string(cursor),
        rows=rows,
        duplicate_excess=excess,
        null_ids=nulls,
        revenue_usd=revenue,
        duplicate_samples=[
            {"order_id": key, "occurrences": count} for key, count in cursor.fetchall()
        ],
    )


def fingerprint(cursor) -> str:
    cursor.execute("""SELECT md5(coalesce(string_agg(payload, E'\n' ORDER BY payload), ''))
        FROM (SELECT row_to_json(o)::text AS payload FROM raw.raw_orders o) s""")
    return cursor.fetchone()[0]


def run_scenario(connection, copies: int) -> FaultReport:
    if not 1 <= copies <= 100:
        raise ValueError("copies must be between 1 and 100")
    # The caller owns this dedicated connection. Nothing is committed, even on failure.
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE reliability_readonly")
            cursor.execute("SET LOCAL statement_timeout = '15s'")
            cursor.execute("SET LOCAL TIME ZONE 'UTC'")
            cursor.execute("""SELECT current_user = 'reliability_readonly'
                AND NOT has_table_privilege(current_user, 'raw.raw_orders', 'INSERT')
                AND NOT has_table_privilege(current_user, 'raw.raw_orders', 'UPDATE')
                AND NOT has_table_privilege(current_user, 'raw.raw_orders', 'DELETE')
                AND NOT has_table_privilege(current_user, 'raw.raw_orders', 'TRUNCATE')""")
            if cursor.fetchone()[0] is not True:
                raise ValueError("Source read-only permission check failed")
            original = fingerprint(cursor)
            cursor.execute(
                "CREATE TEMP TABLE f03_orders ON COMMIT DROP AS SELECT * FROM raw.raw_orders"
            )
            before = detect(cursor, "pg_temp", "f03_orders")
            if before.duplicate_excess or before.null_ids:
                raise ValueError("F03 requires a healthy baseline with unique, non-null order IDs")
            cursor.execute("""SELECT order_id, amount FROM pg_temp.f03_orders
                WHERE status = 'completed' AND amount > 0 ORDER BY order_id LIMIT 1""")
            selected = cursor.fetchone()
            if selected is None:
                raise ValueError("No completed positive-value order available for the scenario")
            order_id, amount = selected
            cursor.execute("SAVEPOINT before_fault")
            cursor.execute(
                """INSERT INTO pg_temp.f03_orders
                SELECT o.* FROM pg_temp.f03_orders o CROSS JOIN generate_series(1, %s)
                WHERE order_id = %s""",
                (copies, order_id),
            )
            fault = detect(cursor, "pg_temp", "f03_orders")
            expected = amount * copies
            observed = fault.revenue_usd - before.revenue_usd
            if (
                fault.duplicate_excess != copies
                or fault.rows != before.rows + copies
                or observed != expected
            ):
                raise ValueError(
                    "Injected duplicate count or revenue impact did not match evidence"
                )
            cursor.execute("ROLLBACK TO SAVEPOINT before_fault")
            restored = detect(cursor, "pg_temp", "f03_orders")
            if (
                restored.rows,
                restored.duplicate_excess,
                restored.null_ids,
                restored.revenue_usd,
            ) != (before.rows, 0, 0, before.revenue_usd):
                raise ValueError("Sandbox did not recover to the healthy baseline")
            after = fingerprint(cursor)
            if original != after:
                raise ValueError("Source data changed during the scenario")
            return FaultReport(
                run_id=str(uuid4()),
                injected_copies=copies,
                selected_order_id=order_id,
                expected_revenue_increase=expected,
                observed_revenue_increase=observed,
                before=before,
                fault=fault,
                restored=restored,
                source_fingerprint_before=original,
                source_fingerprint_after=after,
            )
    finally:
        connection.rollback()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--copies", type=int, default=3, choices=range(1, 101), metavar="1..100")
    args = parser.parse_args()
    prepare_environment()
    # This demo deliberately targets the documented local Compose database only.
    with pipeline_lock(ROOT / ".local/pipeline.lock"):
        connection = psycopg2.connect(
            host="127.0.0.1",
            port=5433,
            dbname="reliability",
            user="reliability_admin",
            password=os.environ["DBT_ENV_SECRET_POSTGRES_PASSWORD"],
            connect_timeout=10,
            application_name="reliability_f03_sandbox",
        )
        try:
            report = run_scenario(connection, args.copies)
        finally:
            connection.close()
        output = ROOT / ".local/fault-runs"
        output.mkdir(parents=True, exist_ok=True)
        path = output / f"{report.run_id}.json"
        with path.open("x", encoding="utf-8") as handle:
            handle.write(report.model_dump_json(indent=2) + "\n")
        # Keep actual SQL and observed values in CI logs as well as the local report.
        print(report.model_dump_json())
        print(
            json.dumps(
                {
                    "event": "sandbox_fault_recovered",
                    "scenario": "F03",
                    "run_id": report.run_id,
                    "report": str(path),
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
