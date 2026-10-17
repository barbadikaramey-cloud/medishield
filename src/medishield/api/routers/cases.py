import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import Response
from sqlalchemy.orm import Session

from medishield.api.deps import (
    ROLE_ADMIN,
    ROLE_CLAIMER,
    ROLE_MANAGER,
    ROLE_REVIEWER,
    AuthUser,
    check_redis_available,
    get_db,
    get_storage,
    require_role,
)
from medishield.api.schemas.cases import (
    CaseCreateResponse,
    CaseDetailResponse,
    CaseExplanationResponse,
    CaseListItem,
    CaseListResponse,
    CaseRerunResponse,
    ClaimerAdjudicationSummary,
    ClaimerDocumentItem,
    ClaimerStatusResponse,
    DocumentUploadedInfo,
    ProcessResumeRequest,
    ProcessResumeResponse,
)
from medishield.db.enums import BusinessDecision, DocumentType, WorkflowState
from medishield.db.models import Case, Decision, Document
from medishield.db.session import SyncSessionLocal
from medishield.intake.storage import (
    StorageBackend,
    generate_raw_object_key,
)
from medishield.intake.validation import (
    MAX_CASE_AGGREGATE_BYTES,
    MAX_FILE_SIZE_BYTES,
    AggregateSizeLimitExceededError,
    FileSizeLimitExceededError,
    SecurityVulnerabilityError,
    UnsupportedMimeTypeError,
    validate_file_upload,
)
from medishield.worker.tasks import (
    process_case_workflow_task,
    resume_case_workflow_task,
)

router = APIRouter(prefix="/cases", tags=["cases"])


def _run_workflow_in_background(case_id: str, trigger_reason: str, storage: StorageBackend) -> None:
    """Executes workflow task in background thread with a dedicated, isolated DB session."""
    session = SyncSessionLocal()
    try:
        process_case_workflow_task.run(
            case_id=case_id,
            trigger_reason=trigger_reason,
            db_session=session,
            storage=storage,
        )
    finally:
        session.close()


def _resume_workflow_in_background(case_id: str, resume_reason: str, expected_case_version: int) -> None:
    """Resumes workflow task in background thread with a dedicated, isolated DB session."""
    session = SyncSessionLocal()
    try:
        resume_case_workflow_task.run(
            case_id=case_id,
            resume_reason=resume_reason,
            expected_case_version=expected_case_version,
            db_session=session,
        )
    finally:
        session.close()


@router.post(
    "",
    response_model=CaseCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Multipart Case Ingestion Handshake",
)
async def create_case(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    claim_type: str = Form("STANDARD_REIMBURSEMENT_CLAIM"),
    metadata: str | None = Form(None),
    current_user: AuthUser = Depends(require_role(ROLE_CLAIMER, ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),

    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> CaseCreateResponse:
    """
    Sub-200ms Ingestion Handshake (Spec 08 § 2.1).
    Validates MIME magic bytes, generates SHA-256 checksums, streams uploads to private S3,
    creates PostgreSQL case record in state RECEIVED, and enqueues async background task.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided in intake payload")

    aggregate_size = 0
    now = datetime.now(UTC)
    case_id = f"CASE-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    # 1. Create Case Aggregate in DB
    db_case = Case(
        case_id=case_id,
        workflow_state=WorkflowState.RECEIVED,
        workflow_version="v1.0.0",
        is_complete=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(db_case)
    db.flush()

    uploaded_docs: list[DocumentUploadedInfo] = []

    # 2. Process, Validate, and Stream Each Uploaded Document
    for idx, upload_file in enumerate(files, start=1):
        content = await upload_file.read()
        file_size = len(content)
        aggregate_size += file_size

        if file_size > MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File {upload_file.filename} exceeds maximum size limit of 25MB",
            )
        if aggregate_size > MAX_CASE_AGGREGATE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Aggregate case upload exceeds maximum cap of 100MB",
            )

        # Validate MIME magic bytes & security signatures
        try:
            val_result = validate_file_upload(
                file_name=upload_file.filename or f"doc_{idx}.pdf",
                content=content,
                current_aggregate_bytes=aggregate_size - file_size,
            )
        except (FileSizeLimitExceededError, AggregateSizeLimitExceededError) as exc:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=str(exc),
            ) from exc
        except UnsupportedMimeTypeError as exc:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=str(exc),
            ) from exc
        except SecurityVulnerabilityError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        # Generate unique document ID & canonical object key inside case folder
        doc_id = f"DOC-{case_id.split('-')[-1]}-{idx:02d}"
        object_key = generate_raw_object_key(case_id, doc_id, upload_file.filename or f"doc_{idx}.pdf")

        # Stream bytes to persistent storage
        storage.put_bytes(object_key, content, val_result.mime_type)

        # Persist Document Row
        doc_record = Document(
            document_id=doc_id,
            case_id=db_case.id,
            file_name=upload_file.filename or f"doc_{idx}.pdf",
            object_key=object_key,
            mime_type=val_result.mime_type,
            checksum=val_result.checksum_sha256,
            size_bytes=file_size,
            doc_type=DocumentType.UNKNOWN,
            created_at=now,
        )
        db.add(doc_record)

        uploaded_docs.append(
            DocumentUploadedInfo(
                document_id=doc_id,
                file_name=upload_file.filename or f"doc_{idx}.pdf",
                checksum=val_result.checksum_sha256,
                size_bytes=file_size,
                mime_type=val_result.mime_type,
            )
        )

    db.commit()

    # 3. Enqueue Background Processing Task via Celery (Layer 3)
    if check_redis_available():
        try:
            process_case_workflow_task.delay(case_id=case_id, trigger_reason="INITIAL_INTAKE")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Celery broker offline; task deferred for case %s: %s", case_id, exc)
    else:
        logger.info(
            "Celery broker offline; dispatching case %s to in-process background worker",
            case_id,
        )
        background_tasks.add_task(
            _run_workflow_in_background,
            case_id=case_id,
            trigger_reason="INITIAL_INTAKE",
            storage=storage,
        )

    return CaseCreateResponse(
        case_id=case_id,
        workflow_state=WorkflowState.RECEIVED.value,
        document_count=len(uploaded_docs),
        documents=uploaded_docs,
        created_at=now,
    )


@router.get(
    "",
    response_model=CaseListResponse,
    summary="List and Filter Intake Cases",
)
def list_cases(
    workflow_state: str | None = Query(None),
    decision: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: AuthUser = Depends(require_role(ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> CaseListResponse:
    """List cases with optional state and decision filters (Spec 08 § 2.2)."""
    query = db.query(Case)

    if workflow_state:
        query = query.filter(Case.workflow_state == workflow_state)
    if decision:
        query = query.filter(Case.current_decision == decision)

    total_count = query.count()
    cases = (
        query.order_by(Case.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        CaseListItem(
            case_id=c.case_id,
            workflow_state=c.workflow_state.value if hasattr(c.workflow_state, "value") else str(c.workflow_state),
            current_decision=c.current_decision.value if c.current_decision else None,
            current_sub_reason=c.current_sub_reason.value if c.current_sub_reason else None,
            decision_confidence=float(c.decision_confidence) if c.decision_confidence is not None else None,
            document_count=len(c.documents) if hasattr(c, "documents") and c.documents else 0,
            is_complete=bool(c.is_complete) if c.is_complete is not None else False,
            version=c.version or 1,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in cases
    ]

    return CaseListResponse(
        total_count=total_count,
        page=page,
        page_size=page_size,
        items=items,
    )


@router.get(
    "/{case_id}",
    response_model=CaseDetailResponse,
    summary="Get Case Adjudication Detail",
)
def get_case_detail(
    case_id: str,
    current_user: AuthUser = Depends(require_role(ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> CaseDetailResponse:
    """Retrieve full case detail, document roster, and active decision (Spec 08 § 2.3)."""
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    docs = [
        DocumentUploadedInfo(
            document_id=d.document_id,
            file_name=d.file_name,
            checksum=d.checksum,
            size_bytes=d.size_bytes,
            mime_type=d.mime_type,
            doc_type=d.doc_type.value if hasattr(d.doc_type, "value") else str(d.doc_type),
            ocr_payload_uri=d.ocr_payload_uri,
        )
        for d in case.documents
    ]

    active_decision = None
    if hasattr(case, "decisions") and case.decisions:
        active_decision = next((d for d in reversed(case.decisions) if getattr(d, "is_active", True)), None)
    if not active_decision:
        try:
            active_decision = (
                db.query(Decision)
                .filter(Decision.case_id == case.id, Decision.is_active.is_(True))
                .order_by(Decision.created_at.desc())
                .first()
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Active decision lookup fallback for case %s: %s", case.case_id, exc)

    detailed_explanation = None
    if active_decision:
        if isinstance(active_decision.agent_summary_snapshot, dict):
            detailed_explanation = active_decision.agent_summary_snapshot.get("detailed_explanation")
        if not detailed_explanation:
            detailed_explanation = active_decision.justification

    return CaseDetailResponse(
        case_id=case.case_id,
        workflow_state=case.workflow_state.value if hasattr(case.workflow_state, "value") else str(case.workflow_state),
        workflow_version=case.workflow_version or "v1.0.0",
        current_decision=case.current_decision.value if case.current_decision else None,
        current_sub_reason=case.current_sub_reason.value if case.current_sub_reason else None,
        decision_confidence=float(case.decision_confidence) if case.decision_confidence is not None else None,
        is_complete=bool(case.is_complete) if case.is_complete is not None else False,
        missing_documents=case.missing_documents or [],
        rule_id_matched=active_decision.rule_id_matched if active_decision else None,
        justification=active_decision.justification if active_decision else None,
        detailed_explanation=detailed_explanation,
        agent_findings=active_decision.agent_summary_snapshot if active_decision else None,
        version=case.version or 1,
        document_count=len(docs),
        documents=docs,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


@router.get(
    "/{case_id}/status",
    response_model=ClaimerStatusResponse,
    summary="Get Sanitized Patient Claim Status (Claimer View)",
)
def get_claimer_claim_status(
    case_id: str,
    current_user: AuthUser = Depends(require_role(ROLE_CLAIMER, ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> ClaimerStatusResponse:
    """
    Sanitized, patient-facing claim tracking endpoint (Spec 11 § 2, ADR-012).
    Shields proprietary internal reasoning traces, fraud scores, and rule IDs,
    returning clear workflow state, financial summary, and customer explanation.
    """
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"Claim {case_id} not found")

    # Document list (sanitized)
    doc_items = [
        ClaimerDocumentItem(
            document_id=d.document_id,
            file_name=d.file_name,
            doc_type=d.doc_type.value if hasattr(d.doc_type, "value") else str(d.doc_type),
            size_bytes=d.size_bytes,
            created_at=d.created_at,
        )
        for d in (case.documents or [])
    ]

    active_decision = None
    if hasattr(case, "decisions") and case.decisions:
        active_decision = next((d for d in reversed(case.decisions) if getattr(d, "is_active", True)), None)
    if not active_decision:
        try:
            active_decision = (
                db.query(Decision)
                .filter(Decision.case_id == case.id, Decision.is_active.is_(True))
                .order_by(Decision.created_at.desc())
                .first()
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Active decision lookup fallback for case %s: %s", case.case_id, exc)

    # Derive user-friendly status label and lifecycle completeness (ADR-001, ADR-012, ADR-014)
    state_str = case.workflow_state.value if hasattr(case.workflow_state, "value") else str(case.workflow_state)
    is_complete = bool(case.is_complete) or state_str == "COMPLETED"

    decision_val = None
    if active_decision:
        decision_val = active_decision.decision.value if hasattr(active_decision.decision, "value") else str(active_decision.decision)
    elif case.current_decision:
        decision_val = case.current_decision.value if hasattr(case.current_decision, "value") else str(case.current_decision)

    # Human Review Barrier: Preliminary automated decisions & calculations are strictly withheld until finalization
    if is_complete:
        if decision_val == "APPROVE":
            status_label = "Claim Approved"
        elif decision_val == "REJECT":
            status_label = "Claim Denied"
        else:
            status_label = "Claim Completed"
    elif state_str == "AWAITING_INFO":
        status_label = "Action Required: Missing Information"
    elif state_str in ("AWAITING_HUMAN", "ANALYZING", "SCANNING", "CLASSIFYING"):
        status_label = "Under Specialist Review"
    elif state_str == "FAILED":
        status_label = "Processing Issue Encountered"
    else:
        status_label = "Claim Received"

    adjudication_summary = None
    # Adjudication summary and patient financial figures are exposed ONLY upon final completion
    if is_complete and active_decision:
        snapshot = active_decision.agent_summary_snapshot or {}
        policy_meta = snapshot.get("policy", {}) if isinstance(snapshot, dict) else {}
        claims_meta = snapshot.get("claims", {}) if isinstance(snapshot, dict) else {}

        # Synthesize patient-friendly explanation letter
        patient_letter = active_decision.justification
        if snapshot.get("detailed_explanation"):
            detailed = str(snapshot.get("detailed_explanation"))
            if "### Executive Summary" in detailed:
                parts = detailed.split("### Executive Summary")
                if len(parts) > 1:
                    patient_letter = parts[1].split("###")[0].strip()

        cov_amt = None
        if policy_meta.get("coverage_percentage"):
            cov_amt = round(float(claims_meta.get("total_billed", 0) or 0) * (float(policy_meta.get("coverage_percentage")) / 100.0), 2)

        adjudication_summary = ClaimerAdjudicationSummary(
            decision=decision_val or "COMPLETED",
            covered_amount=cov_amt,
            total_billed=float(claims_meta.get("total_billed")) if claims_meta.get("total_billed") is not None else None,
            deductible_applied=float(policy_meta.get("deductible_limit")) if policy_meta.get("deductible_limit") is not None else None,
            copay_percentage=float(policy_meta.get("copay_percentage")) if policy_meta.get("copay_percentage") is not None else None,
            member_responsibility=float(policy_meta.get("member_responsibility")) if policy_meta.get("member_responsibility") is not None else None,
            explanation_letter=patient_letter,
        )

    return ClaimerStatusResponse(
        case_id=case.case_id,
        workflow_state=state_str,
        is_complete=is_complete,
        status_label=status_label,
        missing_documents=case.missing_documents or [],
        adjudication=adjudication_summary,
        submitted_documents=doc_items,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )



@router.post(
    "/{case_id}/process",
    response_model=ProcessResumeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Resume Paused Case Processing",
)
def resume_case_processing(
    case_id: str,
    req: ProcessResumeRequest,
    background_tasks: BackgroundTasks,
    current_user: AuthUser = Depends(require_role(ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> ProcessResumeResponse:
    """Triggers background workflow resumption after supplementary uploads (Spec 08 § 2.5)."""
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    if case.version != req.expected_case_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Optimistic lock conflict: DB version {case.version} != expected {req.expected_case_version}",
        )

    case.workflow_state = WorkflowState.SCANNING
    case.version += 1
    case.updated_at = datetime.now(UTC)
    db.commit()

    if check_redis_available():
        try:
            resume_case_workflow_task.delay(
                case_id=case_id,
                resume_reason=req.resume_reason,
                expected_case_version=case.version,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Celery broker offline; resume task deferred for case %s: %s", case_id, exc)
    else:
        logger.info(
            "Celery broker offline; dispatching resume for case %s to in-process background worker",
            case_id,
        )
        background_tasks.add_task(
            _resume_workflow_in_background,
            case_id=case_id,
            resume_reason=req.resume_reason,
            expected_case_version=case.version,
        )

    return ProcessResumeResponse(
        case_id=case_id,
        workflow_state=WorkflowState.SCANNING.value,
        resume_reason=req.resume_reason,
        version=case.version,
        updated_at=case.updated_at,
    )


@router.post(
    "/{case_id}/rerun",
    response_model=CaseRerunResponse,
    status_code=status.HTTP_200_OK,
    summary="Rerun Case Workflow Pipeline",
)
def rerun_case(
    case_id: str,
    current_user: AuthUser = Depends(require_role(ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> CaseRerunResponse:
    """
    Reruns the multi-agent claims adjudication workflow pipeline for an existing case.
    Re-evaluates document extractions, deterministic validators, and decision rules.
    """
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case {case_id} not found",
        )

    if check_redis_available():
        try:
            process_case_workflow_task.delay(
                case_id=case_id,
                trigger_reason="USER_RERUN",
                force_rerun=True,
            )
            return CaseRerunResponse(
                case_id=case_id,
                workflow_state=case.workflow_state.value,
                status="ENQUEUED",
                decision=case.current_decision.value if case.current_decision else None,
                escalation_sub_reason=case.current_sub_reason.value if case.current_sub_reason else None,
                version=case.version,
                message=f"Workflow rerun enqueued for case {case_id}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Celery dispatch failed for rerun %s: %s; falling back to direct run", case_id, exc)

    # In-process synchronous execution
    result = process_case_workflow_task.run(
        case_id=case_id,
        trigger_reason="USER_RERUN",
        force_rerun=True,
        db_session=db,
        storage=storage,
    )

    if hasattr(db, "refresh"):
        db.refresh(case)

    return CaseRerunResponse(
        case_id=case_id,
        workflow_state=case.workflow_state.value,
        status=result.get("status", "COMPLETED"),
        decision=case.current_decision.value if case.current_decision else None,
        escalation_sub_reason=case.current_sub_reason.value if case.current_sub_reason else None,
        version=case.version,
        message=f"Workflow rerun executed successfully for case {case_id}",
    )


@router.get(
    "/{case_id}/documents/{document_id}/ocr",
    summary="Get Document OCR Text and Spans",
)
def get_document_ocr_payload(
    case_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> dict[str, Any]:
    """Retrieves full OCR payload including token spans and bounding boxes for a document."""
    object_key = f"ocr/{case_id}/{document_id}.json"
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if case:
        doc = db.query(Document).filter(Document.case_id == case.id, Document.document_id == document_id).first()
        if doc and doc.ocr_payload_uri:
            object_key = doc.ocr_payload_uri

    try:
        raw_bytes = storage.get_bytes(object_key)
        return json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OCR payload for {document_id} in case {case_id} not found: {exc}",
        ) from exc


@router.get(
    "/{case_id}/documents/{document_id}/content",
    summary="Get Raw Document Content",
)
def get_document_content(
    case_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> Response:
    """Retrieves the raw document file bytes (e.g., Image/PDF) for rendering."""
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    doc = db.query(Document).filter(Document.case_id == case.id, Document.document_id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    try:
        raw_bytes = storage.get_bytes(doc.object_key)
        return Response(content=raw_bytes, media_type=doc.mime_type)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Document content not found in storage") from exc


@router.post(
    "/{case_id}/explain",
    response_model=CaseExplanationResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate or Refresh LLM Clinical Decision Explanation (ADR-011)",
)
def explain_case_decision(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(require_role(ROLE_CLAIMER, ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),
) -> CaseExplanationResponse:
    """
    Synthesizes an in-depth clinical & policy narrative for an adjudicated case using
    the Groq LLM (with deterministic offline fallback per ADR-011 and Rules 10, 11, 14).
    Updates and persists the explanation to the case decision snapshot.
    """
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case {case_id} not found",
        )

    # Find active decision
    active_decision = None
    try:
        active_decision = (
            db.query(Decision)
            .filter(Decision.case_id == case.id, Decision.is_active.is_(True))
            .order_by(Decision.created_at.desc())
            .first()
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Active decision lookup error for %s: %s", case_id, exc)

    if not active_decision:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Case {case_id} has not completed adjudication yet.",
        )

    # Reconstruct context & orchestrator outcome
    snapshot = active_decision.agent_summary_snapshot or {}
    if not isinstance(snapshot, dict):
        snapshot = {}

    from types import SimpleNamespace

    from medishield.core.llm import default_groq_client
    from medishield.domain.explanation import generate_decision_explanation

    context = SimpleNamespace(
        case_id=case.case_id,
        execution_id=getattr(active_decision, "execution_id", None) or f"exec-{case.case_id}",
        security_tamper_score=float(snapshot.get("security_tamper_score", 0.0) or 0.0),
        kyc_output=snapshot.get("kyc"),
        claims_output=snapshot.get("claims"),
        policy_output=snapshot.get("policy"),
        fraud_output=snapshot.get("fraud"),
    )

    decision_enum = active_decision.decision
    if isinstance(decision_enum, str):
        try:
            decision_enum = BusinessDecision(decision_enum)
        except ValueError:
            decision_enum = BusinessDecision.ESCALATE

    conf = getattr(active_decision, "decision_confidence", None)
    if conf is None:
        conf = getattr(active_decision, "confidence", 0.85)

    outcome = SimpleNamespace(
        decision=decision_enum or BusinessDecision.ESCALATE,
        rule_id_matched=active_decision.rule_id_matched or "RULE-P8-CATCH-ALL",
        decision_confidence=float(conf or 0.85),
        justification=active_decision.justification or "Adjudication decision reached.",
        escalation_sub_reason=active_decision.escalation_sub_reason,
    )

    detailed_explanation = generate_decision_explanation(context, outcome)

    # Check model source
    groq_avail = default_groq_client.is_available() if default_groq_client else False
    if groq_avail:
        used = getattr(default_groq_client, "last_used_model", None) or default_groq_client.model
        fb = getattr(default_groq_client, "fallback_used", False)
        clean_model = used.split("/")[-1] if "/" in used else used
        suffix = " [Fallback]" if fb else ""
        model_source = f"Groq LLM ({clean_model}){suffix}"
    else:
        model_source = "Clinical Decision Synthesizer (ADR-011 Engine)"

    # Persist explanation back to Decision record
    try:
        updated_snapshot = dict(snapshot)
        updated_snapshot["detailed_explanation"] = detailed_explanation
        updated_snapshot["explanation_model"] = model_source
        active_decision.agent_summary_snapshot = updated_snapshot
        active_decision.justification = detailed_explanation
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not persist explanation update to DB: %s", exc)

    return CaseExplanationResponse(
        case_id=case.case_id,
        decision=active_decision.decision.value if hasattr(active_decision.decision, "value") else str(active_decision.decision),
        rule_id_matched=active_decision.rule_id_matched or "RULE-P8-CATCH-ALL",
        decision_confidence=float(conf) if conf is not None else None,
        detailed_explanation=detailed_explanation,
        model_source=model_source,
        generated_at=datetime.now(UTC),
    )

