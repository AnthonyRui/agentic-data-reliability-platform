"""F02: reproducible missing amounts in a transaction-scoped PostgreSQL copy."""

from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from scripts.run_fault_demo import QualityEvidence, detect, fingerprint


class NullFaultReport(BaseModel):
    run_id: str
    scenario: Literal["F02"] = "F02"
    scope: Literal["session_temporary_table"] = "session_temporary_table"
    status: Literal["recovered"] = "recovered"
    selection: str
    affected_rows: int = Field(gt=0)
    expected_revenue_loss: Decimal
    observed_revenue_loss: Decimal
    before: QualityEvidence
    fault: QualityEvidence
    restored: QualityEvidence
    source_fingerprint_before: str
    source_fingerprint_after: str
    recovery: Literal["ROLLBACK TO SAVEPOINT before_fault"] = "ROLLBACK TO SAVEPOINT before_fault"


def run_null_scenario(connection) -> NullFaultReport:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE reliability_readonly")
            cursor.execute("SET LOCAL statement_timeout = '15s'")
            cursor.execute("SET LOCAL TIME ZONE 'UTC'")
            cursor.execute("""SELECT current_user = 'reliability_readonly'
                AND NOT has_table_privilege(current_user, 'raw.raw_orders', 'UPDATE')
                AND NOT has_table_privilege(current_user, 'raw.raw_orders', 'INSERT')
                AND NOT has_table_privilege(current_user, 'raw.raw_orders', 'DELETE')
                AND NOT has_table_privilege(current_user, 'raw.raw_orders', 'TRUNCATE')""")
            if cursor.fetchone()[0] is not True:
                raise ValueError("Source read-only permission check failed")
            original = fingerprint(cursor)
            cursor.execute(
                "CREATE TEMP TABLE f02_orders ON COMMIT DROP AS SELECT * FROM raw.raw_orders"
            )
            before = detect(cursor, "pg_temp", "f02_orders")
            if not before.rows or before.duplicate_excess or before.null_ids or before.null_amounts:
                raise ValueError(
                    "F02 requires nonempty, unique orders with non-null IDs and amounts"
                )
            # Stable hash ordering approximates random sampling and is reproducible across runs.
            # Include cancelled orders too: their missing amounts must not reduce revenue.
            affected = (before.rows * 3 + 9) // 10
            selection = """SELECT order_id, amount, status FROM pg_temp.f02_orders
                ORDER BY md5(order_id::text || ':F02:42'), order_id LIMIT %s"""
            cursor.execute(selection, (affected,))
            selected = cursor.fetchall()
            expected = sum(
                (amount for _, amount, status in selected if status == "completed"), Decimal(0)
            )
            order_ids = [key for key, _, _ in selected]
            cursor.execute("SAVEPOINT before_fault")
            cursor.execute(
                "UPDATE pg_temp.f02_orders SET amount = NULL WHERE order_id = ANY(%s)", (order_ids,)
            )
            if cursor.rowcount != affected:
                raise ValueError("F02 did not update the expected number of sandbox rows")
            fault = detect(cursor, "pg_temp", "f02_orders")
            observed = before.revenue_usd - fault.revenue_usd
            if (
                fault.rows != before.rows
                or fault.null_amounts != affected
                or fault.duplicate_excess
                or observed != expected
            ):
                raise ValueError("F02 observed counts or revenue loss differ from selected orders")
            cursor.execute("ROLLBACK TO SAVEPOINT before_fault")
            restored = detect(cursor, "pg_temp", "f02_orders")
            if (
                restored.rows,
                restored.null_amounts,
                restored.null_ids,
                restored.duplicate_excess,
                restored.revenue_usd,
            ) != (before.rows, 0, 0, 0, before.revenue_usd):
                raise ValueError("F02 sandbox failed to recover")
            after = fingerprint(cursor)
            if after != original:
                raise ValueError("Source data changed during F02")
            return NullFaultReport(
                run_id=str(uuid4()),
                selection=selection.replace("%s", str(affected)),
                affected_rows=affected,
                expected_revenue_loss=expected,
                observed_revenue_loss=observed,
                before=before,
                fault=fault,
                restored=restored,
                source_fingerprint_before=original,
                source_fingerprint_after=after,
            )
    finally:
        connection.rollback()
