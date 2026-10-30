import hashlib
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts.incidents import Observation, history, record
from scripts.serve_dashboard import make_server


class DashboardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "incidents.sqlite3"
        self.server = make_server(self.path, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def get(self, route):
        with urlopen(self.url + route, timeout=5) as response:
            return json.load(response)

    def seed(self):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        items = [
            Observation(
                observation_id=f"source:{i}",
                scope="local-compose",
                asset="raw.raw_orders",
                check="duplicate_order",
                observed_at=base + timedelta(seconds=i),
                state="fail" if i == 0 else "unknown",
                evidence={"fixture": True, "sql": "<script>alert(1)</script>"},
            )
            for i in range(55)
        ]
        items += [
            Observation(
                observation_id=f"sandbox:{i}",
                scope="sandbox:test",
                asset="pg_temp.orders",
                check="missing_amount",
                observed_at=base + timedelta(seconds=i),
                state="fail" if i == 0 else "pass",
                evidence={"fixture": True},
            )
            for i in range(2)
        ]
        record(items, self.path)

    def test_empty_store_does_not_create_database(self):
        data = self.get("/api/incidents")
        self.assertFalse(data["available"])
        self.assertEqual(data["items"], [])
        self.assertFalse(self.path.exists())

    def test_filters_details_pagination_and_unchanged_database(self):
        self.seed()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        data = self.get("/api/incidents?scope=source&status=open")
        self.assertEqual(data["total"], 1)
        identity = data["items"][0]["id"]
        first = self.get("/api/incidents/" + identity)
        second = self.get("/api/incidents/" + identity + "?offset=50")
        self.assertEqual(first["total"], 55)
        self.assertEqual(len(first["observations"]), 50)
        self.assertEqual(len(second["observations"]), 5)
        self.assertEqual(
            first["observations"][0]["payload"]["evidence"]["sql"], "<script>alert(1)</script>"
        )
        self.assertEqual(self.get("/api/incidents?scope=sandbox&status=resolved")["total"], 1)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertEqual(len(history(self.path)["incidents"]), 2)

    def test_rejects_invalid_requests_and_writes(self):
        for route, code in [
            ("/api/incidents?offset=-1", 400),
            ("/api/incidents?scope=invalid", 400),
            ("/api/incidents/not-present", 404),
            ("/../.env", 404),
        ]:
            with self.assertRaises(HTTPError) as error:
                self.get(route)
            self.assertEqual(error.exception.code, code)
        for request in [
            Request(self.url + "/api/incidents", method="POST"),
            Request(self.url + "/", headers={"Host": "untrusted.example"}),
        ]:
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=5)
            self.assertIn(error.exception.code, [403, 501])

    def test_database_errors_are_not_reported_as_empty_healthy_state(self):
        self.path.write_text("not a database", encoding="utf-8")
        with self.assertRaises(HTTPError) as error:
            self.get("/api/incidents")
        self.assertEqual(error.exception.code, 503)
        self.assertIn("unavailable", error.exception.read().decode())

    def test_serves_static_assets_with_content_security_policy(self):
        for path in ["/", "/app.js", "/style.css"]:
            with urlopen(self.url + path, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
                self.assertTrue(response.read())
