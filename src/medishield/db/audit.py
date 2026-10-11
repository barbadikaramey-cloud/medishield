import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

GENESIS_HASH = "0" * 64


def canonical_json_dump(payload: dict[str, Any]) -> str:
    """Deterministically serialize payload to JSON for cryptographic hashing."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_audit_hash(
    event_id: UUID | str,
    prev_hash: str,
    payload: dict[str, Any],
    created_at: datetime,
) -> str:
    """
    Compute cryptographic SHA-256 digest for audit event chaining.
    
    Formula: SHA-256(id + prev_hash + canonical_json(payload) + created_at.isoformat())
    """
    event_id_str = str(event_id)
    payload_str = canonical_json_dump(payload)
    created_at_str = created_at.isoformat()

    hasher = hashlib.sha256()
    hasher.update(event_id_str.encode("utf-8"))
    hasher.update(prev_hash.encode("utf-8"))
    hasher.update(payload_str.encode("utf-8"))
    hasher.update(created_at_str.encode("utf-8"))
    return hasher.hexdigest()


def verify_audit_chain(events: list) -> bool:
    """
    Validate an ordered sequence of AuditEvent records.
    Returns True if the entire chain is cryptographically intact, False otherwise.
    """
    if not events:
        return True

    expected_prev = GENESIS_HASH
    for event in events:
        if event.prev_hash != expected_prev:
            return False
        computed = compute_audit_hash(
            event.id,
            event.prev_hash,
            event.event_payload,
            event.created_at,
        )
        if computed != event.current_hash:
            return False
        expected_prev = event.current_hash

    return True
