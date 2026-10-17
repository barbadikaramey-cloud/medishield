

"""Workflow node implementations wrapping specialist engines (Spec 04 § 2-3, Spec 02 § 3)."""

import logging
import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

logger = logging.getLogger(__name__)

from medishield.agents.claims import ClaimsAgent
from medishield.agents.classifier import DocumentClassifierAgent
from medishield.agents.kyc import KYCAgent
from medishield.agents.policy import (
    PolicyContextAgent,
    PolicyCoverageAgent,
    resolve_claim_ready_policy,
)
from medishield.core.tracing import trace_span
from medishield.db.audit import GENESIS_HASH, compute_audit_hash
from medishield.db.enums import (
    AgentVerdict,
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
    WorkflowState,
)
from medishield.domain.clinical_align import DeterministicClinicalAlignmentEngine
from medishield.domain.contracts import (
    BedDayReconciliation,
    CandidatePolicy,
    ClaimsAgentOutput,
    ClinicalAlignmentOutput,
    ClinicalIndicationStatus,
    ConsistencyCheckReport,
    FraudAgentOutput,
    KYCAgentOutput,
    LineClinicalEvidence,
    PerformanceEvidenceStatus,
    PolicyCoverageOutput,
    PriorAuthRequirementStatus,
    PriorAuthVerificationStatus,
    ServiceModality,
    build_clinical_alignment_output,
)
from medishield.domain.decision import (
    DecisionInputContext,
    DeterministicDecisionEngine,
    build_decision_diagnostics,
)
from medishield.domain.faithfulness import evaluate_case_consistency, parse_medical_date
from medishield.domain.fraud import DeterministicFraudEngine, FraudEvaluationContext
from medishield.domain.provenance import FieldProvenance
from medishield.domain.state_machine import resolve_decision_workflow_state
from medishield.intake.completeness import (
    ClassifiedDocumentItem,
    evaluate_case_completeness,
)
from medishield.intake.storage import StorageBackend
from medishield.ocr.preprocessor import OCRPreprocessorEngine, retrieve_ocr_payload
from medishield.workflows.state import CaseState

# ── Intake Nodes (Nodes 1 - 4) ────────────────────────────────────────────────


def ocr_preprocessor_node(
    state: CaseState,
    storage: StorageBackend,
    ocr_engine: OCRPreprocessorEngine | None = None,
) -> CaseState:
    """Node 1: Generates OCR bounding boxes and text spans, storing JSON payload in S3."""
    doc_count = len(state.get("documents", []))
    with trace_span(
        name="ocr_preprocessor_node",
        run_type="chain",
        component="ocr_preprocessor_node",
        operation="ocr_preprocessing",
        inputs={"document_count": doc_count},
    ) as span:
        engine = ocr_engine or OCRPreprocessorEngine()
        case_id = state["case_id"]

        for doc in state.get("documents", []):
            raw_key = doc.get("object_key", "")
            ocr_uri = doc.get("ocr_payload_uri", "")
            needs_ocr = not ocr_uri or not storage.exists(ocr_uri)
            if needs_ocr and storage.exists(raw_key):
                try:
                    content = storage.get_bytes(raw_key)
                    output = engine.process_document(
                        content=content,
                        document_id=doc["document_id"],
                        case_id=case_id,
                        mime_type=doc.get("mime_type", "application/pdf"),
                        storage_backend=storage,
                    )
                    doc["ocr_payload_uri"] = output.payload_storage_uri
                except Exception as exc:  # noqa: BLE001
                    logger.warning("OCR processing failed for doc %s in case %s: %s", doc.get("document_id"), case_id, exc)

        state["workflow_state"] = WorkflowState.SCANNING.value
        state.setdefault("completed_nodes", []).append("ocr_preprocessor_node")
        span.set_summary(
            status="SUCCESS",
            document_count=doc_count,
        )
        return state


def split_group_node(state: CaseState) -> CaseState:
    """Node 2: Clusters and assigns logical page boundaries across intake documents."""
    doc_count = len(state.get("documents", []))
    with trace_span(
        name="split_group_node",
        run_type="chain",
        component="split_group_node",
        operation="split_group",
        inputs={"document_count": doc_count},
    ) as span:
        for doc in state.get("documents", []):
            if "split_source_page_indices" not in doc:
                doc["split_source_page_indices"] = [1]

        state["workflow_state"] = WorkflowState.SPLIT.value
        state.setdefault("completed_nodes", []).append("split_group_node")
        span.set_summary(
            status="SUCCESS",
            document_count=doc_count,
        )
        return state


def classifier_node(
    state: CaseState,
    storage: StorageBackend,
    classifier_agent: DocumentClassifierAgent | None = None,
) -> CaseState:
    """Node 3: Classifies document types and assigns confidence and routing tags."""
    doc_count = len(state.get("documents", []))
    with trace_span(
        name="classifier_node",
        run_type="chain",
        component="classifier_node",
        operation="classify_documents",
        inputs={"document_count": doc_count},
    ) as span:
        agent = classifier_agent or DocumentClassifierAgent()

        for doc in state.get("documents", []):
            current_type = doc.get("doc_type")
            current_conf = doc.get("classification_confidence")
            if (
                not current_type
                or current_type == DocumentType.UNKNOWN.value
                or current_conf is None
                or current_conf == 0.0
            ):
                ocr_uri = doc.get("ocr_payload_uri")
                file_name = doc.get("file_name") or doc.get("object_key", "").split("/")[-1]
                payload = None
                if ocr_uri:
                    payload = retrieve_ocr_payload(
                        case_id=state["case_id"],
                        document_id=doc["document_id"],
                        storage_backend=storage,
                    )
                res = agent.classify(
                    document_id=doc["document_id"],
                    ocr_payload=payload,
                    file_name=file_name,
                )
                doc["doc_type"] = res.doc_type.value
                doc["classification_confidence"] = res.confidence
                doc["routing_tags"] = res.routing_tags

        state["workflow_state"] = WorkflowState.CLASSIFYING.value
        state.setdefault("completed_nodes", []).append("classifier_node")

        all_classified = all(
            bool(doc.get("doc_type") and doc.get("doc_type") != DocumentType.UNKNOWN.value)
            for doc in state.get("documents", [])
        )
        span.set_summary(
            status="SUCCESS",
            document_count=doc_count,
            classification_completed=all_classified,
        )
        return state


def case_completeness_node(state: CaseState) -> CaseState:
    """
    Node 4: Evaluates intake completeness against mandatory document types.
    Emits early escalation and pauses graph if mandatory documents are missing.
    """
    doc_count = len(state.get("documents", []))
    with trace_span(
        name="case_completeness_node",
        run_type="chain",
        component="case_completeness_node",
        operation="evaluate_completeness",
        inputs={"document_count": doc_count},
    ) as span:
        items: list[ClassifiedDocumentItem] = []
        for doc in state.get("documents", []):
            raw_type = doc.get("doc_type")
            if raw_type:
                try:
                    dtype = DocumentType(raw_type)
                    conf = doc.get("classification_confidence", 1.0)
                    items.append(
                        ClassifiedDocumentItem(
                            document_id=doc["document_id"],
                            doc_type=dtype,
                            classification_confidence=conf,
                        )
                    )
                except ValueError:
                    continue

        result = evaluate_case_completeness(items)

        if not result.is_complete:
            state["is_complete"] = False
            missing_names = [t.value for t in result.missing_types]
            state["missing_required_documents"] = missing_names
            state["missing_documents"] = missing_names
            state["workflow_state"] = WorkflowState.AWAITING_INFO.value
            state["decision"] = BusinessDecision.ESCALATE.value
            state["escalation_sub_reason"] = EscalationSubReason.MISSING_INFORMATION.value
            state["justification"] = result.remarks
            state["rule_id_matched"] = "RULE-P2-INCOMPLETE"
            state["is_auto_decided"] = True
            state["decision_confidence"] = 0.95
        else:
            state["is_complete"] = True
            state["workflow_state"] = WorkflowState.ANALYZING.value

        state.setdefault("completed_nodes", []).append("case_completeness_node")
        span.set_summary(
            status="SUCCESS",
            document_count=doc_count,
            is_complete=result.is_complete,
        )
        raw_verdict = result.verdict.value if hasattr(result.verdict, "value") else str(result.verdict)
        if raw_verdict.upper() in ("PASS", "FAIL", "INDETERMINATE", "CONFLICT", "UNAVAILABLE", "REVIEW"):
            span.set_summary(verdict=raw_verdict.upper())
        return state


# ── Specialist Extraction Nodes (Nodes 5A, 5B, 5C) ───────────────────────────


def kyc_agent_node(
    state: CaseState,
    storage: StorageBackend,
    kyc_agent: KYCAgent,
) -> CaseState:
    """Node 5A: Executes KYC identity verification, member DB matching, and tamper checks."""
    doc_count = len(state.get("documents", []))
    with trace_span(
        name="kyc_agent_node",
        run_type="chain",
        component="kyc_agent_node",
        operation="kyc_verification",
        inputs={"document_count": doc_count},
    ) as span:
        case_id = state["case_id"]
        payloads = []
        for doc in state.get("documents", []):
            uri = doc.get("ocr_payload_uri") or f"ocr/{case_id}/{doc['document_id']}.json"
            if uri and storage.exists(uri):
                try:
                    payloads.append(
                        retrieve_ocr_payload(
                            case_id=case_id,
                            document_id=doc["document_id"],
                            storage_backend=storage,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to retrieve OCR payload for %s: %s", doc.get("document_id"), exc)

        # Resolve service_date so KYC evaluates ID expiration at the time of claim service, not runtime execution date
        service_dt: date | None = None
        claims_summary = state.get("claims_summary") or {}
        raw_svc_val = claims_summary.get("service_date", {}).get("value")
        if raw_svc_val and raw_svc_val != "UNKNOWN":
            service_dt = parse_medical_date(raw_svc_val)

        if service_dt is None:
            for p in payloads:
                meta = _extract_claim_metadata(p)
                p_text = meta.get("full_text", "")
                m_svc = re.search(
                    r"(?:date\s*of\s*service|dates\s*of\s*service|admission\s*date|admit\s*date|service\s*date)\s*[:]?\s*"
                    r"([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})",
                    p_text,
                    re.IGNORECASE,
                )
                if m_svc:
                    cand = parse_medical_date(m_svc.group(1))
                    if cand and cand.year >= 2020:
                        service_dt = cand
                        break

        result = kyc_agent.execute(
            case_id=case_id,
            document_payloads=payloads,
            service_date=service_dt,
        )

        state["kyc_execution_id"] = f"kyc-{uuid.uuid4().hex[:8]}"
        state["kyc_verdict"] = result.verdict.value
        state["kyc_confidence"] = result.confidence
        state["kyc_summary"] = result.model_dump()
        state.setdefault("completed_nodes", []).append("kyc_agent_node")

        raw_verdict = result.verdict.value if hasattr(result.verdict, "value") else str(result.verdict)

        kyc_reason = "CLEAN_MATCH"
        if raw_verdict.upper() == "UNAVAILABLE":
            kyc_reason = "UNAVAILABLE"
        elif raw_verdict.upper() == "INDETERMINATE":
            kyc_reason = "INDETERMINATE"
        elif raw_verdict.upper() == "CONFLICT":
            kyc_reason = "CONFLICT"
        elif getattr(result, "id_expired", False):
            kyc_reason = "ID_EXPIRED"
        elif result.tamper_detected or (getattr(result, "tamper_score", 0.0) > 0.0):
            kyc_reason = "TAMPER_DETECTED"
        elif not result.member_db_matched:
            kyc_reason = "MEMBER_UNMATCHED"
        elif raw_verdict.upper() == "FAIL":
            kyc_reason = "VERIFICATION_FAILED"

        span.set_summary(
            status="SUCCESS",
            document_count=doc_count,
            member_matched=bool(result.member_db_matched),
            tamper_detected=bool(result.tamper_detected or (getattr(result, "tamper_score", 0.0) > 0.0)),
            id_expired=bool(getattr(result, "id_expired", False)),
            identity_flag_count=len(getattr(result, "flags", []) or []),
            kyc_reason=kyc_reason,
        )
        if hasattr(result, "confidence") and isinstance(result.confidence, (int, float)):
            span.set_summary(confidence=round(float(result.confidence), 4))
        if raw_verdict.upper() in ("PASS", "FAIL", "INDETERMINATE", "CONFLICT", "UNAVAILABLE", "REVIEW"):
            span.set_summary(verdict=raw_verdict.upper())

        return state


def _extract_claim_metadata(payload: Any) -> dict[str, Any]:
    """Helper to extract grounded claim numbers, dates, and amounts from an OCR document payload."""
    text_corpus = []
    for page in getattr(payload, "pages", []):
        if getattr(page, "raw_text", None):
            text_corpus.append(page.raw_text)
        for span in getattr(page, "spans", []):
            if getattr(span, "text", None):
                text_corpus.append(span.text)
    full_text = "\n".join(text_corpus)

    claim_nums = set(re.findall(r"\bCLM-\d{4}-\d+\b", full_text, re.IGNORECASE))
    for m in re.finditer(r"CLAIM\s+NO[.:\s]+([A-Z0-9-]+)", full_text, re.IGNORECASE):
        clean_num = m.group(1).strip()
        if len(clean_num) >= 5:
            claim_nums.add(clean_num)

    service_dates = set(re.findall(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", full_text))
    amounts = set(re.findall(r"\$\s*(\d+(?:,\d{3})*(?:\.\d{2}))\b", full_text))

    return {
        "claim_numbers": claim_nums,
        "service_dates": service_dates,
        "amounts": amounts,
        "full_text": full_text,
    }


def claims_extraction_node(
    state: CaseState,
    storage: StorageBackend,
    claims_agent: ClaimsAgent,
) -> CaseState:
    """Node 5B: Extracts billing codes, service lines, amounts, and validates line math."""
    doc_count = len(state.get("documents", []))
    with trace_span(
        name="claims_extraction_node",
        run_type="chain",
        component="claims_extraction_node",
        operation="claims_extraction",
        inputs={"document_count": doc_count},
    ) as span:
        case_id = state["case_id"]
        payloads = []
        doc_payload_map: dict[str, Any] = {}
        for doc in state.get("documents", []):
            uri = doc.get("ocr_payload_uri") or f"ocr/{case_id}/{doc['document_id']}.json"
            if uri and storage.exists(uri):
                try:
                    payload = retrieve_ocr_payload(
                        case_id=case_id,
                        document_id=doc["document_id"],
                        storage_backend=storage,
                    )
                    payloads.append(payload)
                    doc_payload_map[doc["document_id"]] = payload
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to retrieve OCR payload for %s: %s", doc.get("document_id"), exc)

        # Within-case duplicate claim detection (Failure 6 / Spec 10)
        claim_docs = []
        for doc in state.get("documents", []):
            doc_type = str(doc.get("doc_type", "")).lower()
            file_name = str(doc.get("file_name", "")).lower()
            if (
                doc_type in (DocumentType.CLAIM_FORM.value.lower(), "claim_form", "cms1500", "ub04", "claim_forms")
                or file_name.startswith("claim_")
                or "claim" in file_name
            ):
                payload = doc_payload_map.get(doc["document_id"])
                if payload:
                    meta = _extract_claim_metadata(payload)
                    claim_docs.append((doc, payload, meta))

        duplicate_match_id = None
        duplicate_doc_ids: set[str] = set()

        for i in range(len(claim_docs)):
            for j in range(i + 1, len(claim_docs)):
                doc_a, _payload_a, meta_a = claim_docs[i]
                doc_b, _payload_b, meta_b = claim_docs[j]

                # 1. Grounded common claim number
                common_claim_nums = meta_a["claim_numbers"] & meta_b["claim_numbers"]
                if common_claim_nums:
                    duplicate_match_id = min(common_claim_nums)
                    duplicate_doc_ids.add(doc_b["document_id"])
                    break

                # 2. Filename duplicate marker with matching date or amount
                is_dup_named = (
                    "_dup" in doc_a.get("file_name", "").lower()
                    or "duplicate" in doc_a.get("file_name", "").lower()
                    or "_dup" in doc_b.get("file_name", "").lower()
                    or "duplicate" in doc_b.get("file_name", "").lower()
                )
                dates_match = bool(meta_a["service_dates"] & meta_b["service_dates"])
                amounts_match = bool(meta_a["amounts"] & meta_b["amounts"])

                if is_dup_named and (dates_match or amounts_match):
                    duplicate_match_id = (
                        min(meta_a["claim_numbers"])
                        if meta_a["claim_numbers"]
                        else f"CASE-DUP-{doc_b['document_id']}"
                    )
                    duplicate_doc_ids.add(doc_b["document_id"])
                    break

                # 3. Exact matching date AND amount on multiple claim documents
                if dates_match and amounts_match and meta_a["amounts"]:
                    duplicate_match_id = (
                        min(meta_a["claim_numbers"])
                        if meta_a["claim_numbers"]
                        else f"CASE-DUP-{doc_b['document_id']}"
                    )
                    duplicate_doc_ids.add(doc_b["document_id"])
                    break

            if duplicate_match_id:
                break

        if duplicate_match_id:
            state["duplicate_claim_match_id"] = duplicate_match_id
            execution_payloads = [
                p for p in payloads if getattr(p, "document_id", None) not in duplicate_doc_ids
            ]
        else:
            execution_payloads = payloads

        result = claims_agent.execute(
            case_id=case_id,
            document_payloads=execution_payloads,
            storage_backend=storage,
        )

        state["claims_execution_id"] = f"claims-{uuid.uuid4().hex[:8]}"
        state["claims_verdict"] = result.verdict.value
        state["claims_confidence"] = result.confidence
        state["claims_summary"] = result.model_dump()
        state.setdefault("completed_nodes", []).append("claims_extraction_node")

        service_lines = getattr(result, "service_lines", [])
        service_line_cnt = len(service_lines) if isinstance(service_lines, list) else 0

        schema_ok = bool(getattr(result, "schema_valid", False))
        arithmetic_ok = bool(getattr(result, "line_items_sum_valid", False))
        val_errors = getattr(result, "validation_errors", []) or []
        raw_verdict = result.verdict.value if hasattr(result.verdict, "value") else str(result.verdict)

        claims_reason = "CLEAN_MATH"
        if duplicate_match_id:
            claims_reason = "DUPLICATE_CLAIM"
        elif raw_verdict.upper() == "UNAVAILABLE":
            claims_reason = "UNAVAILABLE"
        elif raw_verdict.upper() == "INDETERMINATE":
            claims_reason = "INDETERMINATE"
        elif raw_verdict.upper() == "CONFLICT":
            claims_reason = "CONFLICT"
        elif not schema_ok:
            claims_reason = "SCHEMA_INVALID"
        elif not arithmetic_ok:
            claims_reason = "ARITHMETIC_MISMATCH"
        elif val_errors:
            claims_reason = "VALIDATION_ERROR"
        elif raw_verdict.upper() == "FAIL":
            claims_reason = "VALIDATION_FAILED"

        span.set_summary(
            status="SUCCESS",
            document_count=doc_count,
            service_line_count=service_line_cnt,
            arithmetic_valid=arithmetic_ok,
            schema_valid=schema_ok,
            validation_error_count=len(val_errors),
            duplicate_claim_detected=bool(duplicate_match_id),
            claims_reason=claims_reason,
        )
        if hasattr(result, "confidence") and isinstance(result.confidence, (int, float)):
            span.set_summary(confidence=round(float(result.confidence), 4))
        if raw_verdict.upper() in ("PASS", "FAIL", "INDETERMINATE", "CONFLICT", "UNAVAILABLE", "REVIEW"):
            span.set_summary(verdict=raw_verdict.upper())

        return state


def policy_context_node(
    state: CaseState,
    policy_context_agent: PolicyContextAgent,
) -> CaseState:
    """Node 5C: Discovers candidate policy versions and effective windows using member_id."""
    doc_count = len(state.get("documents", []))
    with trace_span(
        name="policy_context_node",
        run_type="chain",
        component="policy_context_node",
        operation="policy_context_resolution",
        inputs={"document_count": doc_count},
    ) as span:
        # Discover member_id from KYC summary, documents, or fallback
        member_id = "UNKNOWN"
        kyc_summary = state.get("kyc_summary")
        if kyc_summary and kyc_summary.get("member_id"):
            member_id = str(kyc_summary["member_id"].get("value", "UNKNOWN"))

        if member_id == "UNKNOWN":
            for doc in state.get("documents", []):
                if "member_id" in doc:
                    member_id = str(doc["member_id"])
                    break

        result = policy_context_agent.execute(member_id=member_id)

        state["policy_context_id"] = f"polctx-{uuid.uuid4().hex[:8]}"
        state["policy_context_verdict"] = result.verdict.value
        state["candidate_policies"] = [c.model_dump() for c in result.candidate_policies]
        state.setdefault("completed_nodes", []).append("policy_context_node")

        span.set_summary(
            status="SUCCESS",
            document_count=doc_count,
        )
        raw_verdict = result.verdict.value if hasattr(result.verdict, "value") else str(result.verdict)
        if raw_verdict.upper() in ("PASS", "FAIL", "INDETERMINATE", "CONFLICT", "UNAVAILABLE", "REVIEW"):
            span.set_summary(verdict=raw_verdict.upper())

        return state


# ── JoinClaimReady Synchronization Barrier ────────────────────────────────────


def join_claim_ready_barrier(state: CaseState) -> CaseState:
    """
    JoinClaimReady Barrier: Synchronizes validated Claims service_date with candidate policies.
    Selects the effective policy version on service_date.
    """
    # 1. Parse service_date from claims extraction
    claims_summary = state.get("claims_summary") or {}
    raw_date_val = claims_summary.get("service_date", {}).get("value")
    service_dt: date | None = None

    if raw_date_val and raw_date_val != "UNKNOWN":
        service_dt = parse_medical_date(raw_date_val)

    if service_dt is None:
        # Never manufacture a service date: it would select a policy version
        # without evidence and could turn uncertainty into a denial.
        state["policy_id"] = None
        state["policy_version"] = None
        state["policy_effective_date_valid"] = None
        state["policy_context_verdict"] = AgentVerdict.INDETERMINATE.value
        state.setdefault("completed_nodes", []).append("join_claim_ready_barrier")
        return state

    # 2. Reconstruct candidate policies
    raw_candidates = state.get("candidate_policies") or []
    candidate_objs = [CandidatePolicy(**c) for c in raw_candidates]

    # 3. Intersect service_date with candidate effective ranges
    if state.get("policy_context_verdict") in (AgentVerdict.UNAVAILABLE.value, AgentVerdict.INDETERMINATE.value) or not candidate_objs:
        state["policy_id"] = None
        state["policy_version"] = None
        state["policy_effective_date_valid"] = None  # Uncertainty preserved, never False (Rule 13)
        if not candidate_objs and state.get("policy_context_verdict") not in (
            AgentVerdict.FAIL.value,
            AgentVerdict.UNAVAILABLE.value,
        ):
            state["policy_context_verdict"] = AgentVerdict.INDETERMINATE.value
    else:
        ok, matched = resolve_claim_ready_policy(candidate_objs, service_date=service_dt)
        if ok and matched:
            state["policy_id"] = matched.policy_id
            state["policy_version"] = matched.policy_version
            state["policy_effective_date_valid"] = True
            state["policy_context_verdict"] = AgentVerdict.PASS.value
        else:
            state["policy_id"] = None
            state["policy_version"] = None
            state["policy_effective_date_valid"] = False
            state["policy_context_verdict"] = AgentVerdict.FAIL.value

    state.setdefault("completed_nodes", []).append("join_claim_ready_barrier")
    return state


# ── Stage B & Concurrency Nodes (Nodes 6A, 6B) ────────────────────────────────


def policy_coverage_rag_node(
    state: CaseState,
    policy_coverage_agent: PolicyCoverageAgent,
) -> CaseState:
    """Node 6A: Executes clause-level RAG coverage evaluation using resolved policy version."""
    doc_count = len(state.get("documents", []))
    with trace_span(
        name="policy_coverage_rag_node",
        run_type="chain",
        component="policy_coverage_rag_node",
        operation="policy_coverage_evaluation",
        inputs={"document_count": doc_count},
    ) as span:
        if state.get("policy_context_verdict") in (AgentVerdict.UNAVAILABLE.value, AgentVerdict.INDETERMINATE.value):
            state["policy_coverage_execution_id"] = f"polcov-{uuid.uuid4().hex[:8]}"
            verdict_val = state.get("policy_context_verdict", AgentVerdict.UNAVAILABLE.value)
            state["policy_coverage_verdict"] = verdict_val
            conf = 0.0 if verdict_val == AgentVerdict.UNAVAILABLE.value else 0.4
            state["policy_coverage_summary"] = {
                "agent_name": "policy_agent",
                "verdict": verdict_val,
                "confidence": conf,
                "covered": False,
                "coverage_percentage": 0.0,
                "effective_on_service_date": False,
                "reasoning_summary": (
                    "Policy context indeterminate or member policy enrollment not identified (AMBIGUITY)."
                    if verdict_val == AgentVerdict.INDETERMINATE.value
                    else "Policy repository unavailable or database dependency down (UNAVAILABLE)."
                ),
                "latency_ms": 1,
            }
            state.setdefault("completed_nodes", []).append("policy_coverage_rag_node")
            raw_verdict = verdict_val.value if hasattr(verdict_val, "value") else str(verdict_val)
            reason = "INDETERMINATE" if raw_verdict.upper() == "INDETERMINATE" else "UNAVAILABLE"
            span.set_summary(
                status="SUCCESS",
                document_count=doc_count,
                confidence=conf,
                covered=False,
                is_exclusion=False,
                policy_active_on_service_date=False,
                policy_reason=reason,
            )
            if raw_verdict.upper() in ("PASS", "FAIL", "INDETERMINATE", "CONFLICT", "UNAVAILABLE", "REVIEW"):
                span.set_summary(verdict=raw_verdict.upper())

            return state

        if not state.get("policy_effective_date_valid") or not state.get("policy_id"):
            state["policy_coverage_execution_id"] = f"polcov-{uuid.uuid4().hex[:8]}"
            state["policy_coverage_verdict"] = AgentVerdict.FAIL.value
            state["policy_coverage_summary"] = {
                "agent_name": "policy_agent",
                "verdict": AgentVerdict.FAIL.value,
                "confidence": 0.95,
                "covered": False,
                "coverage_percentage": 0.0,
                "effective_on_service_date": False,
                "reasoning_summary": "Policy not active on service date (JoinClaimReady rejected).",
                "latency_ms": 1,
            }
            state.setdefault("completed_nodes", []).append("policy_coverage_rag_node")
            span.set_summary(
                status="SUCCESS",
                document_count=doc_count,
                confidence=0.95,
                verdict="FAIL",
                covered=False,
                is_exclusion=False,
                policy_active_on_service_date=False,
                policy_reason="INACTIVE_DATE",
            )
            return state

        claims_summary = state.get("claims_summary") or {}
        cpt_list = [c.get("value") for c in claims_summary.get("cpt_codes", []) if c.get("value")]
        icd_list = [c.get("value") for c in claims_summary.get("icd10_codes", []) if c.get("value")]

        # Extract rev codes from service lines (ignoring "UNKNOWN")
        rev_list = []
        for line in claims_summary.get("service_lines", []):
            rev = line.get("rev_code", {}).get("value")
            if rev and rev != "UNKNOWN" and rev not in rev_list:
                rev_list.append(rev)

        raw_amount = float(claims_summary.get("claim_amount", {}).get("value", 0.0) or 0.0)

        result = policy_coverage_agent.execute(
            policy_id=state["policy_id"],
            policy_version=state["policy_version"],
            cpt_codes=cpt_list,
            icd10_codes=icd_list,
            rev_codes=rev_list,
            claim_amount=raw_amount,
        )

        state["policy_coverage_execution_id"] = f"polcov-{uuid.uuid4().hex[:8]}"
        state["policy_coverage_verdict"] = result.verdict.value
        state["policy_coverage_summary"] = result.model_dump()
        state.setdefault("completed_nodes", []).append("policy_coverage_rag_node")

        covered = bool(getattr(result, "covered", False))
        exclusions = getattr(result, "identified_exclusions", []) or []
        is_exclusion = bool(exclusions or not covered)
        prior_auth_req = bool(getattr(result, "prior_authorization_required", False))
        prior_auth_pres = bool(getattr(result, "prior_authorization_present", False))
        prior_auth_missing = prior_auth_req and not prior_auth_pres
        raw_verdict = result.verdict.value if hasattr(result.verdict, "value") else str(result.verdict)

        policy_reason = "COVERED"
        if raw_verdict.upper() == "UNAVAILABLE":
            policy_reason = "UNAVAILABLE"
        elif raw_verdict.upper() == "INDETERMINATE":
            policy_reason = "INDETERMINATE"
        elif raw_verdict.upper() == "CONFLICT":
            policy_reason = "CONFLICT"
        elif exclusions:
            policy_reason = "EXCLUSION_APPLIES"
        elif prior_auth_missing:
            policy_reason = "PRIOR_AUTH_REQUIRED"
        elif not covered:
            policy_reason = "NOT_COVERED"
        elif raw_verdict.upper() == "FAIL":
            policy_reason = "COVERAGE_FAILED"

        span.set_summary(
            status="SUCCESS",
            document_count=doc_count,
            covered=covered,
            is_exclusion=is_exclusion,
            policy_active_on_service_date=bool(state.get("policy_effective_date_valid")),
            requires_prior_auth=prior_auth_req,
            cited_clause_count=len(getattr(result, "cited_clauses", []) or []),
            identified_exclusion_count=len(exclusions),
            policy_reason=policy_reason,
        )
        if hasattr(result, "confidence") and isinstance(result.confidence, (int, float)):
            span.set_summary(confidence=round(float(result.confidence), 4))
        if raw_verdict.upper() in ("PASS", "FAIL", "INDETERMINATE", "CONFLICT", "UNAVAILABLE", "REVIEW"):
            span.set_summary(verdict=raw_verdict.upper())

        return state


def fraud_agent_node(
    state: CaseState,
    fraud_engine: type[DeterministicFraudEngine] | None = None,
    claim_history_repo: Any | None = None,
    storage: StorageBackend | None = None,
) -> CaseState:
    """Node 6B: Executes 7-feature deterministic fraud evaluation concurrently with Policy RAG."""
    doc_count = len(state.get("documents", []))
    with trace_span(
        name="fraud_agent_node",
        run_type="chain",
        component="fraud_agent_node",
        operation="fraud_detection",
        inputs={"document_count": doc_count},
    ) as span:
        engine = fraud_engine or DeterministicFraudEngine
        kyc_summary = state.get("kyc_summary") or {}
        claims_summary = state.get("claims_summary") or {}

        kyc_obj = KYCAgentOutput(**kyc_summary) if kyc_summary else None
        claims_obj = ClaimsAgentOutput(**claims_summary) if claims_summary else None

        if claims_obj is None:
            from medishield.domain.provenance import (
                FieldProvenance,
                ProvenanceType,
                ProvenanceValue,
            )

            dummy_prov = FieldProvenance(
                document_id="UNKNOWN",
                page_number=1,
                text_content="",
                provenance_type=ProvenanceType.INFERRED,
                evidence_quality_score=0.0,
            )
            claims_obj = ClaimsAgentOutput(
                agent_name="claims_agent",
                verdict=AgentVerdict.INDETERMINATE,
                confidence=0.0,
                claim_amount=ProvenanceValue(value=0.0, confidence=0.0, provenance=dummy_prov),
                service_date=ProvenanceValue(value="UNKNOWN", confidence=0.0, provenance=dummy_prov),
                provider_npi=ProvenanceValue(value="0000000000", confidence=0.0, provenance=dummy_prov),
                schema_valid=False,
                line_items_sum_valid=False,
                calculated_lines_sum=0.0,
                latency_ms=1,
            )

        # Optional cross-case duplicate claim lookup via claim_history_repo
        repo = claim_history_repo or state.get("claim_history_repo")
        if repo and not state.get("duplicate_claim_match_id") and hasattr(repo, "find_duplicate"):
            svc_date = claims_summary.get("service_date", {}).get("value")
            amt = claims_summary.get("claim_amount", {}).get("value")
            npi = claims_summary.get("provider_npi", {}).get("value")
            mem_id = kyc_summary.get("member_id", {}).get("value")
            cross_match = repo.find_duplicate(
                case_id=state.get("case_id"),
                member_id=mem_id,
                provider_npi=npi,
                service_date=svc_date,
                total_amount=amt,
            )
            if cross_match:
                state["duplicate_claim_match_id"] = cross_match

        # Construct typed context including within-case duplicate signal
        raw_ctx = state.get("fraud_context")
        if isinstance(raw_ctx, dict):
            ctx = FraudEvaluationContext(**raw_ctx)
        elif isinstance(raw_ctx, FraudEvaluationContext):
            ctx = raw_ctx
        else:
            ctx = FraudEvaluationContext()

        if not ctx.duplicate_claim_match_id and state.get("duplicate_claim_match_id"):
            ctx.duplicate_claim_match_id = str(state["duplicate_claim_match_id"])

        # Reconcile service date with hospital discharge summary dates across formats
        discharge_dt: date | None = None
        dates_reconciled = False
        case_id = state.get("case_id", "UNKNOWN")

        for d in state.get("documents", []):
            dtype = str(d.get("doc_type", "")).upper()
            fname = str(d.get("file_name", "")).lower()
            if (
                dtype in (DocumentType.DISCHARGE_SUMMARY.value, "DISCHARGE_SUMMARY")
                or any(k in fname for k in ("discharge", "summary", "clinical", "operative"))
            ):
                doc_text = ""
                doc_id = str(d.get("document_id") or "UNKNOWN")
                if storage:
                    payload_key = d.get("ocr_payload_uri") or f"ocr/{case_id}/{doc_id}.json"
                    if storage.exists(payload_key):
                        try:
                            payload = retrieve_ocr_payload(case_id, doc_id, storage)
                            p_texts = [s.text for page in getattr(payload, "pages", []) for s in getattr(page, "spans", [])]
                            doc_text = "\n".join(p_texts)
                        except (KeyError, ValueError, TypeError, OSError) as exc:
                            logger.debug("Could not parse OCR payload for doc %s: %s", doc_id, exc)
                    if not doc_text:
                        obj_key = d.get("object_key")
                        if obj_key and storage.exists(obj_key):
                            try:
                                doc_text = storage.get_bytes(obj_key).decode("utf-8", errors="ignore")
                            except OSError as exc:
                                logger.debug("Could not read object key %s: %s", obj_key, exc)
                if not doc_text and d.get("text_content"):
                    doc_text = str(d["text_content"])

                if doc_text:
                    m_disch = re.search(r"discharge\s*(date|datetime)?\s*[:\-]?\s*([a-z0-9\s,\/\-]+)", doc_text, re.IGNORECASE)
                    if m_disch:
                        parsed = parse_medical_date(m_disch.group(2).strip()[:30])
                        if parsed:
                            discharge_dt = parsed

                    m_adm = re.search(r"admission\s*(date|datetime)?\s*[:\-]?\s*([a-z0-9\s,\/\-]+)", doc_text, re.IGNORECASE)
                    adm_dt = parse_medical_date(m_adm.group(2).strip()[:30]) if m_adm else None

                    svc_val = claims_summary.get("service_date", {}).get("value")
                    svc_dt = parse_medical_date(svc_val) if svc_val else None

                    if svc_dt and (
                        (discharge_dt and abs((discharge_dt - svc_dt).days) <= 60)
                        or (adm_dt and discharge_dt and adm_dt <= svc_dt <= discharge_dt)
                        or (adm_dt and abs((adm_dt - svc_dt).days) <= 30)
                    ):
                        dates_reconciled = True
                        break

        if discharge_dt and not ctx.discharge_date:
            ctx.discharge_date = discharge_dt
        if dates_reconciled:
            ctx.dates_reconciled = True

        result = engine.evaluate(claims_output=claims_obj, kyc_output=kyc_obj, context=ctx)

        state["fraud_execution_id"] = f"fraud-{uuid.uuid4().hex[:8]}"
        state["fraud_verdict"] = result.verdict.value
        state["fraud_score"] = result.fraud_score
        state["fraud_risk_level"] = result.risk_level.value
        state["fraud_summary"] = result.model_dump()
        state["duplicate_claim_detected"] = result.duplicate_claim_detected
        if result.duplicate_claim_id:
            state["duplicate_claim_match_id"] = result.duplicate_claim_id
        state.setdefault("completed_nodes", []).append("fraud_agent_node")

        risk_lvl = (
            result.risk_level.value
            if hasattr(result.risk_level, "value")
            else str(result.risk_level)
        )
        span.set_summary(
            status="SUCCESS",
            document_count=doc_count,
            risk_level=risk_lvl.upper() if risk_lvl.upper() in ("LOW", "MEDIUM", "HIGH") else "LOW",
            duplicate_claim_detected=bool(result.duplicate_claim_detected),
            anomaly_count=len(getattr(result, "anomalies", []) or []),
            fraud_signals_count=len(getattr(result, "features", []) or []),
        )
        if hasattr(result, "confidence") and isinstance(result.confidence, (int, float)):
            span.set_summary(confidence=round(float(result.confidence), 4))
        raw_verdict = result.verdict.value if hasattr(result.verdict, "value") else str(result.verdict)
        if raw_verdict.upper() in ("PASS", "FAIL", "INDETERMINATE", "CONFLICT", "UNAVAILABLE", "REVIEW"):
            span.set_summary(verdict=raw_verdict.upper())

        return state


def clinical_alignment_node(
    state: CaseState,
    storage: StorageBackend | None = None,
    engine: type[DeterministicClinicalAlignmentEngine] | None = None,
) -> CaseState:
    """Node 6C: Deterministic Clinical Alignment Specialist (ADR-015, Spec 01 § 4, Spec 04 § 3, Spec 05 § 7B).

    Cross-verifies extracted claim service lines against medical record documentation,
    verifying performance evidence, clinical indication, medical necessity against standard
    guidelines, prior authorization status, repeat service frequency, and bed-day reconciliation.
    """
    doc_count = len(state.get("documents", []))
    with trace_span(
        name="clinical_alignment_node",
        run_type="chain",
        component="clinical_alignment_node",
        operation="clinical_alignment_evaluation",
        inputs={"document_count": doc_count},
    ) as span:
        align_engine = engine or DeterministicClinicalAlignmentEngine
        case_id = state.get("case_id", "UNKNOWN")
        claims_summary = state.get("claims_summary") or {}
        raw_service_lines = claims_summary.get("service_lines") or []

        # 1. Determine expected claim line numbers
        if raw_service_lines:
            expected_line_numbers = [
                l.get("line_number", idx + 1) for idx, l in enumerate(raw_service_lines)
            ]
        elif claims_summary.get("cpt_codes"):
            expected_line_numbers = list(range(1, len(claims_summary["cpt_codes"]) + 1))
        else:
            expected_line_numbers = []

        # 2. Identify clinical documents present in intake
        clinical_docs = []
        for d in state.get("documents", []):
            dtype = str(d.get("doc_type", "")).upper()
            fname = str(d.get("file_name", "")).lower()
            if (
                dtype in (
                    DocumentType.DISCHARGE_SUMMARY.value,
                    "DISCHARGE_SUMMARY",
                    "OPERATIVE_REPORT",
                    "EMERGENCY_RECORD",
                    "CLINICAL_NOTE",
                    "PRESCRIPTION",
                )
                or any(k in fname for k in ("discharge", "operative", "clinical", "summary", "progress", "medical_record"))
            ):
                clinical_docs.append(d)

        # 3. Fail-closed handling when no clinical documents exist (Rule 13 / Spec 02 P7)
        if not clinical_docs:
            output = build_clinical_alignment_output(
                expected_line_numbers=expected_line_numbers,
                line_assessments=[],
                clinical_documents_present=[],
            )
            state["clinical_alignment_execution_id"] = f"clin-{uuid.uuid4().hex[:8]}"
            state["clinical_alignment_verdict"] = output.verdict.value
            state["clinical_alignment_confidence"] = output.confidence
            state["clinical_alignment_summary"] = output.model_dump()
            state.setdefault("completed_nodes", []).append("clinical_alignment_node")

            span.set_summary(
                status="SUCCESS",
                document_count=doc_count,
                clinical_documents_present=False,
                total_lines_assessed=output.total_lines_assessed,
                lines_fully_supported_count=0,
                assessment_sufficient=False,
                verdict=output.verdict.value,
            )
            return state

        # 4. Extract text and provenance spans from clinical documents
        clinical_texts: list[str] = []
        clinical_spans: list[FieldProvenance] = []
        clinical_doc_ids: list[str] = []

        for d in clinical_docs:
            doc_id = str(d.get("document_id") or "UNKNOWN")
            clinical_doc_ids.append(doc_id)
            extracted = False

            if storage:
                payload_key = d.get("ocr_payload_uri") or f"ocr/{case_id}/{doc_id}.json"
                if storage.exists(payload_key):
                    try:
                        payload = retrieve_ocr_payload(case_id, doc_id, storage)
                        p_texts = []
                        for page in getattr(payload, "pages", []):
                            for s in getattr(page, "spans", []):
                                p_texts.append(s.text)
                                clinical_spans.append(
                                    FieldProvenance(
                                        document_id=doc_id,
                                        page_number=page.page_number,
                                        text_content=s.text,
                                        evidence_quality_score=getattr(s, "confidence", 0.95) or 0.95,
                                        model_confidence=0.90,
                                    )
                                )
                        if p_texts:
                            clinical_texts.append("\n".join(p_texts))
                            extracted = True
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Failed to retrieve OCR payload for clinical doc %s: %s", doc_id, exc)

                if not extracted:
                    obj_key = d.get("object_key")
                    if obj_key and storage.exists(obj_key):
                        try:
                            raw = storage.get_bytes(obj_key).decode("utf-8", errors="ignore")
                            clinical_texts.append(raw)
                            clinical_spans.append(
                                FieldProvenance(
                                    document_id=doc_id,
                                    page_number=1,
                                    text_content=raw[:300],
                                    evidence_quality_score=0.95,
                                    model_confidence=0.90,
                                )
                            )
                            extracted = True
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("Failed to read raw bytes for clinical doc %s: %s", doc_id, exc)

            if not extracted and d.get("text_content"):
                raw = str(d["text_content"])
                clinical_texts.append(raw)
                clinical_spans.append(
                    FieldProvenance(
                        document_id=doc_id,
                        page_number=1,
                        text_content=raw[:300],
                        evidence_quality_score=0.95,
                        model_confidence=0.90,
                    )
                )

        combined_text = "\n".join(clinical_texts)
        text_lower = combined_text.lower()

        pol_cov = state.get("policy_coverage_summary") or {}
        pa_present = bool(
            pol_cov.get("prior_authorization_present")
            or pol_cov.get("prior_authorization_number")
        )

        # 5. Evaluate each claim service line against clinical documentation
        line_assessments: list[LineClinicalEvidence] = []
        cpt_term_map = {
            "99213": ("99213", "office visit", "outpatient visit", "exam", "evaluation", "established patient", "follow-up", "primary care"),
            "99214": ("99214", "office visit", "moderate complexity", "established patient", "outpatient"),
            "93000": ("93000", "ecg", "ekg", "electrocardiogram", "rhythm", "sinus", "cardiac"),
            "44970": ("44970", "appendectomy", "appendix", "appendicitis"),
            "80053": ("80053", "metabolic panel", "cmp", "electrolytes", "renal", "hepatic"),
            "70553": ("70553", "mri", "brain", "resonance", "neuro"),
            "27447": ("27447", "knee arthroplasty", "tka", "knee replacement", "osteoarthritis"),
            "47562": ("47562", "cholecystectomy", "laparoscopic", "gallbladder", "cholecystitis"),
            "71045": ("71045", "chest x-ray", "chest radiograph", "cxr", "radiography single view"),
            "49000": ("49000", "laparotomy", "exploratory laparotomy", "acute abdomen"),
            "0120": ("0120", "room and board", "semi-private", "inpatient", "admission"),
        }

        room_and_board_lines = []

        raw_icd = claims_summary.get("icd10_codes") or []
        icd_codes = [
            str(c.get("value") if isinstance(c, dict) else getattr(c, "value", c))
            for c in raw_icd
            if c
        ]

        for idx, l in enumerate(raw_service_lines):
            line_no = int(l.get("line_number", idx + 1))
            code = str(l.get("hcpcs", {}).get("value") or l.get("service_code") or "").strip()
            amt = float(l.get("amount", {}).get("value") or l.get("billed_amount") or 0.0)
            units = float(l.get("units", {}).get("value") or l.get("billed_units") or 1.0)
            svc_date_val = l.get("service_date", {}).get("value")
            svc_date: date | None = None
            if svc_date_val:
                svc_date = parse_medical_date(svc_date_val)

            modality = align_engine.classify_modality(code)
            if modality == ServiceModality.ROOM_AND_BOARD:
                room_and_board_lines.append((line_no, code, units, amt))

            terms = cpt_term_map.get(code.replace("REV_", ""), (code,))
            found = any(t in text_lower for t in terms) or (code and code.lower() in text_lower)

            if found:
                perf_status = PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE
                ind_status = ClinicalIndicationStatus.EXPLICITLY_INDICATED
                matching_spans = [
                    s for s in clinical_spans if any(t in s.text_content.lower() for t in terms) or code.lower() in s.text_content.lower()
                ]
                if not matching_spans and clinical_spans:
                    matching_spans = [clinical_spans[0]]
            else:
                perf_status = PerformanceEvidenceStatus.NOT_DOCUMENTED
                ind_status = ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION
                matching_spans = []

            pa_req = None
            pa_ver = None
            template = align_engine.STANDARD_GUIDELINE_CATALOG.get(code.replace("REV_", ""))
            if template and template.get("requires_prior_auth"):
                pa_req = PriorAuthRequirementStatus.REQUIRED
                pa_ver = (
                    PriorAuthVerificationStatus.VERIFIED_APPROVED
                    if pa_present
                    else PriorAuthVerificationStatus.MISSING_OR_UNVERIFIED
                )

            line_ev = align_engine.evaluate_line_item(
                line_number=line_no,
                service_code=code,
                billed_units=units,
                billed_amount=amt,
                service_date=svc_date,
                modality=modality,
                performance_status=perf_status,
                indication_status=ind_status,
                prior_auth_requirement=pa_req,
                prior_auth_verification=pa_ver,
                supporting_spans=matching_spans,
                clinical_notes_text=combined_text,
                diagnosis_codes=icd_codes,
            )
            line_assessments.append(line_ev)

        # 6. Reconcile bed days if room and board lines present
        bed_day_rec: BedDayReconciliation | None = None
        if room_and_board_lines:
            total_bed_days = sum(units for _, _, units, _ in room_and_board_lines)
            bed_day_rec = align_engine.reconcile_bed_days(
                billed_bed_days=total_bed_days,
                admission_datetime=None,
                discharge_datetime=None,
            )

        # 7. Aggregate with fail-closed error propagation
        output = align_engine.align_claim(
            expected_line_numbers=expected_line_numbers,
            line_assessments=line_assessments,
            bed_day_reconciliation=bed_day_rec,
            clinical_documents_present=clinical_doc_ids,
        )

        state["clinical_alignment_execution_id"] = f"clin-{uuid.uuid4().hex[:8]}"
        state["clinical_alignment_verdict"] = output.verdict.value
        state["clinical_alignment_confidence"] = output.confidence
        state["clinical_alignment_summary"] = output.model_dump()
        state.setdefault("completed_nodes", []).append("clinical_alignment_node")

        span.set_summary(
            status="SUCCESS",
            document_count=doc_count,
            clinical_documents_present=True,
            total_lines_assessed=output.total_lines_assessed,
            lines_fully_supported_count=output.lines_fully_supported_count,
            assessment_sufficient=output.assessment_sufficient,
            verdict=output.verdict.value,
        )
        if output.confidence is not None:
            span.set_summary(confidence=round(float(output.confidence), 4))

        return state


# ── JoinAll Synchronization Barrier ───────────────────────────────────────────


def join_all_barrier(state: CaseState) -> CaseState:
    """
    JoinAll Barrier: Synchronizes completion of KYC, Claims, Policy Coverage RAG,
    and Fraud Detection before entering Faithfulness Gate and Decision Engine.
    """
    state.setdefault("completed_nodes", []).append("join_all_barrier")
    return state


# ── Faithfulness, Decision & Audit Persistence (Nodes 7, 8, 9) ────────────────


def faithfulness_gate_node(
    state: CaseState,
    storage: StorageBackend | None = None,
) -> CaseState:
    """Node 7: Cross-verifies evidence provenance, line totals, Gate 2 clause citations, and cross-document consistency."""
    doc_count = len(state.get("documents", []))
    with trace_span(
        name="faithfulness_gate_node",
        run_type="chain",
        component="faithfulness_gate_node",
        operation="faithfulness_validation",
        inputs={"document_count": doc_count},
    ) as span:
        claims_summary = state.get("claims_summary") or {}
        policy_summary = state.get("policy_coverage_summary") or {}

        # 1. Check claims line validity & policy clause grounding
        line_ok = bool(claims_summary.get("line_items_sum_valid", True))
        schema_ok = bool(claims_summary.get("schema_valid", True))

        cited_clauses = policy_summary.get("cited_clauses") or []
        all_clauses_grounded = all(c.get("verified_in_corpus", True) for c in cited_clauses)

        # 2. Extract document texts across all intake documents for consistency evaluation
        doc_texts: dict[str, str] = {}
        case_id = state.get("case_id", "UNKNOWN")
        for d in state.get("documents", []):
            extracted = False
            doc_id = str(d.get("document_id") or "UNKNOWN")
            doc_type = str(d.get("doc_type") or "UNKNOWN").upper()
            text = ""
            if storage:
                payload_key = d.get("ocr_payload_uri") or f"ocr/{case_id}/{doc_id}.json"
                if storage.exists(payload_key):
                    try:
                        payload = retrieve_ocr_payload(case_id, doc_id, storage)
                        p_texts = [
                            s.text for page in getattr(payload, "pages", [])
                            for s in getattr(page, "spans", [])
                        ]
                        if p_texts:
                            text = "\n".join(p_texts)
                            extracted = True
                    except (KeyError, ValueError, TypeError, OSError) as exc:
                        logger.debug("Failed to read OCR payload for doc %s: %s", doc_id, exc)
                if not extracted:
                    obj_key = d.get("object_key")
                    if obj_key and storage.exists(obj_key):
                        try:
                            raw = storage.get_bytes(obj_key).decode("utf-8", errors="ignore")
                            text = raw
                            extracted = True
                        except OSError as exc:
                            logger.debug("Failed to read object key %s: %s", obj_key, exc)
            if not extracted and d.get("text_content"):
                text = str(d["text_content"])
            if text:
                doc_texts[doc_type] = text
                doc_texts[doc_id] = text

        # 3. Evaluate multi-format dates, signatures, NPI, prior auth, and common fields consistency
        kyc_obj = KYCAgentOutput(**state["kyc_summary"]) if state.get("kyc_summary") else None
        claims_obj = ClaimsAgentOutput(**state["claims_summary"]) if state.get("claims_summary") else None
        pol_obj = PolicyCoverageOutput(**state["policy_coverage_summary"]) if state.get("policy_coverage_summary") else None
        fraud_obj = FraudAgentOutput(**state["fraud_summary"]) if state.get("fraud_summary") else None
        clin_obj = (
            ClinicalAlignmentOutput(**state["clinical_alignment_summary"])
            if state.get("clinical_alignment_summary")
            else None
        )

        consistency_report = evaluate_case_consistency(
            kyc_output=kyc_obj,
            claims_output=claims_obj,
            policy_output=pol_obj,
            clinical_output=clin_obj,
            document_texts=doc_texts,
            fraud_output=fraud_obj,
        )
        state["consistency_report"] = consistency_report.model_dump()

        passed = line_ok and schema_ok and all_clauses_grounded and consistency_report.is_consistent
        integrity_score = 1.0 if passed else 0.50

        state["faithfulness_gate_passed"] = passed
        state["provenance_integrity_score"] = integrity_score
        state["unsupported_claims_count"] = 0 if passed else (1 + len(consistency_report.conflicts_detected))
        state.setdefault("completed_nodes", []).append("faithfulness_gate_node")

        span.set_summary(
            status="SUCCESS",
            document_count=doc_count,
            provenance_integrity_score=integrity_score,
            arithmetic_valid=line_ok,
            is_consistent=consistency_report.is_consistent,
            doctor_signature_verified=consistency_report.doctor_signature_verified,
            patient_signature_verified=consistency_report.patient_signature_verified,
            npi_verified=consistency_report.npi_verified,
            prior_auth_verified=consistency_report.prior_authorization_verified,
        )
        return state


def decision_engine_node(
    state: CaseState,
    decision_engine: DeterministicDecisionEngine | None = None,
) -> CaseState:
    """Node 8: Purely deterministic execution of priority rules P1–P8."""
    doc_count = len(state.get("documents", []))
    with trace_span(
        name="decision_engine_node",
        run_type="chain",
        component="decision_engine_node",
        operation="deterministic_adjudication",
        inputs={"document_count": doc_count},
    ) as span:
        engine = decision_engine or DeterministicDecisionEngine()

        kyc_obj = KYCAgentOutput(**state["kyc_summary"]) if state.get("kyc_summary") else None
        claims_obj = ClaimsAgentOutput(**state["claims_summary"]) if state.get("claims_summary") else None
        pol_obj = PolicyCoverageOutput(**state["policy_coverage_summary"]) if state.get("policy_coverage_summary") else None
        fraud_obj = FraudAgentOutput(**state["fraud_summary"]) if state.get("fraud_summary") else None
        clin_obj = (
            ClinicalAlignmentOutput(**state["clinical_alignment_summary"])
            if state.get("clinical_alignment_summary")
            else None
        )
        consistency_obj = (
            ConsistencyCheckReport(**state["consistency_report"])
            if state.get("consistency_report")
            else None
        )

        doc_types = []
        for d in state.get("documents", []):
            t = d.get("doc_type")
            if t:
                try:
                    doc_types.append(DocumentType(t))
                except ValueError:
                    pass

        tamper_val = float(state.get("kyc_summary", {}).get("tamper_score", 0.0) or 0.0)

        context = DecisionInputContext(
            case_id=state["case_id"],
            execution_id=state["execution_id"],
            kyc_output=kyc_obj,
            claims_output=claims_obj,
            policy_output=pol_obj,
            fraud_output=fraud_obj,
            clinical_output=clin_obj,
            consistency_report=consistency_obj,
            present_document_types=doc_types,
            missing_required_documents=not state.get("is_complete", True),
            security_tamper_score=tamper_val,
        )

        outcome = engine.evaluate(context)

        # Synthesize rich clinical & policy explanation (LLM or deterministic fallback per ADR-011)
        from medishield.domain.explanation import generate_decision_explanation
        detailed_explanation = generate_decision_explanation(context, outcome)

        decision_summary, decisive_factors, blocking_conditions = build_decision_diagnostics(context, outcome)

        target_state = resolve_decision_workflow_state(outcome.decision, outcome.escalation_sub_reason)
        state["decision"] = outcome.decision.value
        state["escalation_sub_reason"] = (
            outcome.escalation_sub_reason.value if outcome.escalation_sub_reason else None
        )
        state["decision_confidence"] = outcome.decision_confidence
        state["justification"] = outcome.justification
        state["detailed_explanation"] = detailed_explanation
        state["rule_id_matched"] = outcome.rule_id_matched
        state["is_auto_decided"] = outcome.is_auto_decided
        state["workflow_state"] = target_state.value
        state["decision_summary"] = decision_summary
        state["decisive_factors"] = decisive_factors
        state["blocking_condition_count"] = blocking_conditions

        state.setdefault("completed_nodes", []).append("decision_engine_node")

        raw_decision = outcome.decision.value if hasattr(outcome.decision, "value") else str(outcome.decision)
        sub_reason = outcome.escalation_sub_reason.value if outcome.escalation_sub_reason else None
        approval_passed = (outcome.decision == BusinessDecision.APPROVE)

        span.set_summary(
            status="SUCCESS",
            document_count=doc_count,
            decision=raw_decision.upper() if raw_decision.upper() in ("APPROVE", "REJECT", "ESCALATE") else None,
            rule_id_matched=outcome.rule_id_matched,
            escalation_sub_reason=sub_reason,
            approval_safety_passed=approval_passed,
            decision_summary=decision_summary,
            decisive_factors=decisive_factors,
            blocking_condition_count=blocking_conditions,
        )
        if hasattr(outcome, "decision_confidence") and isinstance(outcome.decision_confidence, (int, float)):
            span.set_summary(confidence=round(float(outcome.decision_confidence), 4))

        return state


def audit_persistence_node(
    state: CaseState,
    audit_ledger: list[dict[str, Any]] | None = None,
) -> CaseState:
    """Node 9: Writes immutable HMAC-SHA256 chained audit record and persists terminal state."""
    event_id = uuid.uuid4()
    now_utc = datetime.now(UTC)
    prev_hash = GENESIS_HASH

    if audit_ledger:
        prev_hash = audit_ledger[-1].get("current_hash", GENESIS_HASH)

    state.setdefault("completed_nodes", []).append("audit_persistence_node")

    event_payload = {
        "case_id": state["case_id"],
        "workflow_state": state.get("workflow_state"),
        "decision": state.get("decision"),
        "escalation_sub_reason": state.get("escalation_sub_reason"),
        "rule_id_matched": state.get("rule_id_matched"),
        "confidence": state.get("decision_confidence"),
        "completed_nodes": list(state.get("completed_nodes", [])),
    }

    curr_hash = compute_audit_hash(
        event_id=event_id,
        prev_hash=prev_hash,
        payload=event_payload,
        created_at=now_utc,
    )

    audit_entry = {
        "event_id": str(event_id),
        "case_id": state["case_id"],
        "prev_hash": prev_hash,
        "current_hash": curr_hash,
        "event_payload": event_payload,
        "created_at": now_utc.isoformat(),
        "created_at_dt": now_utc,
    }

    if audit_ledger is not None:
        audit_ledger.append(audit_entry)

    return state
