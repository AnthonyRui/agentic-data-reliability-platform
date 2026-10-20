"""CI acceptance for persisted events from real F01/F02/F03 reports."""

import json
from pathlib import Path

from scripts.incidents import history, record_fault_report
from scripts.null_fault import NullFaultReport
from scripts.run_fault_demo import FaultReport
from scripts.schema_fault import SchemaFaultReport


def main():
    before = history()
    incident_rows = before["incidents"]
    if len(incident_rows) != 3 or any(i["status"] != "resolved" for i in incident_rows):
        raise ValueError("Expected three recovered sandbox incidents")
    if {i["check_name"] for i in incident_rows} != {"schema", "missing_amount", "duplicate_order"}:
        raise ValueError("Missing expected scenario checks")
    if any(i["occurrences"] != 1 or not i["scope"].startswith("sandbox:") for i in incident_rows):
        raise ValueError("Unexpected incident scope or duplicate occurrences")
    models = {"F01": SchemaFaultReport, "F02": NullFaultReport, "F03": FaultReport}
    paths = list((Path(__file__).resolve().parents[1] / ".local/fault-runs").glob("*.json"))
    if len(paths) != 3:
        raise ValueError("Expected three actual CI reports")
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        record_fault_report(models[data["scenario"]].model_validate(data))
    if history() != before:
        raise ValueError("Replay changed persisted history")
    source = [o for o in before["observations"] if o["scope"] == "local-compose"]
    if len(source) < 6 or any(o["payload"]["state"] != "pass" for o in source):
        raise ValueError("Expected successful repeated real source monitoring")
    print(
        json.dumps(
            {
                "event": "incident_history_verified",
                "resolved_sandbox_incidents": 3,
                "source_observations": len(source),
                "replay_idempotent": True,
            }
        )
    )


if __name__ == "__main__":
    main()
