"""CI acceptance: confirm two real, persisted and successful full pipeline runs."""

import json
import os
from pathlib import Path

import dagster as dg

from scripts.incidents import history

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    paths = sorted((ROOT / ".local/runs").glob("*.json"))
    if len(paths) < 2:
        raise ValueError("Expected at least two completed pipeline run receipts")
    os.environ.setdefault("DAGSTER_HOME", str(ROOT / ".local/dagster"))
    required = {
        "source_quality",
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
    stored = {item["id"]: item for item in history()["observations"]}
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
            for check in ["schema", "missing_amount", "duplicate_order"]:
                item = stored.get(f"{receipt['run_id']}:{check}")
                if (
                    not item
                    or item["scope"] != "local-compose"
                    or item["payload"]["state"] != "pass"
                ):
                    raise ValueError("Run is missing successful persisted source checks")
    print(json.dumps({"event": "run_history_verified", "runs": len(paths)}))


if __name__ == "__main__":
    main()
