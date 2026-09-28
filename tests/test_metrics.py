import unittest

from scripts.verify_models import aggregate, expected_metrics, require_equal


class MetricTests(unittest.TestCase):
    def test_cancelled_excluded_rounding_and_distinct_customers(self):
        rows = [
            {"customer_id": "1", "amount": "10.01", "status": "completed"},
            {"customer_id": "1", "amount": "20.00", "status": "completed"},
            {"customer_id": "2", "amount": "999.00", "status": "cancelled"},
        ]
        self.assertEqual(
            aggregate(rows),
            {
                "orders": "3",
                "completed_orders": "2",
                "customers": "1",
                "revenue_usd": "30.01",
                "aov_usd": "15.01",
            },
        )

    def test_zero_completed_has_no_aov(self):
        actual = aggregate([{"customer_id": "1", "amount": "25", "status": "cancelled"}])
        self.assertEqual(actual["revenue_usd"], "0.00")
        self.assertEqual(actual["aov_usd"], "")
        self.assertEqual(aggregate([])["orders"], "0")

    def test_utc_boundary_and_period_distinct_count(self):
        rows = [
            {
                "customer_id": "1",
                "amount": "10.00",
                "status": "completed",
                "order_ts": "2025-01-01T23:30:00-06:00",
            },
            {
                "customer_id": "1",
                "amount": "20.00",
                "status": "completed",
                "order_ts": "2025-01-03T12:00:00+00:00",
            },
        ]
        actual = expected_metrics(rows, [{"customer_id": "1", "region": "West"}])
        self.assertEqual(actual["daily_revenue"][0]["order_date"], "2025-01-02")
        self.assertEqual(actual["executive_metrics"][0]["customers"], "1")
        self.assertEqual(actual["executive_metrics"][0]["revenue_usd"], "30.00")
        self.assertEqual(actual["regional_revenue"][0]["region"], "West")

    def test_corrupted_metric_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "differs"):
            require_equal("daily_revenue", [{"revenue_usd": "10.00"}], [{"revenue_usd": "20.00"}])
