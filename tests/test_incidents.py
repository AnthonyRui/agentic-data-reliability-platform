import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.incidents import Observation, history, record


class IncidentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "incidents.sqlite3"

    def observation(self, second, state, scope="source", check="duplicates"):
        return Observation(
            observation_id=f"{scope}:{check}:{second}",
            scope=scope,
            asset="raw.orders",
            check=check,
            observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=second),
            state=state,
            evidence={"sql": "test fixture", "count": 1 if state == "fail" else 0},
        )

    def test_repeat_failure_unknown_recovery_and_recurrence(self):
        items = [
            self.observation(0, "pass"),
            self.observation(1, "fail"),
            self.observation(2, "fail"),
            self.observation(3, "unknown"),
        ]
        record(items, self.path)
        record(items, self.path)
        result = history(self.path)
        self.assertEqual(len(result["observations"]), 4)
        self.assertEqual(len(result["incidents"]), 1)
        self.assertEqual(result["incidents"][0]["status"], "open")
        self.assertEqual(result["incidents"][0]["occurrences"], 2)
        record([self.observation(4, "pass"), self.observation(5, "fail")], self.path)
        self.assertEqual(
            [i["status"] for i in history(self.path)["incidents"]], ["resolved", "open"]
        )

    def test_other_scope_or_check_cannot_resolve_source(self):
        record(
            [
                self.observation(1, "fail"),
                self.observation(2, "pass", scope="sandbox"),
                self.observation(3, "pass", check="amount"),
            ],
            self.path,
        )
        self.assertEqual(history(self.path)["incidents"][0]["status"], "open")

    def test_conflicting_replay_and_stale_recovery_roll_back_batch(self):
        item = self.observation(2, "fail")
        record([item], self.path)
        for invalid in [item.model_copy(update={"state": "pass"}), self.observation(1, "pass")]:
            with self.assertRaises(ValueError):
                record([self.observation(4, "fail", check="another"), invalid], self.path)
        result = history(self.path)
        self.assertEqual(len(result["incidents"]), 1)
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["incidents"][0]["status"], "open")

    def test_concurrent_identical_observations_create_one_incident(self):
        record([], self.path)
        item = self.observation(1, "fail")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: record([item], self.path), range(8)))
        self.assertEqual(len(history(self.path)["incidents"]), 1)
        self.assertEqual(history(self.path)["incidents"][0]["occurrences"], 1)

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            Observation(
                observation_id="bad",
                scope="source",
                asset="orders",
                check="null",
                observed_at=datetime(2026, 1, 1),
                state="pass",
                evidence={},
            )
