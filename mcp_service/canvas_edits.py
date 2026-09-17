"""Retain prepared canvas edits so a lost response can be retried unchanged."""

import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .workflows import apply_patch


class CanvasEdits:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("""CREATE TABLE IF NOT EXISTS canvas_edits (
            id TEXT PRIMARY KEY, digest TEXT NOT NULL, payload TEXT, result TEXT, created REAL NOT NULL)""")
        self.db.execute("CREATE TABLE IF NOT EXISTS canvas_runs (id TEXT PRIMARY KEY, digest TEXT NOT NULL, payload TEXT NOT NULL)")
        self.db.commit()
        self.lock = asyncio.Lock()

    def close(self):
        self.db.close()

    async def prepare_run(self, client, session_id, expected_revision, request_id):
        if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
            raise ValueError("Use a canonical UUID request_id for the intended canvas run.")
        digest = hashlib.sha256(json.dumps([session_id, expected_revision]).encode()).hexdigest()
        async with self.lock:
            row = self.db.execute("SELECT digest,payload FROM canvas_runs WHERE id=?", (request_id,)).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError("This run id was already used for a different canvas/revision.")
                return json.loads(row[1])
            snapshot = await client.bridge("canvas", {"session_id": session_id, "command": "read", "request_id": str(uuid.uuid4())})
            if snapshot["revision"] != expected_revision:
                raise ValueError("Canvas changed. Read it again before running.")
            sessions = (await client.bridge("sessions"))["sessions"]
            session = next((item for item in sessions if item["session_id"] == session_id), None)
            if session is None or session.get("state", "connected") != "connected":
                raise ValueError("The selected canvas is no longer shared/connected.")
            payload = {"prompt": snapshot["prompt"], "workflow": snapshot["workflow"], "client_id": session["client_id"]}
            with self.db:
                inserted = self.db.execute("INSERT OR IGNORE INTO canvas_runs VALUES (?,?,?)", (request_id, digest, json.dumps(payload)))
            if not inserted.rowcount:
                row = self.db.execute("SELECT digest,payload FROM canvas_runs WHERE id=?", (request_id,)).fetchone()
                if row[0] != digest:
                    raise ValueError("Another caller used this run id for a different canvas.")
                return json.loads(row[1])
            return payload

    async def apply(self, client, session_id, request_id, command, expected_revision, *, workflow=None, operations=None):
        if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
            raise ValueError("Use a canonical UUID request_id; reuse it only for an identical edit.")
        if command not in {"apply", "patch", "undo"}:
            raise ValueError("Unknown canvas edit.")
        original = {"session_id": session_id, "command": command, "expected_revision": expected_revision,
                    "workflow": workflow, "operations": operations}
        digest = hashlib.sha256(json.dumps(original, sort_keys=True, allow_nan=False).encode()).hexdigest()
        async with self.lock:
            row = self.db.execute("SELECT digest,payload,result FROM canvas_edits WHERE id=?", (request_id,)).fetchone()
            if row and row[0] != digest:
                raise ValueError("This request_id was already used for a different canvas edit.")
            if row and row[2]:
                return {**json.loads(row[2]), "reused_request": True}
            payload = json.loads(row[1]) if row and row[1] else None
            if payload is None:
                if command == "patch":
                    snapshot = await client.bridge("canvas", {"session_id": session_id, "command": "read",
                                                               "request_id": str(uuid.uuid4())})
                    if snapshot["revision"] != expected_revision:
                        raise ValueError("Canvas changed. Read it again before preparing an edit.")
                    workflow = apply_patch(snapshot["workflow"], operations)
                payload = {"session_id": session_id, "command": "undo" if command == "undo" else "apply",
                           "expected_revision": expected_revision, "request_id": request_id}
                if command != "undo":
                    payload["workflow"] = workflow
                with self.db:
                    inserted = self.db.execute("INSERT OR IGNORE INTO canvas_edits VALUES (?,?,?,NULL,?)",
                                               (request_id, digest, json.dumps(payload, allow_nan=False), time.time()))
                if inserted.rowcount == 0:
                    # Another gateway prepared this id while the browser read was pending.
                    row = self.db.execute("SELECT digest,payload,result FROM canvas_edits WHERE id=?", (request_id,)).fetchone()
                    if row[0] != digest:
                        raise ValueError("This request_id belongs to a different edit.")
                    if row[2]:
                        return {**json.loads(row[2]), "reused_request": True}
                    payload = json.loads(row[1])
            result = await client.bridge("canvas", payload)
            with self.db:
                self.db.execute("UPDATE canvas_edits SET result=? WHERE id=?", (json.dumps(result), request_id))
            return result
