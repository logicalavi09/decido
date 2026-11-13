from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.core.errors import InjectedCommitFailure

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    id                 TEXT PRIMARY KEY,
    workflow_name      TEXT NOT NULL,
    workflow_version   TEXT NOT NULL,
    workflow_hash      TEXT NOT NULL,
    workflow_snapshot  TEXT NOT NULL,
    idempotency_key    TEXT NOT NULL UNIQUE,
    request_hash       TEXT NOT NULL,
    status             TEXT NOT NULL,
    outcome            TEXT,
    current_stage      TEXT,
    input_json         TEXT NOT NULL,
    context_json       TEXT NOT NULL,
    result_json        TEXT,
    retry_count        INTEGER NOT NULL DEFAULT 0,
    next_retry_at      TEXT,
    last_error         TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    version            INTEGER NOT NULL DEFAULT 0,
    audit_head_hash    TEXT,
    audit_event_count  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON workflow_runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_workflow ON workflow_runs(workflow_name, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_retry ON workflow_runs(next_retry_at);

CREATE TABLE IF NOT EXISTS run_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    event_type   TEXT NOT NULL,
    stage        TEXT,
    outcome      TEXT,
    payload_json TEXT NOT NULL,
    prev_hash    TEXT NOT NULL DEFAULT '',
    event_hash   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    UNIQUE(run_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id, seq);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        connection = self.connect()
        try:
            connection.executescript(SCHEMA)
            self._migrate(connection)
            connection.execute("PRAGMA journal_mode = WAL")
        finally:
            connection.close()

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Add audit-chain columns to databases created by earlier versions."""
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(run_events)").fetchall()
        }
        if "prev_hash" not in columns:
            connection.execute(
                "ALTER TABLE run_events ADD COLUMN prev_hash TEXT NOT NULL DEFAULT ''"
            )
        if "event_hash" not in columns:
            connection.execute(
                "ALTER TABLE run_events ADD COLUMN event_hash TEXT NOT NULL DEFAULT ''"
            )

        run_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(workflow_runs)").fetchall()
        }
        if "audit_head_hash" not in run_columns:
            connection.execute(
                "ALTER TABLE workflow_runs ADD COLUMN audit_head_hash TEXT"
            )
        if "audit_event_count" not in run_columns:
            connection.execute(
                "ALTER TABLE workflow_runs ADD COLUMN audit_event_count INTEGER NOT NULL DEFAULT 0"
            )

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, fail_commit: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            if fail_commit:
                raise InjectedCommitFailure(
                    "simulated commit failure: transaction rolled back before persistence"
                )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
