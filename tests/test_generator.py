import csv
import hashlib
import json
import tempfile
import unittest
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from data.generator.generate import Config, generate


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_same_seed_produces_identical_files(self):
        config = Config(months=1, daily_orders=5)
        generate(self.root / "a", config)
        generate(self.root / "b", config)
        for path in (self.root / "a").iterdir():
            self.assertEqual(path.read_bytes(), (self.root / "b" / path.name).read_bytes())

    def test_seed_changes_data(self):
        a = generate(self.root / "a", Config(seed=1, months=1, daily_orders=5))
        b = generate(self.root / "b", Config(seed=2, months=1, daily_orders=5))
        self.assertNotEqual(a["sha256"]["raw_orders.csv"], b["sha256"]["raw_orders.csv"])

    def test_full_18_months_baseline_and_relations(self):
        out = self.root / "data"
        manifest = generate(out, Config())
        orders = read_csv(out / "raw_orders.csv")
        customers = {r["customer_id"]: r for r in read_csv(out / "raw_customers.csv")}
        products = {r["product_id"] for r in read_csv(out / "raw_products.csv")}
        daily = defaultdict(lambda: [0, 0, Decimal("0.00")])
        self.assertEqual(len(orders), len({r["order_id"] for r in orders}))
        self.assertEqual(len(orders), manifest["row_counts"]["raw_orders"])
        for row in orders:
            timestamp = datetime.fromisoformat(row["order_ts"])
            self.assertEqual(timestamp.utcoffset().total_seconds(), 0)
            day = timestamp.date()
            self.assertGreaterEqual(day, date(2025, 1, 1))
            self.assertLess(day, date(2026, 7, 1))
            self.assertIn(row["customer_id"], customers)
            self.assertLess(date.fromisoformat(customers[row["customer_id"]]["signup_ts"]), day)
            self.assertIn(row["product_id"], products)
            self.assertEqual(row["currency"], "USD")
            self.assertIn(row["status"], {"completed", "cancelled"})
            self.assertGreater(Decimal(row["amount"]), 0)
            daily[day.isoformat()][0] += 1
            if row["status"] == "completed":
                daily[day.isoformat()][1] += 1
                daily[day.isoformat()][2] += Decimal(row["amount"])
        self.assertEqual(len(daily), 546)
        for row in read_csv(out / "daily_baseline.csv"):
            self.assertEqual(
                daily[row["date"]],
                [int(row["orders"]), int(row["completed_orders"]), Decimal(row["revenue_usd"])],
            )
        self.assertEqual(sum(v[2] for v in daily.values()), Decimal(manifest["revenue_usd"]))
        for filename, checksum in manifest["sha256"].items():
            self.assertEqual(hashlib.sha256((out / filename).read_bytes()).hexdigest(), checksum)
        self.assertTrue(json.loads((out / "manifest.json").read_text())["synthetic"])

    def test_leap_year_and_year_boundary(self):
        manifest = generate(
            self.root / "leap", Config(start_date=date(2024, 2, 1), months=1, daily_orders=2)
        )
        self.assertEqual(manifest["days"], 29)
        self.assertEqual(Config(start_date=date(2025, 12, 1), months=2).end_date, date(2026, 2, 1))

    def test_existing_data_is_not_overwritten(self):
        dest = self.root / "existing"
        dest.mkdir()
        sentinel = dest / "keep.txt"
        sentinel.write_text("keep")
        with self.assertRaises(FileExistsError):
            generate(dest, Config(months=1))
        self.assertEqual(sentinel.read_text(), "keep")

    def test_invalid_configuration(self):
        for kwargs in (
            {"months": 0},
            {"months": 25},
            {"customers": 0},
            {"products": 0},
            {"daily_orders": -1},
            {"start_date": date(2025, 1, 15)},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Config(**kwargs)


if __name__ == "__main__":
    unittest.main()
