"""Verify the dashboard HTTP API against actual CI fault records."""

import json
from threading import Thread
from urllib.request import urlopen

from scripts.serve_dashboard import make_server


def main():
    server = make_server(port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def get(route):
        with urlopen(base + route, timeout=5) as response:
            return json.load(response)

    try:
        listing = get("/api/incidents?scope=sandbox&status=resolved")
        if not listing["available"] or listing["total"] != 3:
            raise ValueError("Expected three real recovered CI sandbox events")
        for incident in listing["items"]:
            detail = get("/api/incidents/" + incident["id"])
            states = [row["payload"]["state"] for row in detail["observations"]]
            if states != ["fail", "pass"]:
                raise ValueError(f"Missing fault/recovery evidence: {states}")
            if any(not row["payload"]["evidence"] for row in detail["observations"]):
                raise ValueError("Missing original evidence")
        if get("/api/incidents?scope=source")["total"] != 0:
            raise ValueError("Sandbox events leaked into source filter")
        print("Dashboard HTTP acceptance passed: three real CI fault/recovery histories")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
