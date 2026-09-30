"""CI acceptance: confirm two real, persisted and successful full pipeline runs."""

import json
import os
from pathlib import Path

import dagster as dg

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    paths = sorted((ROOT / ".local/runs").glob("*.json"))
    if len(paths) < 2:
        raise ValueError("Expected at least two completed pipeline run receipts")
    os.environ.setdefault("DAGSTER_HOME", str(ROOT / ".local/dagster"))
    required = {
        "raw/raw_customers",
        "raw/raw_products",
        "raw/raw_orders",
        "stg_customers",
        "stg_products",
        "stg_orders",
        "fact_sales",
        "daily_revenue",
        "regional_revenue",
        "executive_metrics",
        "verified_metrics",
    }
    with dg.DagsterInstance.get() as instance:
        for path in paths:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            run = instance.get_run_by_id(receipt["run_id"])
            if not run or run.status != dg.DagsterRunStatus.SUCCESS:
                raise ValueError("Dagster does not contain a successful persisted run")
            if receipt["status"] != "success" or receipt["failed_steps"]:
                raise ValueError("Run receipt is not successful")
            if set(receipt["materialized_assets"]) != required:
                raise ValueError("Pipeline did not materialize the complete expected asset graph")
            if not receipt["asset_checks"] or not all(c["passed"] for c in receipt["asset_checks"]):
                raise ValueError("dbt asset checks were missing or failed")
    print(json.dumps({"event": "run_history_verified", "runs": len(paths)}))


if __name__ == "__main__":
    main()
