import os
import unittest
from unittest.mock import patch

import psycopg2

from scripts.run_fault_demo import detect, fingerprint, run_scenario


class FaultBoundaryTests(unittest.TestCase):
    def test_unapproved_identifiers_rejected_before_query(self):
        for schema, table in [("public", "orders"), ("raw", "raw_orders; DROP TABLE x")]:
            with self.assertRaises(ValueError):
                detect(None, schema, table)

    def test_invalid_sizes_rejected_before_connection_use(self):
        for copies in [0, -1, 101]:
            with self.assertRaises(ValueError):
                run_scenario(None, copies)


@unittest.skipUnless(os.environ.get("RUN_POSTGRES_TESTS") == "1", "requires real Compose database")
class PostgresFaultTests(unittest.TestCase):
    def setUp(self):
        self.connection = psycopg2.connect(
            host="127.0.0.1",
            port=5433,
            dbname="reliability",
            user="reliability_admin",
            password=os.environ["POSTGRES_PASSWORD"],
            connect_timeout=10,
        )
        self.addCleanup(self.connection.close)

    def test_real_duplicate_impact_recovery_and_repeatability(self):
        for copies in [1, 3]:
            report = run_scenario(self.connection, copies)
            self.assertEqual(report.fault.duplicate_excess, copies)
            self.assertEqual(
                report.fault.duplicate_samples,
                [{"order_id": report.selected_order_id, "occurrences": copies + 1}],
            )
            self.assertEqual(report.observed_revenue_increase, report.expected_revenue_increase)
            self.assertGreater(report.observed_revenue_increase, 0)
            self.assertEqual(report.restored.revenue_usd, report.before.revenue_usd)
            self.assertEqual(report.source_fingerprint_before, report.source_fingerprint_after)
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('pg_temp.f03_orders')")
                self.assertIsNone(cursor.fetchone()[0])
            self.connection.rollback()

    def test_detection_exception_rolls_back_and_removes_sandbox(self):
        with self.connection.cursor() as cursor:
            original = fingerprint(cursor)
        self.connection.rollback()
        calls = 0

        def fail_after_injection(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("forced detector failure")
            return detect(*args)

        with patch("scripts.run_fault_demo.detect", side_effect=fail_after_injection):
            with self.assertRaisesRegex(RuntimeError, "forced detector failure"):
                run_scenario(self.connection, 2)
        with self.connection.cursor() as cursor:
            self.assertEqual(fingerprint(cursor), original)
            cursor.execute("SELECT to_regclass('pg_temp.f03_orders')")
            self.assertIsNone(cursor.fetchone()[0])

    def test_database_denies_source_writes_under_demo_role(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE reliability_readonly")
            with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                cursor.execute(
                    "INSERT INTO raw.raw_orders SELECT * FROM raw.raw_orders WHERE false"
                )
        self.connection.rollback()
