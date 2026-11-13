from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS_HASH = "0" * 64


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def compute_event_hash(
    *,
    prev_hash: str,
    run_id: str,
    seq: int,
    event_type: str,
    stage: str | None,
    outcome: str | None,
    payload: dict[str, Any],
    created_at: str,
) -> str:
    body = {
        "prev_hash": prev_hash,
        "run_id": run_id,
        "seq": seq,
        "event_type": event_type,
        "stage": stage,
        "outcome": outcome,
        "payload": payload,
        "created_at": created_at,
    }
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def verify_chain(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify an ordered event stream is a valid SHA-256 hash chain."""

    if not events:
        return {
            "valid": True,
            "algorithm": "sha256-chain",
            "event_count": 0,
            "head_hash": None,
            "broken_at_seq": None,
            "reason": None,
        }

    previous = GENESIS_HASH
    for event in events:
        seq = event["seq"]
        stored_prev = event.get("prev_hash")
        stored_hash = event.get("event_hash")
        if not stored_prev or not stored_hash:
            return {
                "valid": False,
                "algorithm": "sha256-chain",
                "event_count": len(events),
                "head_hash": events[-1].get("event_hash"),
                "broken_at_seq": seq,
                "reason": "event is missing chain hashes (legacy or tampered record)",
            }
        if stored_prev != previous:
            return {
                "valid": False,
                "algorithm": "sha256-chain",
                "event_count": len(events),
                "head_hash": events[-1].get("event_hash"),
                "broken_at_seq": seq,
                "reason": "previous hash does not match the chain head",
            }
        recomputed = compute_event_hash(
            prev_hash=stored_prev,
            run_id=event["run_id"],
            seq=seq,
            event_type=event["event_type"],
            stage=event.get("stage"),
            outcome=event.get("outcome"),
            payload=event["payload"],
            created_at=event["created_at"],
        )
        if recomputed != stored_hash:
            return {
                "valid": False,
                "algorithm": "sha256-chain",
                "event_count": len(events),
                "head_hash": events[-1].get("event_hash"),
                "broken_at_seq": seq,
                "reason": "event hash does not match the event contents",
            }
        previous = stored_hash

    return {
        "valid": True,
        "algorithm": "sha256-chain",
        "event_count": len(events),
        "head_hash": previous,
        "broken_at_seq": None,
        "reason": None,
    }
