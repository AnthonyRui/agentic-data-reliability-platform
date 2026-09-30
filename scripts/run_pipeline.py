"""Start and verify the local warehouse through a persistent Dagster run."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from data.generator.generate import Config, generate

ROOT = Path(__file__).resolve().parents[1]
FILES = ("raw_customers.csv", "raw_products.csv", "raw_orders.csv", "daily_baseline.csv")


def ensure_dataset(output: Path) -> None:
    if not output.exists():
        generate(output, Config())
        return
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("synthetic") is not True or manifest.get("format_version") != 1:
        raise ValueError("Dataset is not a supported synthetic baseline")
    for filename in FILES:
        checksum = hashlib.sha256((output / filename).read_bytes()).hexdigest()
        if checksum != manifest["sha256"].get(filename):
            raise ValueError(f"Baseline file changed: {filename}; refusing to overwrite it")


@contextmanager
def pipeline_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink()


def command(args: list[str], timeout: int = 180) -> None:
    subprocess.run(args, cwd=ROOT, check=True, timeout=timeout)


def execute(check_only: bool = False) -> int:
    import dagster as dg

    from dagster_project.assets import build_definitions
    from scripts.run_dbt import prepare_environment

    prepare_environment()
    # Compose and dbt must use the same password; never put it on a command line.
    os.environ["POSTGRES_PASSWORD"] = os.environ["DBT_ENV_SECRET_POSTGRES_PASSWORD"]
    with pipeline_lock(ROOT / ".local/pipeline.lock"):
        if not check_only:
            command(["docker", "compose", "version"], timeout=30)
            ensure_dataset(ROOT / "data/generated")
            command(["docker", "compose", "up", "-d", "--wait", "--wait-timeout", "150"])
            command(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    "reliability_admin",
                    "-d",
                    "reliability",
                    "-f",
                    "/docker-entrypoint-initdb.d/002_transform.sql",
                ]
            )
        command([sys.executable, "-m", "scripts.run_dbt", "parse"])
        defs = build_definitions()
        dg.Definitions.validate_loadable(defs)
        job = defs.resolve_job_def("healthy_pipeline")
        if check_only:
            print(json.dumps({"event": "definitions_validated", "job": job.name}))
            return 0

        home = Path(os.environ.get("DAGSTER_HOME", ROOT / ".local/dagster")).resolve()
        home.mkdir(parents=True, exist_ok=True)
        config = home / "dagster.yaml"
        if not config.exists():
            config.write_text("telemetry:\n  enabled: false\n", encoding="utf-8")
        os.environ["DAGSTER_HOME"] = str(home)
        started = datetime.now(timezone.utc).isoformat()
        with dg.DagsterInstance.get() as instance:
            result = job.execute_in_process(instance=instance, raise_on_error=False)
            receipt = {
                "run_id": result.run_id,
                "status": "success" if result.success else "failure",
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "materialized_assets": sorted(
                    event.asset_key.to_user_string()
                    for event in result.get_asset_materialization_events()
                ),
                "failed_steps": [event.step_key for event in result.get_step_failure_events()],
                "asset_checks": [
                    {
                        "asset": check.asset_key.to_user_string(),
                        "name": check.check_name,
                        "passed": check.passed,
                    }
                    for check in result.get_asset_check_evaluations()
                ],
            }
            destination = ROOT / ".local/runs"
            destination.mkdir(parents=True, exist_ok=True)
            (destination / f"{result.run_id}.json").write_text(
                json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(receipt))
            return 0 if result.success else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Parse and validate definitions without Docker"
    )
    args = parser.parse_args()
    try:
        return execute(args.check)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # Bootstrap failures happen before a Dagster run and must not be labelled successful.
        print(
            json.dumps({"event": "pipeline_bootstrap_failed", "error_type": type(exc).__name__}),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
