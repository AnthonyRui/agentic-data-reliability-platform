"""Check actual dbt outputs against independent Python/CSV calculations."""

import csv
import io
import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from scripts.verify_database import query


def aggregate(rows: list[dict]) -> dict[str, str]:
    completed = [row for row in rows if row["status"].strip().lower() == "completed"]
    revenue = sum((Decimal(row["amount"]) for row in completed), Decimal("0"))
    aov = (
        (revenue / len(completed)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if completed
        else None
    )
    return {
        "orders": str(len(rows)),
        "completed_orders": str(len(completed)),
        "customers": str(len({row["customer_id"] for row in completed})),
        "revenue_usd": f"{revenue:.2f}",
        "aov_usd": "" if aov is None else f"{aov:.2f}",
    }


def expected_metrics(orders: list[dict], customers: list[dict]) -> dict[str, list[dict]]:
    regions = {r["customer_id"]: r["region"].strip() for r in customers}
    days = defaultdict(list)
    regional = defaultdict(list)
    for row in orders:
        date = datetime.fromisoformat(row["order_ts"]).astimezone(timezone.utc).date().isoformat()
        days[date].append(row)
        regional[(date, regions[row["customer_id"]])].append(row)
    daily = [{"order_date": day, **aggregate(rows)} for day, rows in sorted(days.items())]
    region_rows = []
    for (day, region), rows in sorted(regional.items()):
        result = aggregate(rows)
        region_rows.append(
            {
                "order_date": day,
                "region": region,
                **{k: result[k] for k in ("orders", "completed_orders", "revenue_usd")},
            }
        )
    totals = {
        "period_start": min(days, default=""),
        "period_end": max(days, default=""),
        **aggregate(orders),
    }
    return {"daily_revenue": daily, "regional_revenue": region_rows, "executive_metrics": [totals]}


def require_equal(name: str, actual: list[dict], expected: list[dict]) -> None:
    if actual != expected:
        raise ValueError(f"{name} differs from the independent CSV baseline")


def verify() -> dict:
    data = Path(__file__).resolve().parents[1] / "data/generated"

    def read(name):
        with (data / name).open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    orders = read("raw_orders.csv")
    expected = expected_metrics(orders, read("raw_customers.csv"))
    columns = {
        "daily_revenue": "order_date, orders, completed_orders, customers, revenue_usd, aov_usd",
        "regional_revenue": "order_date, region, orders, completed_orders, revenue_usd",
        "executive_metrics": "period_start, period_end, orders, completed_orders, customers, revenue_usd, aov_usd",
    }
    for model, fields in columns.items():
        order_by = "order_date, region" if model == "regional_revenue" else "1"
        actual = list(
            csv.DictReader(
                io.StringIO(
                    query(
                        f"COPY (SELECT {fields} FROM analytics.{model} ORDER BY {order_by}) TO STDOUT WITH CSV HEADER"
                    )
                )
            )
        )
        require_equal(model, actual, expected[model])
    if int(query("SELECT count(*) FROM analytics.fact_sales")) != len(orders):
        raise ValueError("fact_sales does not preserve the original order count")
    rights = query("""SELECT has_table_privilege('reliability_transform', 'raw.raw_orders', 'SELECT')
        AND NOT has_table_privilege('reliability_transform', 'raw.raw_orders', 'UPDATE')
        AND NOT has_table_privilege('reliability_transform', 'raw.raw_orders', 'INSERT')
        AND NOT has_table_privilege('reliability_transform', 'raw.raw_orders', 'DELETE')
        AND NOT has_table_privilege('reliability_transform', 'raw.raw_orders', 'TRUNCATE')
        AND has_table_privilege('reliability_readonly', 'analytics.daily_revenue', 'SELECT')
        AND NOT has_table_privilege('reliability_readonly', 'analytics.daily_revenue', 'UPDATE')""")
    if rights != "t":
        raise ValueError("Transform or investigation role permissions are incorrect")
    return {
        "event": "models_verified",
        "days": len(expected["daily_revenue"]),
        "regional_rows": len(expected["regional_revenue"]),
        "orders": len(orders),
    }


if __name__ == "__main__":
    print(json.dumps(verify()))
