"""Local incident history with atomic transitions and idempotent observations."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

DEFAULT_STORE = Path(__file__).resolve().parents[1] / ".local/incidents.sqlite3"


class Observation(BaseModel):
    observation_id: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    asset: str = Field(min_length=1)
    check: str = Field(min_length=1)
    observed_at: datetime
    state: Literal["pass", "fail", "unknown"]
    evidence: dict

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Observation time must include a timezone")
        return value.astimezone(timezone.utc)


def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY, scope TEXT NOT NULL, asset TEXT NOT NULL, check_name TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('open', 'resolved')),
            opened_at TEXT NOT NULL, last_seen TEXT NOT NULL, resolved_at TEXT,
            occurrences INTEGER NOT NULL CHECK(occurrences > 0)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS one_open_incident
            ON incidents(scope, asset, check_name) WHERE status='open';
        CREATE TABLE IF NOT EXISTS observations (
            id TEXT PRIMARY KEY, scope TEXT NOT NULL, asset TEXT NOT NULL, check_name TEXT NOT NULL,
            observed_at TEXT NOT NULL, payload TEXT NOT NULL,
            incident_id TEXT REFERENCES incidents(id)
        );
        CREATE INDEX IF NOT EXISTS observation_key
            ON observations(scope, asset, check_name, observed_at);
    """)
    return db


def record(observations: list[Observation], path: Path = DEFAULT_STORE) -> None:
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        for item in observations:
            payload = json.dumps(
                item.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            )
            previous = db.execute(
                "SELECT payload FROM observations WHERE id=?", (item.observation_id,)
            ).fetchone()
            if previous:
                if previous["payload"] != payload:
                    raise ValueError("Observation ID already exists with different evidence")
                continue
            key = (item.scope, item.asset, item.check)
            stamp = item.observed_at.isoformat(timespec="microseconds")
            latest = db.execute(
                """SELECT max(observed_at) FROM observations
                WHERE scope=? AND asset=? AND check_name=?""",
                key,
            ).fetchone()[0]
            if latest and stamp <= latest:
                raise ValueError("New observation must be newer than the stored observation")
            active = db.execute(
                """SELECT id FROM incidents
                WHERE scope=? AND asset=? AND check_name=? AND status='open' """,
                key,
            ).fetchone()
            incident_id = active["id"] if active else None
            if item.state == "fail":
                if incident_id:
                    db.execute(
                        "UPDATE incidents SET last_seen=?, occurrences=occurrences+1 WHERE id=?",
                        (stamp, incident_id),
                    )
                else:
                    incident_id = str(uuid4())
                    db.execute(
                        "INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (incident_id, *key, "open", stamp, stamp, None, 1),
                    )
            elif item.state == "pass" and incident_id:
                db.execute(
                    "UPDATE incidents SET status='resolved', resolved_at=? WHERE id=?",
                    (stamp, incident_id),
                )
            # Unknown never closes an active incident or invents a data failure.
            db.execute(
                "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (item.observation_id, *key, stamp, payload, incident_id),
            )


def history(path: Path = DEFAULT_STORE) -> dict:
    with closing(connect(path)) as db:
        incidents = [
            dict(row) for row in db.execute("SELECT * FROM incidents ORDER BY opened_at, id")
        ]
        observations = [
            dict(row) for row in db.execute("SELECT * FROM observations ORDER BY observed_at, id")
        ]
        for row in observations:
            row["payload"] = json.loads(row["payload"])
        return {"incidents": incidents, "observations": observations}


def record_fault_report(report, path: Path = DEFAULT_STORE) -> None:
    """Replay measured stages, scoped to one sandbox session, never to the source database."""
    items = []
    for stage in ["before", "fault", "restored"]:
        evidence = getattr(report, stage)
        if report.scenario == "F01":
            expected = {c.name: (c.data_type, c.nullable) for c in report.before.columns}
            actual = {c.name: (c.data_type, c.nullable) for c in evidence.columns}
            failed = actual != expected
            check = "schema"
            details = {
                "schema": evidence.model_dump(mode="json"),
                "query_result": getattr(report, stage + "_query").model_dump(mode="json"),
            }
        else:
            check = "missing_amount" if report.scenario == "F02" else "duplicate_order"
            failed = (
                evidence.null_amounts > 0
                if report.scenario == "F02"
                else evidence.duplicate_excess > 0
            )
            details = evidence.model_dump(mode="json")
        items.append(
            Observation(
                observation_id=f"{report.run_id}:{stage}",
                scope=f"sandbox:{report.run_id}",
                asset=evidence.relation,
                check=check,
                observed_at=evidence.observed_at,
                state="fail" if failed else "pass",
                evidence=details,
            )
        )
    record(items, path)


if __name__ == "__main__":
    print(json.dumps(history(), ensure_ascii=False, indent=2))
