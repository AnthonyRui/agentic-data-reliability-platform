"""Load with dagster dev -m dagster_project.definitions after dbt parse."""

from dagster_project.assets import build_definitions
from scripts.run_dbt import prepare_environment

prepare_environment()
defs = build_definitions()
