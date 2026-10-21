import uuid
from datetime import UTC, datetime

from medishield.db.audit import GENESIS_HASH, compute_audit_hash
from medishield.db.models import utcnow


def test_backend_utcnow_is_timezone_aware() -> None:
    """Verify that utcnow() emits timezone-aware UTC datetime instances (preventing offset drift)."""
    now = utcnow()
    assert now.tzinfo is not None
    assert now.tzinfo == UTC
    assert now.utcoffset() is not None
    assert now.utcoffset().total_seconds() == 0


def test_audit_hash_chain_with_human_override() -> None:
    """Verify audit hash chaining for human override records."""
    ev1_id = uuid.uuid4()
    now1 = datetime.now(UTC)
    payload1 = {"action": "WORKFLOW_ADJUDICATION", "decision": "ESCALATE"}
    hash1 = compute_audit_hash(ev1_id, GENESIS_HASH, payload1, now1)
    assert len(hash1) == 64

    ev2_id = uuid.uuid4()
    now2 = datetime.now(UTC)
    payload2 = {
        "action": "HUMAN_OVERRIDE",
        "reviewer_id": "reviewer_01",
        "new_decision": "APPROVE",
        "remarks": "Supervisory approval granted following clinical review.",
    }
    hash2 = compute_audit_hash(ev2_id, hash1, payload2, now2)
    assert len(hash2) == 64
    assert hash2 != hash1
