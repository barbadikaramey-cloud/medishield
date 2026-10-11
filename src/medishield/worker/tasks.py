import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from celery.exceptions import Retry

from medishield.core.telemetry import scrub_dict_phi
from medishield.db.enums import (
    ActorType,
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
    WorkflowState,
)
from medishield.db.models import AuditEvent, Case, Decision
from medishield.intake.storage import InMemoryStorageBackend, StorageBackend
from medishield.ocr.preprocessor import OCRPreprocessorEngine
from medishield.worker.celery_app import celery_app
from medishield.workflows.coordinator import CaseWorkflowCoordinator
from medishield.workflows.state import CaseState, DocumentReference, create_initial_case_state

logger = logging.getLogger(__name__)

TRANSIENT_EXCEPTIONS = (ConnectionError, TimeoutError, OSError)


@celery_app.task(
    bind=True,
    name="medishield.tasks.intake.process_document_ocr",
    max_retries=3,
    default_retry_delay=5,
)
def process_document_ocr_task(
    self: Any,
    case_id: str,
    document_id: str,
    storage: StorageBackend | None = None,
    ocr_engine: OCRPreprocessorEngine | None = None,
    content: bytes | None = None,
    mime_type: str = "text/plain",
    object_key: str | None = None,
) -> dict[str, Any]:
    """
    Intake queue task: Processes raw uploaded document through OCR preprocessor (Spec 04 § 2 Node 1).
    Routed to: intake_queue.
    """
    active_storage = storage or InMemoryStorageBackend()
    active_engine = ocr_engine or OCRPreprocessorEngine()

    try:
        if content is None:
            resolved_key = object_key or f"raw/{case_id}/{document_id}.txt"
            if not active_storage.exists(resolved_key):
                alt_keys = [
                    f"raw/{case_id}/{document_id}.pdf",
                    f"cases/{case_id}/raw/{document_id}.pdf",
                    f"cases/{case_id}/raw/{document_id}.txt",
                ]
                for k in alt_keys:
                    if active_storage.exists(k):
                        resolved_key = k
                        break
            content = active_storage.get_bytes(resolved_key)

        output = active_engine.process_document(
            content=content,
            document_id=document_id,
            case_id=case_id,
            mime_type=mime_type,
            storage_backend=active_storage,
        )
        result = {
            "status": "COMPLETED",
            "case_id": case_id,
            "document_id": document_id,
            "page_count": output.total_pages,
            "payload_uri": output.payload_storage_uri,
            "quality_score": output.image_quality_score,
            "verdict": output.verdict.value,
        }
        return scrub_dict_phi(result)
    except Retry:
        raise
    except TRANSIENT_EXCEPTIONS as exc:
        logger.warning(
            "Transient error processing OCR for document %s in case %s: %s (attempt %d/3)",
            document_id,
            case_id,
            exc,
            self.request.retries + 1,
        )
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 5) from exc
    except Exception as exc:
        logger.exception(
            "Fatal failure processing OCR for document %s in case %s",
            document_id,
            case_id,
        )
        return scrub_dict_phi({
            "status": "FAILED",
            "case_id": case_id,
            "document_id": document_id,
            "error": str(exc),
        })


@celery_app.task(
    bind=True,
    name="medishield.tasks.workflow.process_case",
    max_retries=3,
    default_retry_delay=5,
)
def process_case_workflow_task(
    self: Any,
    case_id: str,
    trigger_reason: str = "INITIAL_INTAKE",
    expected_case_version: int | None = None,
    coordinator: CaseWorkflowCoordinator | None = None,
    initial_state: CaseState | None = None,
    db_session: Any | None = None,
    storage: StorageBackend | None = None,
    force_rerun: bool = False,
) -> dict[str, Any]:
    """
    Workflow queue task: Orchestrates multi-agent claims adjudication (Spec 04 § 2-4).
    Routed to: workflow_queue.
    Honors Human Review Decoupling Principle (Spec 04 § 4): Releases worker thread on escalation.
    """
    case_obj: Case | None = None

    # 1. Optimistic Concurrency & DB Hydration Check
    if db_session is not None:
        case_obj = db_session.query(Case).filter(Case.case_id == case_id).first()
        if case_obj is not None:
            if (
                expected_case_version is not None
                and case_obj.version != expected_case_version
            ):
                logger.warning(
                    "Optimistic lock conflict for case %s: DB version %d != expected %d",
                    case_id,
                    case_obj.version,
                    expected_case_version,
                )
                return scrub_dict_phi({
                    "status": "VERSION_CONFLICT",
                    "case_id": case_id,
                    "current_version": case_obj.version,
                    "expected_version": expected_case_version,
                    "completed": False,
                })
            if case_obj.workflow_state == WorkflowState.COMPLETED and not force_rerun:
                logger.info("Case %s is already completed. Skipping workflow execution.", case_id)
                return scrub_dict_phi({
                    "status": "ALREADY_COMPLETED",
                    "case_id": case_id,
                    "decision": case_obj.current_decision.value if case_obj.current_decision else None,
                    "completed": True,
                })

    # 2. Build or Adopt CaseState
    if initial_state is not None:
        state = initial_state
    elif case_obj is not None:
        doc_refs: list[DocumentReference] = [
            DocumentReference(
                document_id=doc.document_id,
                file_name=doc.file_name,
                object_key=doc.object_key,
                mime_type=doc.mime_type,
                checksum=doc.checksum,
                ocr_payload_uri=doc.ocr_payload_uri or "",
                doc_type=doc.doc_type.value if hasattr(doc.doc_type, "value") else str(doc.doc_type),
            )
            for doc in case_obj.documents
        ]
        state = create_initial_case_state(case_id=case_id, documents=doc_refs)
    else:
        state = create_initial_case_state(case_id=case_id, documents=[])

    # 3. Coordinate Multi-Agent Workflow Execution
    active_storage = storage or InMemoryStorageBackend()
    active_coordinator = coordinator or CaseWorkflowCoordinator(
        storage=active_storage,
        db_session=db_session,
    )

    logger.info(">>> [Workflow START] Case %s: Initiating multi-agent pipeline with %d documents", case_id, len(state.get("documents", [])))

    try:
        final_state = active_coordinator.execute(state)
    except Retry:
        raise
    except TRANSIENT_EXCEPTIONS as exc:
        logger.warning(
            "Transient error in workflow for case %s: %s (attempt %d/3)",
            case_id,
            exc,
            self.request.retries + 1,
        )
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 5) from exc
    except Exception as exc:
        logger.exception(
            "Fatal unrecoverable failure in workflow for case %s",
            case_id,
        )
        # Update case to FAILED with TECHNICAL_FAILURE
        if case_obj is not None and db_session is not None:
            case_obj.workflow_state = WorkflowState.FAILED
            case_obj.current_decision = BusinessDecision.ESCALATE
            case_obj.current_sub_reason = EscalationSubReason.TECHNICAL_FAILURE
            case_obj.version += 1
            db_session.commit()

        return scrub_dict_phi({
            "status": "FAILED",
            "case_id": case_id,
            "decision": BusinessDecision.ESCALATE.value,
            "escalation_sub_reason": EscalationSubReason.TECHNICAL_FAILURE.value,
            "error": str(exc),
            "completed": False,
        })

    # 4. State Persistence & Decoupled Thread Release
    workflow_state = final_state.get("workflow_state", WorkflowState.COMPLETED.value)
    decision = final_state.get("decision")
    sub_reason = final_state.get("escalation_sub_reason")
    confidence = final_state.get("decision_confidence", 0.0)
    rule_id = final_state.get("rule_id_matched", "UNKNOWN")
    justification = final_state.get("justification", "")

    logger.info(
        "<<< [Workflow FINISH] Case %s: state=%s, decision=%s, sub_reason=%s, confidence=%.2f, rule=%s, justification=%s",
        case_id,
        workflow_state,
        decision,
        sub_reason,
        confidence,
        rule_id,
        justification,
    )

    if case_obj is not None and db_session is not None:
        case_obj.workflow_state = WorkflowState(workflow_state)
        case_obj.current_decision = BusinessDecision(decision) if decision else None
        case_obj.current_sub_reason = EscalationSubReason(sub_reason) if sub_reason else None
        case_obj.decision_confidence = Decimal(str(confidence))
        case_obj.is_complete = final_state.get("is_complete", False)
        case_obj.missing_documents = final_state.get("missing_required_documents", [])

        # Sync classified document metadata back to database records
        doc_state_map = {d["document_id"]: d for d in final_state.get("documents", [])}
        for doc in case_obj.documents:
            if doc.document_id in doc_state_map:
                s_doc = doc_state_map[doc.document_id]
                raw_type = s_doc.get("doc_type")
                if raw_type and raw_type != DocumentType.UNKNOWN.value:
                    try:
                        doc.doc_type = DocumentType(raw_type)
                    except ValueError:
                        pass
                if s_doc.get("ocr_payload_uri"):
                    doc.ocr_payload_uri = s_doc["ocr_payload_uri"]

        # Persist authoritative Decision audit record (Spec 04 § 2, Spec 06 § 2, ADR-011)
        detailed_explanation = final_state.get("detailed_explanation")
        decision_record = Decision(
            id=uuid.uuid4(),
            case_id=case_obj.id,
            decision=BusinessDecision(decision) if decision else BusinessDecision.ESCALATE,
            escalation_sub_reason=EscalationSubReason(sub_reason) if sub_reason else None,
            actor_type=ActorType.SYSTEM,
            actor_id="system-decision-engine",
            decision_confidence=Decimal(str(confidence)),
            rule_id_matched=rule_id,
            justification=justification,
            agent_summary_snapshot={
                "kyc": final_state.get("kyc_summary"),
                "claims": final_state.get("claims_summary"),
                "policy": final_state.get("policy_coverage_summary"),
                "fraud": final_state.get("fraud_summary"),
                "detailed_explanation": detailed_explanation,
            },
            case_version_at_decision=case_obj.version,
            is_active=True,
        )
        db_session.add(decision_record)

        # Persist chained audit records from workflow ledger (Spec 06, Spec 08 § 5)
        for entry in getattr(active_coordinator, "audit_ledger", []):
            try:
                ev_id = uuid.UUID(str(entry.get("event_id"))) if entry.get("event_id") else uuid.uuid4()
                raw_cat = entry.get("created_at")
                c_at = datetime.fromisoformat(raw_cat) if raw_cat else datetime.now(UTC)
                audit_rec = AuditEvent(
                    id=ev_id,
                    case_id=case_obj.id,
                    event_type="WORKFLOW_ADJUDICATION",
                    actor_type=ActorType.SYSTEM,
                    actor_id="system-decision-engine",
                    event_payload=entry.get("event_payload", {}),
                    prev_hash=entry.get("prev_hash", ""),
                    current_hash=entry.get("current_hash", ""),
                    created_at=c_at,
                )
                db_session.add(audit_rec)
            except Exception as audit_exc:  # noqa: BLE001
                logger.warning("Failed to persist audit event: %s", audit_exc)

        case_obj.version += 1
        db_session.commit()

    return scrub_dict_phi({
        "status": workflow_state,
        "case_id": case_id,
        "decision": decision,
        "escalation_sub_reason": sub_reason,
        "confidence": confidence,
        "is_complete": final_state.get("is_complete", False),
        "completed": True,
    })


@celery_app.task(
    bind=True,
    name="medishield.tasks.workflow.resume_case",
    max_retries=3,
    default_retry_delay=5,
)
def resume_case_workflow_task(
    self: Any,
    case_id: str,
    resume_reason: str,
    human_decision: str | None = None,
    reviewer_id: str | None = None,
    expected_case_version: int | None = None,
    coordinator: CaseWorkflowCoordinator | None = None,
    state: CaseState | None = None,
    db_session: Any | None = None,
) -> dict[str, Any]:
    """
    Workflow queue task: Resumes a paused workflow (AWAITING_HUMAN or AWAITING_INFO) (Spec 08 § 2.5).
    Routed to: workflow_queue.
    """
    case_obj: Case | None = None

    if db_session is not None:
        case_obj = db_session.query(Case).filter(Case.case_id == case_id).first()
        if (
            case_obj is not None
            and expected_case_version is not None
            and case_obj.version != expected_case_version
        ):
            logger.warning(
                "Optimistic lock conflict during resume for case %s: DB version %d != expected %d",
                case_id,
                case_obj.version,
                expected_case_version,
            )
            return scrub_dict_phi({
                "status": "VERSION_CONFLICT",
                "case_id": case_id,
                "current_version": case_obj.version,
                "expected_version": expected_case_version,
                "resumed": False,
            })

    active_coordinator = coordinator or CaseWorkflowCoordinator(db_session=db_session)

    # Adopt or construct state
    if state is not None:
        active_state = state
    else:
        active_state = create_initial_case_state(case_id=case_id, documents=[])
        active_state["workflow_state"] = WorkflowState.AWAITING_HUMAN.value

    try:
        if human_decision is not None:
            # Human operator override path (ADR-005)
            override_dec = BusinessDecision(human_decision)
            final_state = active_coordinator.resume_with_human_override(
                state=active_state,
                override_decision=override_dec,
                reviewer_id=reviewer_id or "OPERATIONS_REVIEWER",
                reason=resume_reason,
            )
        else:
            # Supplementary document resume path
            final_state = active_coordinator.execute(active_state)
    except Retry:
        raise
    except TRANSIENT_EXCEPTIONS as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 5) from exc
    except Exception as exc:
        logger.exception("Failed to resume workflow for case %s", case_id)
        return scrub_dict_phi({
            "status": "FAILED",
            "case_id": case_id,
            "error": str(exc),
            "resumed": False,
        })

    workflow_state = final_state.get("workflow_state", WorkflowState.COMPLETED.value)
    decision = final_state.get("decision")
    sub_reason = final_state.get("escalation_sub_reason")

    if case_obj is not None and db_session is not None:
        case_obj.workflow_state = WorkflowState(workflow_state)
        case_obj.current_decision = BusinessDecision(decision) if decision else None
        case_obj.current_sub_reason = EscalationSubReason(sub_reason) if sub_reason else None
        confidence = final_state.get("decision_confidence", 1.0)
        case_obj.decision_confidence = Decimal(str(confidence))

        if hasattr(case_obj, "decisions") and case_obj.decisions:
            for d in case_obj.decisions:
                d.is_active = False

        decision_record = Decision(
            id=uuid.uuid4(),
            case_id=case_obj.id,
            decision=BusinessDecision(decision) if decision else BusinessDecision.APPROVE,
            escalation_sub_reason=EscalationSubReason(sub_reason) if sub_reason else None,
            actor_type=ActorType.HUMAN if human_decision else ActorType.SYSTEM,
            actor_id=reviewer_id or "system-resume",
            decision_confidence=Decimal(str(confidence)),
            rule_id_matched=final_state.get("rule_id_matched", "HUMAN_OVERRIDE" if human_decision else "RESUME"),
            justification=final_state.get("justification", f"Resumed via {resume_reason}"),
            agent_summary_snapshot={
                "kyc": final_state.get("kyc_summary"),
                "claims": final_state.get("claims_summary"),
                "policy": final_state.get("policy_coverage_summary"),
                "fraud": final_state.get("fraud_summary"),
            },
            case_version_at_decision=case_obj.version,
            is_active=True,
        )
        db_session.add(decision_record)

        case_obj.version += 1
        db_session.commit()

    return scrub_dict_phi({
        "status": workflow_state,
        "case_id": case_id,
        "decision": decision,
        "escalation_sub_reason": sub_reason,
        "resumed": True,
        "completed": True,
    })
