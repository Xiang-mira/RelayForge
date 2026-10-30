"""Independent durable truth store, never a replacement for PostgreSQL Journal.

Each simulated request commits in its own SQLite transaction before returning.
There is no delayed asynchronous completion. Absence is usable only after the
calling worker is known to have stopped and all of its requests are quiescent.
"""

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


class KnownNoEffect(Exception):
    """Provider protocol guarantees rejection occurred before any mutation."""


class InjectedCrash(BaseException):
    """Simulates a process boundary, deliberately bypassing Exception handling."""


@dataclass(frozen=True)
class Evidence:
    outcome: str
    receipt: dict | None = None
    count: int | None = None


class MockExternal:
    def __init__(self, path: Path, *, supports_idempotency=True, supports_query=True):
        self.path = path
        self.supports_idempotency = supports_idempotency
        self.supports_query = supports_query
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS operations (
                external_id TEXT PRIMARY KEY, action_key TEXT NOT NULL,
                idempotency_key TEXT UNIQUE, payload_hash TEXT NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, action_key TEXT NOT NULL,
                payload_hash TEXT NOT NULL, fault TEXT NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS request_observations (
                request_id TEXT PRIMARY KEY, recorded_at_ns INTEGER NOT NULL,
                monotonic_ns INTEGER NOT NULL, idempotency_used INTEGER NOT NULL)""")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def execute(
        self, action_key: str, digest: str, idempotency_key: str | None, fault: str = "none"
    ) -> dict:
        with self._connect() as connection:
            request_id = str(uuid4())
            connection.execute(
                "INSERT INTO requests VALUES (?,?,?,?)", (request_id, action_key, digest, fault)
            )
            connection.execute(
                "INSERT INTO request_observations VALUES (?,?,?,?)",
                (
                    request_id,
                    time.time_ns(),
                    time.monotonic_ns(),
                    int(self.supports_idempotency and idempotency_key is not None),
                ),
            )
        if fault == "before_failure":
            raise KnownNoEffect("Mock rejected operation before mutation")
        key = idempotency_key if self.supports_idempotency else None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = None
            if key:
                existing = connection.execute(
                    "SELECT * FROM operations WHERE idempotency_key=?", (key,)
                ).fetchone()
            if existing and existing["payload_hash"] != digest:
                raise KnownNoEffect("Provider idempotency key input conflict")
            external_id = existing["external_id"] if existing else str(uuid4())
            if not existing:
                connection.execute(
                    "INSERT INTO operations VALUES (?,?,?,?)",
                    (external_id, action_key, key, digest),
                )
        # External commit has completed. These faults cannot roll it back.
        if fault == "response_lost":
            raise TimeoutError("Response lost after external commit")
        if fault == "after_external_before_receipt":
            raise InjectedCrash("Crash after external commit, before local receipt")
        return {"external_id": external_id, "provider": "local_mock"}

    def lookup(self, action_key: str, digest: str, *, quiescent: bool = False) -> Evidence:
        if not self.supports_query:
            return Evidence("indeterminate")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM operations WHERE action_key=?", (action_key,)
            ).fetchall()
        if len(rows) > 1 or any(row["payload_hash"] != digest for row in rows):
            # Multiple matching resources cannot identify one authoritative result.
            return Evidence("indeterminate", count=len(rows))
        if rows:
            return Evidence(
                "occurred",
                {"external_id": rows[0]["external_id"], "provider": "local_mock"},
                len(rows),
            )
        return Evidence("not_occurred" if quiescent else "indeterminate", count=0)

    def truth_count(self, action_key: str) -> int:
        # For independent test/evaluation observation, not a recovery oracle.
        with self._connect() as connection:
            return connection.execute(
                "SELECT count(*) FROM operations WHERE action_key=?", (action_key,)
            ).fetchone()[0]

    def request_count(self, action_key: str) -> int:
        with self._connect() as connection:
            return connection.execute(
                "SELECT count(*) FROM requests WHERE action_key=?", (action_key,)
            ).fetchone()[0]

    def request_history(self, action_key: str) -> list[dict]:
        """Independent evaluator observation, never a recovery interface."""
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    """
                SELECT r.rowid AS ordinal,r.request_id,r.payload_hash,r.fault,
                       o.recorded_at_ns,o.monotonic_ns,o.idempotency_used
                FROM requests r LEFT JOIN request_observations o ON o.request_id=r.request_id
                WHERE r.action_key=? ORDER BY r.rowid
            """,
                    (action_key,),
                ).fetchall()
            ]
