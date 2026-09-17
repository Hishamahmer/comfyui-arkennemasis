"""Explicit long-running maintenance operations with durable retry identity."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid


class Operations:
    def __init__(self, state_dir):
        path = Path(state_dir) / "operations.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("""CREATE TABLE IF NOT EXISTS operations (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, scope TEXT NOT NULL, digest TEXT NOT NULL,
            state TEXT NOT NULL, result TEXT, created REAL NOT NULL, updated REAL NOT NULL)""")
        self.db.commit()
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ark-mcp-operation")
        self.futures = {}

    def get(self, request_id):
        with self.lock:
            row = self.db.execute("SELECT kind,scope,state,result,created,updated FROM operations WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown operation id.")
        state = row[2]
        if state in {"queued", "running"} and request_id not in self.futures:
            state = "unconfirmed"
        return {"request_id": request_id, "kind": row[0], "required_scope": row[1], "state": state,
                "result": json.loads(row[3]) if row[3] else None, "created_at": row[4], "updated_at": row[5],
                "note": "Unconfirmed work is never automatically resubmitted; inspect its target before a new request." if state == "unconfirmed" else ""}

    def submit(self, kind, scope, request_id, arguments, function):
        if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
            raise ValueError("Use a canonical UUID request_id and reuse it only for identical work.")
        digest = hashlib.sha256(json.dumps([kind, scope, arguments], sort_keys=True, allow_nan=False).encode()).hexdigest()
        with self.lock:
            row = self.db.execute("SELECT digest FROM operations WHERE id=?", (request_id,)).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError("This operation id was used with different arguments.")
                return self.get(request_id)
            if sum(not future.done() for future in self.futures.values()) >= 8:
                raise ValueError("Eight operations are already pending. Wait for one to finish.")
            with self.db:
                self.db.execute("INSERT INTO operations VALUES (?,?,?,?,'queued',NULL,?,?)",
                                (request_id, kind, scope, digest, time.time(), time.time()))
            self.futures[request_id] = self.pool.submit(self._run, request_id, function)
            return self.get(request_id)

    def _finish(self, request_id, state, result=None):
        with self.lock, self.db:
            self.db.execute("UPDATE operations SET state=?,result=?,updated=? WHERE id=?",
                            (state, json.dumps(result, allow_nan=False), time.time(), request_id))

    def _run(self, request_id, function):
        self._finish(request_id, "running")
        try:
            result = function()
            self._finish(request_id, "completed", result)
        except Exception as exc:
            # Domain modules return safe diagnostics; unexpected exceptions reveal no paths/credentials.
            self._finish(request_id, "failed", {"error": type(exc).__name__,
                                               "message": str(exc)[:2000] if isinstance(exc, ValueError) else "Operation failed. Inspect local diagnostics."})

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.db.close()
