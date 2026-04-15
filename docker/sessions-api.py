#!/usr/bin/env python3
"""Minimal sessions REST API — lit le SQLite Hermes, sert JSON sur port 9119."""

import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

HERMES_HOME = os.environ.get("HERMES_HOME", "/opt/data")
DB_PATH = os.path.join(HERMES_HOME, "state.db")
API_KEY = os.environ.get("API_SERVER_KEY", "")
PORT = int(os.environ.get("SESSIONS_API_PORT", "9119"))


def get_db():
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # Silence les logs d'accès

    def auth_ok(self):
        if not API_KEY:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {API_KEY}"

    def send_json(self, data, status=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.auth_ok():
            self.send_json({"error": "Unauthorized"}, 401)
            return

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        path = parsed.path.rstrip("/")
        db = get_db()

        try:
            # GET /sessions?limit=20&offset=0&q=search
            if path == "/sessions":
                limit = int(params.get("limit", ["20"])[0])
                offset = int(params.get("offset", ["0"])[0])
                q = params.get("q", [""])[0].strip()

                if q:
                    rows = db.execute("""
                        SELECT DISTINCT s.* FROM sessions s
                        JOIN messages m ON m.session_id = s.id
                        WHERE m.content LIKE ?
                        ORDER BY s.started_at DESC LIMIT ? OFFSET ?
                    """, (f"%{q}%", limit, offset)).fetchall()
                else:
                    rows = db.execute(
                        "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ? OFFSET ?",
                        (limit, offset)
                    ).fetchall()

                total = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
                self.send_json({"sessions": [dict(r) for r in rows], "total": total})

            # GET /sessions/{id}/messages
            elif path.startswith("/sessions/") and path.endswith("/messages"):
                session_id = path.split("/")[2]
                session = db.execute(
                    "SELECT * FROM sessions WHERE id=?", (session_id,)
                ).fetchone()
                if not session:
                    self.send_json({"error": "Not found"}, 404)
                    return
                msgs = db.execute(
                    "SELECT * FROM messages WHERE session_id=? ORDER BY timestamp",
                    (session_id,)
                ).fetchall()
                self.send_json({"session": dict(session), "messages": [dict(m) for m in msgs]})

            else:
                self.send_json({"error": "Not found"}, 404)

        except Exception as e:
            self.send_json({"error": str(e)}, 500)
        finally:
            db.close()


if __name__ == "__main__":
    print(f"[sessions-api] Starting on port {PORT}, db={DB_PATH}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
