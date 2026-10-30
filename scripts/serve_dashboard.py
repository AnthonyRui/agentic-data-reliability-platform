"""Serve a loopback-only, read-only incident dashboard."""

import argparse
import json
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from scripts.incidents import DEFAULT_STORE

WEB = Path(__file__).resolve().parents[1] / "apps/dashboard"
STATIC = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
}


def read_api(path: Path, route: str, query: dict) -> tuple[int, dict]:
    scope = query.get("scope", ["all"])[0]
    status = query.get("status", ["all"])[0]
    if scope not in {"all", "source", "sandbox"} or status not in {"all", "open", "resolved"}:
        return 400, {"error": "Invalid filter"}
    try:
        offset = int(query.get("offset", ["0"])[0])
    except ValueError:
        return 400, {"error": "Invalid offset"}
    if not 0 <= offset <= 1_000_000:
        return 400, {"error": "Invalid offset"}
    if not path.exists():
        if route != "/api/incidents":
            return 404, {"error": "No incident store yet"}
        return 200, {"available": False, "items": [], "total": 0, "offset": offset, "limit": 50}
    # mode=ro never creates or migrates the incident database.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        if route == "/api/incidents":
            clauses, params = [], []
            if scope != "all":
                clauses.append(
                    "scope LIKE 'sandbox:%'" if scope == "sandbox" else "scope='local-compose'"
                )
            if status != "all":
                clauses.append("status=?")
                params.append(status)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            total = db.execute("SELECT count(*) FROM incidents" + where, params).fetchone()[0]
            rows = db.execute(
                "SELECT * FROM incidents"
                + where
                + " ORDER BY opened_at DESC, id LIMIT 50 OFFSET ?",
                [*params, offset],
            ).fetchall()
            return 200, {
                "available": True,
                "items": [dict(r) for r in rows],
                "total": total,
                "offset": offset,
                "limit": 50,
            }
        if route.startswith("/api/incidents/"):
            identity = route.removeprefix("/api/incidents/")
            incident = db.execute("SELECT * FROM incidents WHERE id=?", (identity,)).fetchone()
            if not incident:
                return 404, {"error": "Incident not found"}
            rows = db.execute(
                """SELECT id, observed_at, payload FROM observations
                WHERE incident_id=? ORDER BY observed_at, id LIMIT 50 OFFSET ?""",
                (identity, offset),
            ).fetchall()
            count = db.execute(
                "SELECT count(*) FROM observations WHERE incident_id=?", (identity,)
            ).fetchone()[0]
            return 200, {
                "incident": dict(incident),
                "observations": [
                    {
                        "id": r["id"],
                        "observed_at": r["observed_at"],
                        "payload": json.loads(r["payload"]),
                    }
                    for r in rows
                ],
                "total": count,
                "offset": offset,
                "limit": 50,
            }
    return 404, {"error": "Not found"}


def make_server(path: Path = DEFAULT_STORE, port: int = 8010) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, content, kind):
            self.send_response(status)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; object-src 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            allowed = {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }
            if self.headers.get("Host") not in allowed:
                self.respond(403, b"Invalid Host", "text/plain")
                return
            parsed = urlsplit(self.path)
            if parsed.path in STATIC:
                filename, kind = STATIC[parsed.path]
                self.respond(200, (WEB / filename).read_bytes(), kind)
                return
            try:
                status, result = read_api(path, parsed.path, parse_qs(parsed.query))
            except (sqlite3.Error, ValueError, OSError):
                status, result = (
                    503,
                    {"error": "Incident store unavailable; no health conclusion can be drawn"},
                )
            self.respond(
                status, json.dumps(result, ensure_ascii=False).encode("utf-8"), "application/json"
            )

        def log_message(self, format, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    with make_server(port=args.port) as server:
        print(f"Incident dashboard: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
