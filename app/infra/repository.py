from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.core.errors import ConcurrentModificationError, RunNotFoundError
from app.domain.audit_chain import GENESIS_HASH, compute_event_hash
from app.domain.models import Outcome, RunState, RunStatus
from app.infra.db import Database


class DuplicateIdempotencyKey(Exception):
    pass


UPDATE_COLUMNS = {
    "status",
    "outcome",
    "current_stage",
    "input",
    "context",
    "result",
    "retry_count",
    "next_retry_at",
    "last_error",
    "updated_at",
}

DB_COLUMNS = {
    "status": "status",
    "outcome": "outcome",
    "current_stage": "current_stage",
    "input": "input_json",
    "context": "context_json",
    "result": "result_json",
    "retry_count": "retry_count",
    "next_retry_at": "next_retry_at",
    "last_error": "last_error",
    "updated_at": "updated_at",
}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _scalar(value: Any) -> Any:
    if isinstance(value, (RunStatus, Outcome)):
        return value.value
    return value


def _row_to_run(row: sqlite3.Row, events: list[dict[str, Any]] | None = None) -> RunState:
    return RunState(
        id=row["id"],
        workflow_name=row["workflow_name"],
        workflow_version=row["workflow_version"],
        workflow_hash=row["workflow_hash"],
        workflow_snapshot=json.loads(row["workflow_snapshot"]),
        idempotency_key=row["idempotency_key"],
        request_hash=row["request_hash"],
        status=RunStatus(row["status"]),
        outcome=Outcome(row["outcome"]) if row["outcome"] else None,
        current_stage=row["current_stage"],
        input=json.loads(row["input_json"]),
        context=json.loads(row["context_json"]),
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        retry_count=row["retry_count"],
        next_retry_at=row["next_retry_at"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
        events=events or [],
    )


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "seq": row["seq"],
        "run_id": row["run_id"],
        "event_type": row["event_type"],
        "stage": row["stage"],
        "outcome": row["outcome"],
        "payload": json.loads(row["payload_json"]),
        "created_at": row["created_at"],
        "prev_hash": row["prev_hash"],
        "event_hash": row["event_hash"],
    }


class RunRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_run(self, run: RunState, event: dict[str, Any]) -> None:
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO workflow_runs (
                        id, workflow_name, workflow_version, workflow_hash,
                        workflow_snapshot, idempotency_key, request_hash, status,
                        outcome, current_stage, input_json, context_json, result_json,
                        retry_count, next_retry_at, last_error, created_at, updated_at, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.id,
                        run.workflow_name,
                        run.workflow_version,
                        run.workflow_hash,
                        _json(run.workflow_snapshot),
                        run.idempotency_key,
                        run.request_hash,
                        run.status.value,
                        _scalar(run.outcome),
                        run.current_stage,
                        _json(run.input),
                        _json(run.context),
                        _json(run.result) if run.result is not None else None,
                        run.retry_count,
                        run.next_retry_at,
                        run.last_error,
                        run.created_at,
                        run.updated_at,
                        run.version,
                    ),
                )
                event_hash = self._insert_event(connection, run.id, 1, event)
                connection.execute(
                    """
                    UPDATE workflow_runs
                    SET audit_head_hash = ?, audit_event_count = 1
                    WHERE id = ?
                    """,
                    (event_hash, run.id),
                )
        except sqlite3.IntegrityError as exc:
            if "idempotency_key" in str(exc) or "UNIQUE" in str(exc).upper():
                raise DuplicateIdempotencyKey(run.idempotency_key) from exc
            raise

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        seq: int,
        event: dict[str, Any],
    ) -> str:
        previous = connection.execute(
            "SELECT event_hash FROM run_events WHERE run_id = ? ORDER BY seq DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        prev_hash = previous["event_hash"] if previous and previous["event_hash"] else GENESIS_HASH
        outcome = _scalar(event.get("outcome"))
        payload = event.get("payload", {})
        event_hash = compute_event_hash(
            prev_hash=prev_hash,
            run_id=run_id,
            seq=seq,
            event_type=event["event_type"],
            stage=event.get("stage"),
            outcome=outcome,
            payload=payload,
            created_at=event["created_at"],
        )
        connection.execute(
            """
            INSERT INTO run_events
                (run_id, seq, event_type, stage, outcome, payload_json,
                 prev_hash, event_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                seq,
                event["event_type"],
                event.get("stage"),
                outcome,
                _json(payload),
                prev_hash,
                event_hash,
                event["created_at"],
            ),
        )
        return event_hash

    def get_run(self, run_id: str, *, with_events: bool = False) -> RunState:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise RunNotFoundError(f"run '{run_id}' was not found")
            events = None
            if with_events:
                event_rows = connection.execute(
                    "SELECT * FROM run_events WHERE run_id = ? ORDER BY seq", (run_id,)
                ).fetchall()
                events = [_row_to_event(event_row) for event_row in event_rows]
            return _row_to_run(row, events)

    def find_by_idempotency_key(self, key: str) -> RunState | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_runs WHERE idempotency_key = ?", (key,)
            ).fetchone()
            return _row_to_run(row) if row else None

    def commit_stage(
        self,
        run_id: str,
        expected_version: int,
        updates: dict[str, Any],
        events: list[dict[str, Any]],
        *,
        fail_commit: bool = False,
    ) -> int:
        unknown = set(updates) - UPDATE_COLUMNS
        if unknown:
            raise ValueError(f"unsupported update columns: {sorted(unknown)}")

        with self.database.transaction(fail_commit=fail_commit) as connection:
            next_seq_row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) AS last_seq FROM run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            previous_count = int(next_seq_row["last_seq"])
            next_seq = previous_count + 1
            last_event_hash: str | None = None
            for event in events:
                last_event_hash = self._insert_event(
                    connection, run_id, next_seq, event
                )
                next_seq += 1

            assignments: list[str] = []
            parameters: list[Any] = []
            for key, value in updates.items():
                column = DB_COLUMNS[key]
                assignments.append(f"{column} = ?")
                if key == "input":
                    parameters.append(_json(value))
                elif key == "context":
                    parameters.append(_json(value))
                elif key == "result":
                    parameters.append(_json(value) if value is not None else None)
                else:
                    parameters.append(_scalar(value))

            if last_event_hash is not None:
                assignments.extend(
                    ["audit_head_hash = ?", "audit_event_count = ?"]
                )
                parameters.extend(
                    [last_event_hash, previous_count + len(events)]
                )
            assignments.append("version = version + 1")
            parameters.extend([run_id, expected_version])
            cursor = connection.execute(
                f"UPDATE workflow_runs SET {', '.join(assignments)} "
                "WHERE id = ? AND version = ?",
                parameters,
            )
            if cursor.rowcount != 1:
                raise ConcurrentModificationError(
                    f"run '{run_id}' was modified concurrently; reload and retry"
                )
            return expected_version + 1

    def get_audit_anchor(self, run_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT audit_head_hash, audit_event_count FROM workflow_runs "
                "WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise RunNotFoundError(f"run '{run_id}' was not found")
            return {
                "head_hash": row["audit_head_hash"],
                "event_count": row["audit_event_count"],
            }

    def list_events(self, run_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM run_events WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
            return [_row_to_event(row) for row in rows]

    def list_runs(
        self,
        *,
        status: str | None = None,
        workflow: str | None = None,
        limit: int = 50,
    ) -> list[RunState]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if status:
            clauses.append("status = ?")
            parameters.append(status)
        if workflow:
            clauses.append("workflow_name = ?")
            parameters.append(workflow)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with self.database.read() as connection:
            rows = connection.execute(
                f"SELECT * FROM workflow_runs {where} ORDER BY created_at DESC LIMIT ?",
                parameters,
            ).fetchall()
            return [_row_to_run(row) for row in rows]

    def recent_inputs(
        self, workflow: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Most recent stored inputs for a workflow, used for shadow analysis."""

        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT id, input_json, status, outcome, created_at
                FROM workflow_runs
                WHERE workflow_name = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (workflow, limit),
            ).fetchall()
        return [
            {
                "run_id": row["id"],
                "input": json.loads(row["input_json"]),
                "status": row["status"],
                "outcome": row["outcome"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def due_retries(self, now_iso: str, limit: int = 25) -> list[RunState]:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_runs
                WHERE status = 'retry_scheduled'
                  AND next_retry_at IS NOT NULL
                  AND next_retry_at <= ?
                ORDER BY next_retry_at
                LIMIT ?
                """,
                (now_iso, limit),
            ).fetchall()
            return [_row_to_run(row) for row in rows]
