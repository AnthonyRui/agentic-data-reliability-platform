"""F01: measure schema drift and a real failed query, then restore a temporary table."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import uuid4

import psycopg2
from pydantic import BaseModel

from scripts.run_fault_demo import fingerprint

SCHEMA_QUERY = """SELECT attname, format_type(atttypid, atttypmod), NOT attnotnull
    FROM pg_attribute WHERE attrelid = to_regclass(%s)
    AND attnum > 0 AND NOT attisdropped ORDER BY attnum"""
REVENUE_QUERY = """SELECT count(*), coalesce(sum(amount)
    FILTER (WHERE status = 'completed'), 0) FROM pg_temp.f01_orders"""


class Column(BaseModel):
    name: str
    data_type: str
    nullable: bool


class SchemaEvidence(BaseModel):
    observed_at: str
    relation: str
    query: str = SCHEMA_QUERY
    parameters: list[str]
    columns: list[Column]


class QueryEvidence(BaseModel):
    observed_at: str
    query: str = REVENUE_QUERY
    status: Literal["success", "error"]
    rows: int | None = None
    revenue_usd: Decimal | None = None
    sqlstate: str | None = None
    error: str | None = None


class SchemaFaultReport(BaseModel):
    run_id: str
    scenario: Literal["F01"] = "F01"
    scope: Literal["session_temporary_table"] = "session_temporary_table"
    status: Literal["recovered"] = "recovered"
    before: SchemaEvidence
    fault: SchemaEvidence
    restored: SchemaEvidence
    missing_columns: list[str]
    added_columns: list[str]
    before_query: QueryEvidence
    fault_query: QueryEvidence
    restored_query: QueryEvidence
    source_fingerprint_before: str
    source_fingerprint_after: str
    recovery: Literal["ROLLBACK TO SAVEPOINT before_fault"] = "ROLLBACK TO SAVEPOINT before_fault"


def inspect_schema(cursor, relation: str) -> SchemaEvidence:
    if relation not in {"raw.raw_orders", "pg_temp.f01_orders"}:
        raise ValueError("Unsupported schema inspection target")
    cursor.execute(SCHEMA_QUERY, (relation,))
    columns = [
        Column(name=name, data_type=kind, nullable=nullable)
        for name, kind, nullable in cursor.fetchall()
    ]
    if not columns:
        raise ValueError("Schema target does not exist or contains no columns")
    return SchemaEvidence(
        observed_at=datetime.now(timezone.utc).isoformat(),
        relation=relation,
        parameters=[relation],
        columns=columns,
    )


def probe_revenue(cursor) -> QueryEvidence:
    # PostgreSQL errors abort the transaction until this inner savepoint is restored.
    cursor.execute("SAVEPOINT query_probe")
    try:
        cursor.execute(REVENUE_QUERY)
        rows, revenue = cursor.fetchone()
        return QueryEvidence(
            observed_at=datetime.now(timezone.utc).isoformat(),
            status="success",
            rows=rows,
            revenue_usd=revenue,
        )
    except psycopg2.errors.UndefinedColumn as error:
        return QueryEvidence(
            observed_at=datetime.now(timezone.utc).isoformat(),
            status="error",
            sqlstate=error.pgcode,
            error=error.diag.message_primary,
        )
    finally:
        cursor.execute("ROLLBACK TO SAVEPOINT query_probe")
        cursor.execute("RELEASE SAVEPOINT query_probe")


def run_schema_scenario(connection) -> SchemaFaultReport:
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
            source_schema = inspect_schema(cursor, "raw.raw_orders")
            cursor.execute(
                "CREATE TEMP TABLE f01_orders ON COMMIT DROP AS SELECT * FROM raw.raw_orders"
            )
            before = inspect_schema(cursor, "pg_temp.f01_orders")
            before_query = probe_revenue(cursor)
            if before_query.status != "success" or not before_query.rows:
                raise ValueError("F01 requires a nonempty baseline with a working amount query")
            cursor.execute("SAVEPOINT before_fault")
            cursor.execute(
                "ALTER TABLE pg_temp.f01_orders RENAME COLUMN amount TO order_amount_usd"
            )
            fault = inspect_schema(cursor, "pg_temp.f01_orders")
            missing = sorted({c.name for c in before.columns} - {c.name for c in fault.columns})
            added = sorted({c.name for c in fault.columns} - {c.name for c in before.columns})
            fault_query = probe_revenue(cursor)
            if (
                missing != ["amount"]
                or added != ["order_amount_usd"]
                or fault_query.status != "error"
                or fault_query.sqlstate != "42703"
            ):
                raise ValueError("F01 schema change or query failure did not match actual evidence")
            cursor.execute("ROLLBACK TO SAVEPOINT before_fault")
            restored = inspect_schema(cursor, "pg_temp.f01_orders")
            restored_query = probe_revenue(cursor)
            if (
                restored.columns != before.columns
                or restored_query.status != "success"
                or restored_query.rows != before_query.rows
                or restored_query.revenue_usd != before_query.revenue_usd
            ):
                raise ValueError("F01 schema or query failed to recover")
            after = fingerprint(cursor)
            if (
                original != after
                or inspect_schema(cursor, "raw.raw_orders").columns != source_schema.columns
            ):
                raise ValueError("Source schema or data changed during F01")
            return SchemaFaultReport(
                run_id=str(uuid4()),
                before=before,
                fault=fault,
                restored=restored,
                missing_columns=missing,
                added_columns=added,
                before_query=before_query,
                fault_query=fault_query,
                restored_query=restored_query,
                source_fingerprint_before=original,
                source_fingerprint_after=after,
            )
    finally:
        connection.rollback()
