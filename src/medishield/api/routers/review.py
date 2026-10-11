import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, status
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
from medishield.db.enums import EscalationSubReason, WorkflowState
from medishield.db.models import Case, HumanReview, ReviewLock
from medishield.worker.tasks import resume_case_workflow_task

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

    # 2. Update Case State & Version
    case.current_decision = req.new_decision
    case.workflow_state = WorkflowState.COMPLETED
    case.version += 1
    case.updated_at = now

    # 3. Release Lock
    lock = db.query(ReviewLock).filter(ReviewLock.case_id == case.id).first()
    if lock:
        db.delete(lock)

    db.commit()

    # 4. Dispatch Audit and Resumption Event
    try:
        resume_case_workflow_task.delay(
            case_id=case_id,
            resume_reason=req.reason_code,
            human_decision=req.new_decision.value,
            reviewer_id=current_user.user_id,
            expected_case_version=case.version,
        )
    except (ConnectionError, OSError) as exc:
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

    case.current_decision = req.new_decision
    case.workflow_state = WorkflowState.COMPLETED
    case.version += 1
    case.updated_at = now

    lock = db.query(ReviewLock).filter(ReviewLock.case_id == case.id).first()
    if lock:
        db.delete(lock)

    db.commit()

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
