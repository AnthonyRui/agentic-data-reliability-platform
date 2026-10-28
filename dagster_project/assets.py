import os
import sysconfig
from pathlib import Path

import dagster as dg
from dagster_dbt import DbtCliResource, dbt_assets

from scripts.monitor import CHECKS, run_monitor
from scripts.verify_database import verify as verify_raw
from scripts.verify_models import verify as verify_metrics

PROJECT = Path(__file__).resolve().parents[1] / "dbt_project"
RAW_TABLES = ("raw_customers", "raw_products", "raw_orders")


@dg.asset(group_name="source_validation")
def source_quality(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Persist actual source observations before allowing any warehouse computation."""
    items = run_monitor(run_id=context.run_id)
    metadata = {
        "checks": {item.check: item.state for item in items},
        "observation_ids": [item.observation_id for item in items],
    }
    if len(items) != len(CHECKS) or {item.check for item in items} != set(CHECKS):
        raise dg.Failure("Source monitoring returned incomplete checks", metadata=metadata)
    if any(item.state != "pass" for item in items):
        raise dg.Failure(
            "Source checks failed or are unavailable; downstream stopped", metadata=metadata
        )
    return dg.MaterializeResult(metadata=metadata)


@dg.multi_asset(
    outs={name: dg.AssetOut(key=dg.AssetKey(["raw", name])) for name in RAW_TABLES},
    group_name="source_validation",
    deps=[source_quality],
)
def validated_sources():
    """Record healthy sources only after real database validation succeeds."""
    result = verify_raw()
    for name in RAW_TABLES:
        yield dg.Output(None, output_name=name, metadata={"rows": result["row_counts"][name]})


@dg.asset(
    deps=["daily_revenue", "regional_revenue", "executive_metrics"],
    group_name="baseline_validation",
)
def verified_metrics() -> dg.MaterializeResult:
    result = verify_metrics()
    return dg.MaterializeResult(metadata=result)


def build_definitions() -> dg.Definitions:
    manifest = PROJECT / "target/manifest.json"
    if not manifest.exists():
        raise FileNotFoundError("Run python -m scripts.run_dbt parse before loading Dagster")

    @dbt_assets(manifest=manifest)
    def sales_models(context: dg.AssetExecutionContext, dbt: DbtCliResource):
        yield from dbt.cli(["build"], context=context).stream()

    return dg.Definitions(
        assets=[source_quality, validated_sources, sales_models, verified_metrics],
        jobs=[dg.define_asset_job("healthy_pipeline", selection=dg.AssetSelection.all())],
        resources={
            "dbt": DbtCliResource(
                project_dir=PROJECT,
                profiles_dir=PROJECT,
                dbt_executable=str(
                    Path(sysconfig.get_path("scripts")) / ("dbt.exe" if os.name == "nt" else "dbt")
                ),
            )
        },
    )
