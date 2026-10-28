import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import dagster as dg
import psycopg2

from dagster_project.assets import source_quality
from scripts.incidents import Observation, history
from scripts.monitor import CHECKS, run_monitor


class MonitorJobTests(unittest.TestCase):
    def test_failure_unknown_recovery_and_duplicate_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "incidents.sqlite3"
            called = []

            @dg.asset(deps=[source_quality])
            def downstream():
                called.append(True)

            with dg.DagsterInstance.ephemeral() as instance:
                for state in ["fail", "fail", "unknown", "pass"]:

                    def observations(connection, run_id):
                        return [
                            Observation(
                                observation_id=f"{run_id}:{check}",
                                scope="local-compose",
                                asset="raw.raw_orders",
                                check=check,
                                observed_at=datetime.now(timezone.utc),
                                state=state if check == "duplicate_order" else "pass",
                                evidence={"fixture": True, "test": "job transition"},
                            )
                            for check in CHECKS
                        ]

                    def invoke(run_id):
                        return run_monitor(run_id, path)

                    with (
                        patch("scripts.monitor.prepare_environment"),
                        patch.dict(os.environ, {"DBT_ENV_SECRET_POSTGRES_PASSWORD": "test-only"}),
                        patch("scripts.monitor.psycopg2.connect", return_value=MagicMock()),
                        patch("scripts.monitor.collect", side_effect=observations),
                        patch("dagster_project.assets.run_monitor", side_effect=invoke),
                    ):
                        result = dg.materialize(
                            [source_quality, downstream], instance=instance, raise_on_error=False
                        )
                    self.assertEqual(result.success, state == "pass")
                    self.assertEqual(
                        instance.get_run_by_id(result.run_id).status,
                        dg.DagsterRunStatus.SUCCESS
                        if state == "pass"
                        else dg.DagsterRunStatus.FAILURE,
                    )
                    saved = history(path)
                    self.assertEqual(len(saved["incidents"]), 1)
                    self.assertEqual(
                        saved["incidents"][0]["status"], "resolved" if state == "pass" else "open"
                    )
                    self.assertEqual(len(called), 1 if state == "pass" else 0)
                self.assertEqual(saved["incidents"][0]["occurrences"], 2)
                self.assertEqual(len(saved["observations"]), 12)

    def test_connection_failure_persists_unknown_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "incidents.sqlite3"
            with (
                patch("scripts.monitor.prepare_environment"),
                patch.dict(os.environ, {"DBT_ENV_SECRET_POSTGRES_PASSWORD": "test-only"}),
                patch(
                    "scripts.monitor.psycopg2.connect",
                    side_effect=psycopg2.OperationalError("unavailable"),
                ),
            ):
                items = run_monitor("failed-connect", path)
            self.assertEqual(len(items), 3)
            self.assertTrue(all(item.state == "unknown" for item in items))
            self.assertEqual(len(history(path)["observations"]), 3)
            self.assertEqual(history(path)["incidents"], [])

    def test_incomplete_checks_block_downstream(self):
        with patch("dagster_project.assets.run_monitor", return_value=[]):
            result = dg.materialize([source_quality], raise_on_error=False)
        self.assertFalse(result.success)
        self.assertEqual(result.get_asset_materialization_events(), [])
