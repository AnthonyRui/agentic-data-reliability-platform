import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dagster as dg

from dagster_project.assets import validated_sources
from data.generator.generate import Config, generate
from scripts.run_pipeline import ensure_dataset, pipeline_lock


class PipelineTests(unittest.TestCase):
    def test_existing_seed_is_reused_and_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "seed"
            generate(path, Config(months=1, daily_orders=2))
            before = (path / "raw_orders.csv").read_bytes()
            ensure_dataset(path)
            self.assertEqual((path / "raw_orders.csv").read_bytes(), before)
            (path / "raw_orders.csv").write_bytes(before + b"tampered\n")
            with self.assertRaisesRegex(ValueError, "changed"):
                ensure_dataset(path)

    def test_lock_rejects_overlap_and_releases_after_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lock"
            with self.assertRaisesRegex(ValueError, "failure"):
                with pipeline_lock(path):
                    with self.assertRaises(FileExistsError):
                        with pipeline_lock(path):
                            self.fail("overlapping run entered")
                    raise ValueError("failure")
            self.assertFalse(path.exists())

    def test_bad_source_blocks_downstream_and_records_failed_run(self):
        called = []

        @dg.asset(deps=[dg.AssetKey(["raw", "raw_orders"])])
        def downstream():
            called.append(True)

        with patch("dagster_project.assets.verify_raw", side_effect=ValueError("bad source")):
            result = dg.materialize([validated_sources, downstream], raise_on_error=False)
        self.assertFalse(result.success)
        self.assertEqual(called, [])
        self.assertEqual(len(result.get_asset_materialization_events()), 0)
        self.assertTrue(result.get_step_failure_events())
