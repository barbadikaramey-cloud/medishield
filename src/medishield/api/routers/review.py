import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from kombu.exceptions import OperationalError as KombuOperationalError
from sqlalchemy.orm import Session

from medishield.api.deps import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_REVIEWER,
    AuthUser,
    get_db,
    require_role,
)
from medishield.api.schemas.review import (
    HumanOverrideRequest,
    HumanReviewResponse,
    RequestInfoRequest,
    ReviewLockResponse,
)
from medishield.db.audit import GENESIS_HASH, compute_audit_hash
from medishield.db.enums import ActorType, EscalationSubReason, WorkflowState
from medishield.db.models import AuditEvent, Case, Decision, HumanReview, ReviewLock
from medishield.worker.tasks import resume_case_workflow_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cases", tags=["human-review"])

LOCK_DURATION_MINUTES = 15


@router.post(
    "/{case_id}/review/lock",
    response_model=ReviewLockResponse,
    summary="Acquire Human Review Lock",
)
def acquire_review_lock(
    case_id: str,
    current_user: AuthUser = Depends(require_role(ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> ReviewLockResponse:
    """
    Acquires exclusive 15-minute lock to prevent concurrent adjuster collision (Spec 08 § 3.1).
    Returns HTTP 409 Conflict if locked by another active reviewer.
    """
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    now = datetime.now(UTC)
    existing_lock = db.query(ReviewLock).filter(ReviewLock.case_id == case.id).first()

    if existing_lock:
        if existing_lock.expires_at > now and existing_lock.reviewer_id != current_user.user_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Case is already locked by reviewer {existing_lock.reviewer_id} until {existing_lock.expires_at.isoformat()}",
            )
        # Lock expired or held by same reviewer -> refresh TTL
        existing_lock.reviewer_id = current_user.user_id
        existing_lock.locked_at = now
        existing_lock.expires_at = now + timedelta(minutes=LOCK_DURATION_MINUTES)
    else:
        new_lock = ReviewLock(
            case_id=case.id,
            reviewer_id=current_user.user_id,
            locked_at=now,
            expires_at=now + timedelta(minutes=LOCK_DURATION_MINUTES),
        )
        db.add(new_lock)

    db.commit()

    return ReviewLockResponse(
        case_id=case_id,
        reviewer_id=current_user.user_id,
        locked_at=now,
        expires_at=now + timedelta(minutes=LOCK_DURATION_MINUTES),
        lock_active=True,
    )


@router.delete(
    "/{case_id}/review/lock",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Release Human Review Lock",
)
def release_review_lock(
    case_id: str,
    current_user: AuthUser = Depends(require_role(ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> None:
    """Explicitly releases reviewer lock on case (Spec 08 § 3.5)."""
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    lock = db.query(ReviewLock).filter(ReviewLock.case_id == case.id).first()
    if lock:
        db.delete(lock)
        db.commit()


@router.post(
    "/{case_id}/review/override",
    response_model=HumanReviewResponse,
    summary="Submit Authoritative Reviewer Override",
)
def submit_human_override(
    case_id: str,
    req: HumanOverrideRequest,
    current_user: AuthUser = Depends(require_role(ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> HumanReviewResponse:
    """
    Submits manual human override with reason codes and optimistic locking (Spec 08 § 3.2, ADR-005).
    Releases review lock, persists audit review record, and advances case to COMPLETED.
    """
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    # Optimistic Concurrency Control
    if case.version != req.expected_case_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Optimistic lock conflict: DB version {case.version} != expected {req.expected_case_version}",
        )

    now = datetime.now(UTC)
    old_decision = case.current_decision

    # 1. Record Human Review in System of Record
    review_record = HumanReview(
        case_id=case.id,
        reviewer_id=current_user.user_id,
        action="OVERRIDE",
        new_decision=req.new_decision,
        reason_code=req.reason_code,
        remarks=req.remarks,
        expected_case_version=req.expected_case_version,
        created_at=now,
    )
    db.add(review_record)

    # 2. Deactivate existing active decisions and create new authoritative Decision record
    prev_decisions = db.query(Decision).filter(Decision.case_id == case.id).all()
    prev_snapshot: dict[str, Any] = {}
    for pd in prev_decisions:
        if getattr(pd, "is_active", False):
            pd.is_active = False
            if isinstance(pd.agent_summary_snapshot, dict):
                prev_snapshot = dict(pd.agent_summary_snapshot)

    prev_snapshot["original_ai_decision"] = old_decision.value if hasattr(old_decision, "value") else (str(old_decision) if old_decision else None)
    prev_snapshot["override_action"] = "OVERRIDE"
    prev_snapshot["override_reason"] = req.reason_code
    prev_snapshot["override_remarks"] = req.remarks
    prev_snapshot["overridden_by"] = current_user.user_id
    prev_snapshot["overridden_at"] = now.isoformat()

    new_decision_record = Decision(
        case_id=case.id,
        decision=req.new_decision,
        escalation_sub_reason=None,
        actor_type=ActorType.HUMAN,
        actor_id=current_user.user_id,
        decision_confidence=Decimal("1.000"),
        rule_id_matched=f"HUMAN_OVERRIDE_{req.reason_code}",
        justification=f"Authoritative Human Override [{req.reason_code}]: {req.remarks}",
        agent_summary_snapshot=prev_snapshot,
        case_version_at_decision=case.version + 1,
        is_active=True,
        created_at=now,
    )
    db.add(new_decision_record)

    # 3. Update Case State & Version
    case.current_decision = req.new_decision
    case.workflow_state = WorkflowState.COMPLETED
    case.is_complete = True
    case.version += 1
    case.updated_at = now

    # 4. Release Lock
    lock = db.query(ReviewLock).filter(ReviewLock.case_id == case.id).first()
    if lock:
        db.delete(lock)

    # 5. Cryptographically Chained Audit Ledger Record
    try:
        last_event = (
            db.query(AuditEvent)
            .filter(AuditEvent.case_id == case.id)
            .order_by(AuditEvent.created_at.desc())
            .first()
        )
        prev_hash = last_event.current_hash if last_event else GENESIS_HASH

        ev_id = uuid.uuid4()
        audit_payload = {
            "action": "HUMAN_OVERRIDE",
            "case_id": case_id,
            "reviewer_id": current_user.user_id,
            "previous_decision": old_decision.value if hasattr(old_decision, "value") else (str(old_decision) if old_decision else None),
            "new_decision": req.new_decision.value,
            "reason_code": req.reason_code,
            "remarks": req.remarks,
            "case_version": case.version,
        }
        current_hash = compute_audit_hash(
            event_id=ev_id,
            prev_hash=prev_hash,
            payload=audit_payload,
            created_at=now,
        )

        audit_ev = AuditEvent(
            id=ev_id,
            case_id=case.id,
            event_type="HUMAN_OVERRIDE",
            actor_type=ActorType.HUMAN,
            actor_id=current_user.user_id,
            event_payload=audit_payload,
            prev_hash=prev_hash,
            current_hash=current_hash,
            created_at=now,
        )
        db.add(audit_ev)
    except Exception as a_exc:  # noqa: BLE001
        logger.warning("Failed to record human review audit event: %s", a_exc)

    db.commit()

    # 6. Real-time Live Event Streaming (WebSockets)
    try:
        from medishield.api.events import ExecutionEvent, broadcaster
        broadcaster.broadcast(
            case_id,
            ExecutionEvent(
                case_id=case_id,
                agent_name="HumanReviewer",
                event_type="COMPLETED",
                status="SUCCESS",
                safe_summary=f"Decision overridden to {req.new_decision.value} by reviewer {current_user.user_id}: {req.remarks[:60]}",
                payload={"new_decision": req.new_decision.value, "reason_code": req.reason_code},
            ),
        )
    except Exception as b_exc:  # noqa: BLE001
        logger.debug("Failed to broadcast human review event: %s", b_exc)

    # 7. Dispatch Audit and Resumption Event
    try:
        resume_case_workflow_task.delay(
            case_id=case_id,
            resume_reason=req.reason_code,
            human_decision=req.new_decision.value,
            reviewer_id=current_user.user_id,
            expected_case_version=case.version,
        )
    except (ConnectionError, OSError, KombuOperationalError) as exc:
        logger.warning("Celery broker offline; resume task deferred for case %s: %s", case_id, exc)

    return HumanReviewResponse(
        case_id=case_id,
        action="OVERRIDE",
        decision=req.new_decision.value,
        reason_code=req.reason_code,
        reviewer_id=current_user.user_id,
        case_version=case.version,
        created_at=now,
    )


@router.post(
    "/{case_id}/review/confirm",
    response_model=HumanReviewResponse,
    summary="Confirm AI Adjudication Recommendation",
)
def confirm_ai_decision(
    case_id: str,
    req: HumanOverrideRequest,
    current_user: AuthUser = Depends(require_role(ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> HumanReviewResponse:
    """Confirms AI adjudication decision without override (Spec 08 § 3.3)."""
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    if case.version != req.expected_case_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Optimistic lock conflict: DB version {case.version} != expected {req.expected_case_version}",
        )

    now = datetime.now(UTC)

    review_record = HumanReview(
        case_id=case.id,
        reviewer_id=current_user.user_id,
        action="CONFIRM",
        new_decision=req.new_decision,
        reason_code=req.reason_code,
        remarks=req.remarks,
        expected_case_version=req.expected_case_version,
        created_at=now,
    )
    db.add(review_record)

    # Deactivate existing active decisions and create new authoritative Decision record
    prev_decisions = db.query(Decision).filter(Decision.case_id == case.id).all()
    prev_snapshot: dict[str, Any] = {}
    for pd in prev_decisions:
        if getattr(pd, "is_active", False):
            pd.is_active = False
            if isinstance(pd.agent_summary_snapshot, dict):
                prev_snapshot = dict(pd.agent_summary_snapshot)

    prev_snapshot["override_action"] = "CONFIRM"
    prev_snapshot["confirmed_by"] = current_user.user_id
    prev_snapshot["confirmed_at"] = now.isoformat()

    new_decision_record = Decision(
        case_id=case.id,
        decision=req.new_decision,
        escalation_sub_reason=None,
        actor_type=ActorType.HUMAN,
        actor_id=current_user.user_id,
        decision_confidence=Decimal("1.000"),
        rule_id_matched=f"HUMAN_CONFIRMATION_{req.reason_code}",
        justification=f"Human Reviewer Confirmation [{req.reason_code}]: {req.remarks}",
        agent_summary_snapshot=prev_snapshot,
        case_version_at_decision=case.version + 1,
        is_active=True,
        created_at=now,
    )
    db.add(new_decision_record)

    case.current_decision = req.new_decision
    case.workflow_state = WorkflowState.COMPLETED
    case.is_complete = True
    case.version += 1
    case.updated_at = now

    lock = db.query(ReviewLock).filter(ReviewLock.case_id == case.id).first()
    if lock:
        db.delete(lock)

    # Cryptographically Chained Audit Ledger Record
    try:
        last_event = (
            db.query(AuditEvent)
            .filter(AuditEvent.case_id == case.id)
            .order_by(AuditEvent.created_at.desc())
            .first()
        )
        prev_hash = last_event.current_hash if last_event else GENESIS_HASH

        ev_id = uuid.uuid4()
        audit_payload = {
            "action": "HUMAN_CONFIRM",
            "case_id": case_id,
            "reviewer_id": current_user.user_id,
            "decision": req.new_decision.value,
            "reason_code": req.reason_code,
            "remarks": req.remarks,
            "case_version": case.version,
        }
        current_hash = compute_audit_hash(
            event_id=ev_id,
            prev_hash=prev_hash,
            payload=audit_payload,
            created_at=now,
        )

        audit_ev = AuditEvent(
            id=ev_id,
            case_id=case.id,
            event_type="HUMAN_CONFIRM",
            actor_type=ActorType.HUMAN,
            actor_id=current_user.user_id,
            event_payload=audit_payload,
            prev_hash=prev_hash,
            current_hash=current_hash,
            created_at=now,
        )
        db.add(audit_ev)
    except Exception as a_exc:  # noqa: BLE001
        logger.warning("Failed to record human confirm audit event: %s", a_exc)

    db.commit()

    # Real-time Live Event Streaming (WebSockets)
    try:
        from medishield.api.events import ExecutionEvent, broadcaster
        broadcaster.broadcast(
            case_id,
            ExecutionEvent(
                case_id=case_id,
                agent_name="HumanReviewer",
                event_type="COMPLETED",
                status="SUCCESS",
                safe_summary=f"Decision confirmed as {req.new_decision.value} by reviewer {current_user.user_id}",
                payload={"decision": req.new_decision.value},
            ),
        )
    except Exception as b_exc:  # noqa: BLE001
        logger.debug("Failed to broadcast human confirm event: %s", b_exc)

    return HumanReviewResponse(
        case_id=case_id,
        action="CONFIRM",
        decision=req.new_decision.value,
        reason_code=req.reason_code,
        reviewer_id=current_user.user_id,
        case_version=case.version,
        created_at=now,
    )


@router.post(
    "/{case_id}/review/request-info",
    response_model=HumanReviewResponse,
    summary="Request Supplementary Information",
)
def request_supplementary_info(
    case_id: str,
    req: RequestInfoRequest,
    current_user: AuthUser = Depends(require_role(ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> HumanReviewResponse:
    """Transitions case to AWAITING_INFO for customer document re-submission (Spec 08 § 3.4)."""
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    if case.version != req.expected_case_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Optimistic lock conflict: DB version {case.version} != expected {req.expected_case_version}",
        )

    now = datetime.now(UTC)

    review_record = HumanReview(
        case_id=case.id,
        reviewer_id=current_user.user_id,
        action="REQUEST_INFO",
        new_decision=None,
        reason_code=req.reason_code,
        remarks=req.remarks,
        expected_case_version=req.expected_case_version,
        created_at=now,
    )
    db.add(review_record)

    case.workflow_state = WorkflowState.AWAITING_INFO
    case.current_sub_reason = EscalationSubReason.MISSING_INFORMATION
    if req.requested_document_types:
        case.missing_documents = req.requested_document_types
    case.version += 1
    case.updated_at = now

    lock = db.query(ReviewLock).filter(ReviewLock.case_id == case.id).first()
    if lock:
        db.delete(lock)

    db.commit()

    return HumanReviewResponse(
        case_id=case_id,
        action="REQUEST_INFO",
        decision=None,
        reason_code=req.reason_code,
        reviewer_id=current_user.user_id,
        case_version=case.version,
        created_at=now,
    )
