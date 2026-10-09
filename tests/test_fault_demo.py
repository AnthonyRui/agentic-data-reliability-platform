import os
import unittest
from unittest.mock import patch

import psycopg2

from scripts.null_fault import run_null_scenario
from scripts.run_fault_demo import detect, fingerprint, main, run_scenario
from scripts.schema_fault import inspect_schema, run_schema_scenario


class FaultBoundaryTests(unittest.TestCase):
    def test_schema_inspection_rejects_unknown_target(self):
        with self.assertRaises(ValueError):
            inspect_schema(None, "raw.raw_orders; DROP TABLE x")

    def test_null_scenario_rejects_duplicate_parameter_before_connecting(self):
        with patch("sys.argv", ["demo", "--scenario", "F02", "--copies", "3"]):
            with patch("scripts.run_fault_demo.prepare_environment") as prepare:
                with self.assertRaises(SystemExit) as error:
                    main()
                self.assertEqual(error.exception.code, 2)
                prepare.assert_not_called()

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

    def test_renamed_amount_causes_real_query_error_and_recovers(self):
        for _ in range(2):
            report = run_schema_scenario(self.connection)
            self.assertEqual(report.missing_columns, ["amount"])
            self.assertEqual(report.added_columns, ["order_amount_usd"])
            self.assertEqual(report.fault_query.status, "error")
            self.assertEqual(report.fault_query.sqlstate, "42703")
            self.assertIn("amount", report.fault_query.error)
            self.assertIsNone(report.fault_query.revenue_usd)
            self.assertIsNone(report.fault_query.rows)
            self.assertEqual(report.before.columns, report.restored.columns)
            self.assertEqual(report.before_query.revenue_usd, report.restored_query.revenue_usd)
            self.assertEqual(report.source_fingerprint_before, report.source_fingerprint_after)
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('pg_temp.f01_orders')")
                self.assertIsNone(cursor.fetchone()[0])
            self.connection.rollback()

    def test_schema_inspection_failure_after_rename_cleans_up(self):
        with self.connection.cursor() as cursor:
            original = fingerprint(cursor)
            original_schema = inspect_schema(cursor, "raw.raw_orders")
        self.connection.rollback()
        calls = 0

        def fail_after_rename(*args):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("forced schema inspection failure")
            return inspect_schema(*args)

        with patch("scripts.schema_fault.inspect_schema", side_effect=fail_after_rename):
            with self.assertRaisesRegex(RuntimeError, "forced schema inspection failure"):
                run_schema_scenario(self.connection)
        with self.connection.cursor() as cursor:
            self.assertEqual(fingerprint(cursor), original)
            self.assertEqual(
                inspect_schema(cursor, "raw.raw_orders").columns, original_schema.columns
            )
            cursor.execute("SELECT to_regclass('pg_temp.f01_orders')")
            self.assertIsNone(cursor.fetchone()[0])

    def test_database_denies_source_schema_change_under_demo_role(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE reliability_readonly")
            with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                cursor.execute(
                    "ALTER TABLE raw.raw_orders RENAME COLUMN amount TO forbidden_amount"
                )
        self.connection.rollback()

    def test_null_amounts_detected_and_recovered_reproducibly(self):
        first = run_null_scenario(self.connection)
        second = run_null_scenario(self.connection)
        self.assertEqual(first.affected_rows, (first.before.rows * 3 + 9) // 10)
        self.assertEqual(first.fault.null_amounts, first.affected_rows)
        self.assertEqual(first.observed_revenue_loss, first.expected_revenue_loss)
        self.assertGreater(first.observed_revenue_loss, 0)
        self.assertEqual(first.fault.null_amount_samples, second.fault.null_amount_samples)
        self.assertEqual(first.expected_revenue_loss, second.expected_revenue_loss)
        self.assertEqual(first.restored.null_amounts, 0)
        self.assertEqual(first.restored.revenue_usd, first.before.revenue_usd)
        self.assertEqual(first.source_fingerprint_before, first.source_fingerprint_after)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('pg_temp.f02_orders')")
            self.assertIsNone(cursor.fetchone()[0])

    def test_null_detection_counts_cancelled_but_excludes_cancelled_revenue(self):
        with self.connection.cursor() as cursor:
            cursor.execute(
                "CREATE TEMP TABLE f02_orders (order_id bigint, amount numeric, status text)"
            )
            cursor.execute("""INSERT INTO pg_temp.f02_orders VALUES
                (1, 12.34, 'completed'), (2, NULL, 'completed'),
                (3, NULL, 'cancelled'), (4, 999, 'cancelled')""")
            evidence = detect(cursor, "pg_temp", "f02_orders")
            self.assertEqual(str(evidence.revenue_usd), "12.34")
            self.assertEqual(evidence.null_amounts, 2)
            self.assertEqual(
                evidence.null_amount_samples,
                [{"order_id": 2, "status": "completed"}, {"order_id": 3, "status": "cancelled"}],
            )
        self.connection.rollback()

    def test_null_detector_failure_cleans_up_and_preserves_source(self):
        with self.connection.cursor() as cursor:
            original = fingerprint(cursor)
        self.connection.rollback()
        calls = 0

        def fail_after_injection(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("forced null detector failure")
            return detect(*args)

        with patch("scripts.null_fault.detect", side_effect=fail_after_injection):
            with self.assertRaisesRegex(RuntimeError, "forced null detector failure"):
                run_null_scenario(self.connection)
        with self.connection.cursor() as cursor:
            self.assertEqual(fingerprint(cursor), original)
            cursor.execute("SELECT to_regclass('pg_temp.f02_orders')")
            self.assertIsNone(cursor.fetchone()[0])

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
