"""Run the project's fixed dbt profile; credentials stay in environment variables."""

import argparse
import os
from pathlib import Path

from dbt.cli.main import dbtRunner
from dotenv import load_dotenv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["parse", "build", "test", "compile"], default="build", nargs="?"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=False)
    password = os.environ.get("DBT_ENV_SECRET_POSTGRES_PASSWORD") or os.environ.get(
        "POSTGRES_PASSWORD"
    )
    if not password:
        parser.error(
            "Set POSTGRES_PASSWORD in .env or DBT_ENV_SECRET_POSTGRES_PASSWORD in the environment"
        )
    os.environ["DBT_ENV_SECRET_POSTGRES_PASSWORD"] = password
    os.environ["DBT_SEND_ANONYMOUS_USAGE_STATS"] = "false"
    project = str(root / "dbt_project")
    result = dbtRunner().invoke([args.command, "--project-dir", project, "--profiles-dir", project])
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
