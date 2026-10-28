"""Read-only source checks with persistent incidents; no repairs or notifications."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg2

from scripts.incidents import DEFAULT_STORE, Observation, record
from scripts.run_dbt import prepare_environment
from scripts.run_fault_demo import detect
from scripts.schema_fault import inspect_schema

EXPECTED = {
    "order_id": "bigint",
    "customer_id": "bigint",
    "product_id": "bigint",
    "order_ts": "timestamp with time zone",
    "amount": "numeric(12,2)",
    "currency": "text",
    "status": "text",
}
CHECKS = ["schema", "missing_amount", "duplicate_order"]


def unavailable(run_id: str, reason: dict) -> list[Observation]:
    return [
        Observation(
            observation_id=f"{run_id}:{check}",
            scope="local-compose",
            asset="raw.raw_orders",
            check=check,
            observed_at=datetime.now(timezone.utc),
            state="unknown",
            evidence=reason,
        )
        for check in CHECKS
    ]


def collect(connection, run_id: str) -> list[Observation]:
    try:
        connection.set_session(readonly=True, isolation_level="REPEATABLE READ")
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE reliability_readonly")
            cursor.execute("SET LOCAL statement_timeout = '15s'")
            cursor.execute("SET LOCAL TIME ZONE 'UTC'")
            schema = inspect_schema(cursor, "raw.raw_orders")
            schema_ok = {c.name: c.data_type for c in schema.columns} == EXPECTED
            items = [
                Observation(
                    observation_id=f"{run_id}:schema",
                    scope="local-compose",
                    asset="raw.raw_orders",
                    check="schema",
                    observed_at=schema.observed_at,
                    state="pass" if schema_ok else "fail",
                    evidence={"actual": schema.model_dump(mode="json"), "expected": EXPECTED},
                )
            ]
            if not schema_ok:
                items.extend(
                    unavailable(run_id, {"reason": "schema mismatch prevents quality query"})[1:]
                )
                return items
            data = detect(cursor, "raw", "raw_orders")
            for check, count in [
                ("missing_amount", data.null_amounts),
                ("duplicate_order", data.duplicate_excess),
            ]:
                items.append(
                    Observation(
                        observation_id=f"{run_id}:{check}",
                        scope="local-compose",
                        asset="raw.raw_orders",
                        check=check,
                        observed_at=data.observed_at,
                        state="fail" if count else "pass",
                        evidence=data.model_dump(mode="json"),
                    )
                )
            return items
    except (psycopg2.Error, ValueError) as error:
        return unavailable(
            run_id, {"error_type": type(error).__name__, "sqlstate": getattr(error, "pgcode", None)}
        )
    finally:
        connection.rollback()


def run_monitor(run_id: str | None = None, store_path: Path = DEFAULT_STORE) -> list[Observation]:
    prepare_environment()
    run_id = run_id or str(uuid4())
    try:
        connection = psycopg2.connect(
            host="127.0.0.1",
            port=5433,
            dbname="reliability",
            user="reliability_admin",
            password=os.environ["DBT_ENV_SECRET_POSTGRES_PASSWORD"],
            connect_timeout=10,
            application_name="reliability_monitor",
        )
    except psycopg2.Error as error:
        items = unavailable(run_id, {"error_type": type(error).__name__})
    else:
        try:
            items = collect(connection, run_id)
        finally:
            connection.close()
    record(items, store_path)
    return items


def main() -> int:
    run_id = str(uuid4())
    items = run_monitor(run_id)
    print(
        json.dumps({"run_id": run_id, "checks": [item.model_dump(mode="json") for item in items]})
    )
    return 0 if all(item.state == "pass" for item in items) else 1


if __name__ == "__main__":
    raise SystemExit(main())
