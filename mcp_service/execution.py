"""Persist queue requests before submission so reconnects cannot duplicate a run."""

import asyncio
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path


class JobLedger:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            request_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
            status TEXT NOT NULL, result TEXT, created REAL NOT NULL)""")
        self.db.commit()
        self.lock = asyncio.Lock()

    def close(self):
        self.db.close()

    def get(self, request_id):
        row = self.db.execute("SELECT status,result FROM jobs WHERE request_id=?", (request_id,)).fetchone()
        if not row:
            return None
        return {"request_id": request_id, "submission_status": row[0],
                "result": json.loads(row[1]) if row[1] else None}

    async def submit(self, client, request_id, prompt, workflow=None, client_id=None):
        if str(uuid.UUID(request_id)) != request_id:
            raise ValueError("request_id must be a canonical UUID. Reuse it only for the same run.")
        arguments = {"prompt": prompt, "workflow": workflow}
        if client_id is not None:
            arguments["client_id"] = client_id
        payload = json.dumps(arguments, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()
        digest = hashlib.sha256(payload).hexdigest()
        async with self.lock:
            with self.db:
                cursor = self.db.execute(
                    "INSERT OR IGNORE INTO jobs VALUES (?,?,'submitting',NULL,?)",
                    (request_id, digest, time.time()),
                )
            if cursor.rowcount == 0:
                row = self.db.execute("SELECT payload_hash,status,result FROM jobs WHERE request_id=?",
                                      (request_id,)).fetchone()
                if row[0] != digest:
                    raise ValueError("This request_id was already used with a different workflow.")
                if row[1] == "accepted":
                    return {**json.loads(row[2]), "reused_request": True}
                observed = await client.job(request_id)
                if observed.get("status") not in {"unknown", "not_found", None}:
                    result = {"prompt_id": request_id, "recovered": True, "job": observed}
                    self._finish(request_id, "accepted", result)
                    return result
                return {"prompt_id": request_id, "submission_status": row[1],
                        "resubmitted": False,
                        "message": "Previous submission was not confirmed. Check ComfyUI queue/history before starting a new request."}
            try:
                options = {"client_id": client_id} if client_id is not None else {}
                result = await client.queue(prompt, workflow=workflow, request_id=request_id, **options)
            except BaseException:
                self._finish(request_id, "unconfirmed", None)
                raise
            self._finish(request_id, "accepted", result)
            return result

    def _finish(self, request_id, status, result):
        with self.db:
            self.db.execute("UPDATE jobs SET status=?,result=? WHERE request_id=?",
                            (status, json.dumps(result) if result is not None else None, request_id))
