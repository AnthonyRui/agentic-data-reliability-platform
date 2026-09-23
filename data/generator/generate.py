"""Generate a healthy, deterministic baseline using Python 3.11+ only."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Config:
    seed: int = 42
    start_date: date = date(2025, 1, 1)
    months: int = 18
    customers: int = 500
    products: int = 40
    daily_orders: int = 80

    def __post_init__(self) -> None:
        if self.start_date.day != 1:
            raise ValueError("start_date must be the first day of a month")
        if not 1 <= self.months <= 24:
            raise ValueError("months must be between 1 and 24")
        for key, limit in (("customers", 100_000), ("products", 10_000), ("daily_orders", 10_000)):
            if not 1 <= getattr(self, key) <= limit:
                raise ValueError(f"{key} must be between 1 and {limit}")
        _ = self.end_date

    @property
    def end_date(self) -> date:
        month_index = self.start_date.year * 12 + self.start_date.month - 1 + self.months
        return date(month_index // 12, month_index % 12 + 1, 1)


def money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def write_csv(path: Path, fields: list[str], rows: Iterable[dict]) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def generate(output: Path, config: Config) -> dict:
    """Refuse existing destinations so a baseline cannot be silently replaced."""
    output.mkdir(parents=True, exist_ok=False)
    rng = random.Random(config.seed)
    customers = [
        {
            "customer_id": i,
            "region": rng.choice(["North", "South", "East", "West"]),
            "signup_ts": (config.start_date - timedelta(days=rng.randint(1, 730))).isoformat(),
            "segment": rng.choice(["consumer", "business"]),
        }
        for i in range(1, config.customers + 1)
    ]
    costs = {i: rng.randint(200, 12_000) for i in range(1, config.products + 1)}
    products = [
        {
            "product_id": i,
            "category": rng.choice(["home", "electronics", "outdoors", "clothing"]),
            "unit_cost": money(cost),
        }
        for i, cost in costs.items()
    ]
    write_csv(
        output / "raw_customers.csv", ["customer_id", "region", "signup_ts", "segment"], customers
    )
    write_csv(output / "raw_products.csv", ["product_id", "category", "unit_cost"], products)
    baseline: list[dict] = []
    counts = {"raw_customers": config.customers, "raw_products": config.products, "raw_orders": 0}
    total_revenue = 0

    def orders() -> Iterable[dict]:
        nonlocal total_revenue
        days = (config.end_date - config.start_date).days
        for offset in range(days):
            day = config.start_date + timedelta(days=offset)
            seasonal = 1 + 0.15 * math.cos(2 * math.pi * (day.timetuple().tm_yday - 350) / 365.25)
            weekday = 1.15 if day.weekday() >= 5 else 1.0
            trend = 1 + 0.15 * offset / days
            volume = max(
                1, round(config.daily_orders * seasonal * weekday * trend * rng.uniform(0.85, 1.15))
            )
            completed = revenue = 0
            for _ in range(volume):
                counts["raw_orders"] += 1
                product = rng.randint(1, config.products)
                amount = costs[product] * rng.randint(130, 220) // 100
                status = "completed" if rng.random() < 0.94 else "cancelled"
                if status == "completed":
                    completed += 1
                    revenue += amount
                timestamp = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
                timestamp += timedelta(seconds=rng.randrange(86400))
                yield {
                    "order_id": counts["raw_orders"],
                    "customer_id": rng.randint(1, config.customers),
                    "product_id": product,
                    "order_ts": timestamp.isoformat(),
                    "amount": money(amount),
                    "currency": "USD",
                    "status": status,
                }
            total_revenue += revenue
            baseline.append(
                {
                    "date": day.isoformat(),
                    "orders": volume,
                    "completed_orders": completed,
                    "revenue_usd": money(revenue),
                }
            )

    write_csv(
        output / "raw_orders.csv",
        ["order_id", "customer_id", "product_id", "order_ts", "amount", "currency", "status"],
        orders(),
    )
    write_csv(
        output / "daily_baseline.csv",
        ["date", "orders", "completed_orders", "revenue_usd"],
        baseline,
    )
    settings = asdict(config)
    settings["start_date"] = config.start_date.isoformat()
    manifest = {
        "format_version": 1,
        "generator_version": 1,
        "synthetic": True,
        "config": settings,
        "end_date_exclusive": config.end_date.isoformat(),
        "row_counts": counts,
        "days": len(baseline),
        "revenue_usd": money(total_revenue),
        "revenue_definition": "SUM(amount) for completed orders; USD; UTC calendar day; excludes cancelled orders",
        "sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob("*.csv"))
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/generated"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--months", type=int, default=18)
    parser.add_argument("--daily-orders", type=int, default=80)
    args = parser.parse_args()
    try:
        config = Config(
            seed=args.seed,
            start_date=args.start_date,
            months=args.months,
            daily_orders=args.daily_orders,
        )
        manifest = generate(args.output, config)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Generation failed: {exc}\n")
    print(
        json.dumps(
            {
                "event": "dataset_generated",
                "output": str(args.output),
                "row_counts": manifest["row_counts"],
                "days": manifest["days"],
            }
        )
    )


if __name__ == "__main__":
    main()
