import uuid
from datetime import UTC, datetime

from medishield.db.audit import (
    GENESIS_HASH,
    canonical_json_dump,
    compute_audit_hash,
    verify_audit_chain,
)


class MockAuditEvent:
    def __init__(self, id, prev_hash, current_hash, event_payload, created_at):
        self.id = id
        self.prev_hash = prev_hash
        self.current_hash = current_hash
        self.event_payload = event_payload
        self.created_at = created_at


def test_canonical_json_ordering():
    payload1 = {"b": 2, "a": 1, "c": [3, 2, 1]}
    payload2 = {"a": 1, "c": [3, 2, 1], "b": 2}
    assert canonical_json_dump(payload1) == canonical_json_dump(payload2)


def test_compute_audit_hash_deterministic():
    event_id = uuid.uuid4()
    now = datetime.now(UTC)
    payload = {"action": "CASE_CREATED", "version": 1}

    hash1 = compute_audit_hash(event_id, GENESIS_HASH, payload, now)
    hash2 = compute_audit_hash(event_id, GENESIS_HASH, payload, now)

    assert hash1 == hash2
    assert len(hash1) == 64


def test_verify_audit_chain_valid():
    id1 = uuid.uuid4()
    now1 = datetime(2026, 10, 2, 10, 0, 0, tzinfo=UTC)
    p1 = {"step": 1}
    h1 = compute_audit_hash(id1, GENESIS_HASH, p1, now1)
    e1 = MockAuditEvent(id1, GENESIS_HASH, h1, p1, now1)

    id2 = uuid.uuid4()
    now2 = datetime(2026, 10, 2, 10, 1, 0, tzinfo=UTC)
    p2 = {"step": 2}
    h2 = compute_audit_hash(id2, h1, p2, now2)
    e2 = MockAuditEvent(id2, h1, h2, p2, now2)

    assert verify_audit_chain([e1, e2]) is True


def test_verify_audit_chain_tampered_payload():
    id1 = uuid.uuid4()
    now1 = datetime(2026, 10, 2, 10, 0, 0, tzinfo=UTC)
    p1 = {"step": 1}
    h1 = compute_audit_hash(id1, GENESIS_HASH, p1, now1)

    # Tamper with payload
    e1_tampered = MockAuditEvent(id1, GENESIS_HASH, h1, {"step": 1, "hacked": True}, now1)
    assert verify_audit_chain([e1_tampered]) is False

