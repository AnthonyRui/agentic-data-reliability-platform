"""Compare a running Compose database against the generated baseline (read only)."""

import csv
import io
import json
import subprocess
from pathlib import Path


def query(sql: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "reliability_admin",
            "-d",
            "reliability",
            "-A",
            "-t",
            "-c",
            sql,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def verify() -> dict:
    root = Path(__file__).resolve().parents[1]
    data = root / "data/generated"
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    for name in ("raw_customers", "raw_products", "raw_orders"):
        count = int(query(f"SELECT count(*) FROM raw.{name}"))
        if count != manifest["row_counts"][name]:
            raise ValueError(f"Row count mismatch for {name}: {count}")
    with (data / "daily_baseline.csv").open(encoding="utf-8", newline="") as handle:
        expected = list(csv.DictReader(handle))
    sql = """COPY (
        SELECT (order_ts AT TIME ZONE 'UTC')::date AS date, count(*) AS orders,
               count(*) FILTER (WHERE status = 'completed') AS completed_orders,
               coalesce(sum(amount) FILTER (WHERE status = 'completed'), 0)::numeric(16,2) AS revenue_usd
        FROM raw.raw_orders GROUP BY 1 ORDER BY 1
    ) TO STDOUT WITH CSV HEADER"""
    actual = list(csv.DictReader(io.StringIO(query(sql))))
    if actual != expected:
        raise ValueError("Database daily revenue/counts differ from the generated baseline")
    invalid = query("""SELECT count(*) FROM raw.raw_orders o
        LEFT JOIN raw.raw_customers c USING (customer_id)
        LEFT JOIN raw.raw_products p USING (product_id)
        WHERE c.customer_id IS NULL OR p.product_id IS NULL OR o.amount IS NULL
          OR o.amount < 0 OR o.currency <> 'USD'""")
    if invalid != "0":
        raise ValueError(f"Invalid baseline records: {invalid}")
    duplicates = query("SELECT count(*) - count(DISTINCT order_id) FROM raw.raw_orders")
    if duplicates != "0":
        raise ValueError(f"Duplicate baseline orders: {duplicates}")
    rights = query("""SELECT has_table_privilege('reliability_readonly', 'raw.raw_orders', 'SELECT')
        AND NOT has_table_privilege('reliability_readonly', 'raw.raw_orders', 'INSERT')
        AND NOT has_table_privilege('reliability_readonly', 'raw.raw_orders', 'UPDATE')
        AND NOT has_table_privilege('reliability_readonly', 'raw.raw_orders', 'DELETE')
        AND NOT has_table_privilege('reliability_readonly', 'raw.raw_orders', 'TRUNCATE')""")
    if rights != "t":
        raise ValueError("Read-only role permissions are incorrect")
    return {"event": "database_verified", "days": len(actual), "row_counts": manifest["row_counts"]}


if __name__ == "__main__":
    print(json.dumps(verify()))
