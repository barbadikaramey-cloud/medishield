

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
from medishield.core.config import settings
from medishield.core.tracing import scrub_phi_text, trace_span
from medishield.db.audit import GENESIS_HASH, compute_audit_hash
from medishield.db.enums import (
    AgentVerdict,
    BusinessDecision,
    CodingSystem,
    DocumentType,
    EscalationSubReason,
    WorkflowState,
)
from medishield.domain.clinical_align import (
    DeterministicClinicalAlignmentEngine,
    DischargeSummaryAnalysis,
)
from medishield.domain.clinical_determination import (
    ClinicalDeterminationEngine,
    ClinicalDeterminationLine,
    ValidatedDetermination,
)
from medishield.domain.clinical_interpreter import ClinicalRelationshipInterpreter
from medishield.domain.contracts import (
    BedDayBillingRuleContract,
    BedDayReconciliation,
    CandidatePolicy,
    ClaimsAgentOutput,
    ClinicalAlignmentOutput,
    ClinicalIndicationStatus,
    ConsistencyCheckReport,
    FacilityBillingConvention,
    FraudAgentOutput,
    KYCAgentOutput,
    LineClinicalEvidence,
    MedicalNecessityStatus,
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
from medishield.domain.faithfulness import (
    evaluate_case_consistency,
    parse_medical_date,
    verify_clinical_evidence_reference,
)
from medishield.domain.fraud import DeterministicFraudEngine, FraudEvaluationContext
from medishield.domain.provenance import (
    ClinicalAssertionStatus,
    EvidenceSource,
    EvidenceVerificationStatus,
    FieldProvenance,
    ProvenanceType,
)
from medishield.domain.state_machine import resolve_decision_workflow_state
from medishield.intake.completeness import (
    ClassifiedDocumentItem,
    evaluate_case_completeness,
)
from medishield.intake.storage import StorageBackend
from medishield.ocr.preprocessor import OCRPreprocessorEngine, retrieve_ocr_payload
from medishield.workflows.state import CaseState


def _claim_value_and_verified_span(
    raw_value: Any,
    claim_ocr_payloads: dict[str, Any] | None = None,
) -> tuple[str, FieldProvenance | None]:
    if isinstance(raw_value, dict):
        value = raw_value.get("value", "")
        raw_provenance = raw_value.get("provenance")
    else:
        value = getattr(raw_value, "value", raw_value)
        raw_provenance = getattr(raw_value, "provenance", None)
    try:
        provenance = (
            raw_provenance
            if isinstance(raw_provenance, FieldProvenance)
            else FieldProvenance.model_validate(raw_provenance)
            if raw_provenance
            else None
        )
    except ValueError:
        provenance = None
    if provenance is not None and provenance.evidence_source == EvidenceSource.OCR and provenance.verification_status == EvidenceVerificationStatus.VERIFIED:
        return str(value or "").strip(), provenance
    payload = (claim_ocr_payloads or {}).get(provenance.document_id) if provenance else None
    if provenance and payload:
        _, provenance = verify_clinical_evidence_reference(provenance, payload)
        if (
            provenance.evidence_source == EvidenceSource.OCR
            and provenance.verification_status == EvidenceVerificationStatus.VERIFIED
            and _clinical_code_matches_text(str(value or ""), provenance.text_content)
        ):
            return str(value or "").strip(), provenance
    provenance = None
    return str(value or "").strip(), provenance


def _clinical_code_matches_text(code: str, text: str) -> bool:
    normalize = lambda value: re.sub(r"[^A-Z0-9]", "", value.upper())
    normalized_code = normalize(code)
    normalized_text = normalize(text)
    if not normalized_code:
        return False
    variants = {normalized_code}
    if normalized_code[0] == "I":
        variants.add("1" + normalized_code[1:])
    return any(variant in normalized_text for variant in variants)


def _clinical_relationship_candidates(
    service_lines: list[dict[str, Any]],
    diagnosis_codes: list[str],
    claims_summary: dict[str, Any],
    clinical_spans: list[FieldProvenance],
    guideline_repo: Any,
    claim_ocr_payloads: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    code_details = getattr(guideline_repo, "get_active_code_details", None)
    if not code_details:
        return [], {}
    code_cache: dict[tuple[str, CodingSystem, date | None], dict[str, str] | None] = {}

    def get_details(code: str, coding_system: CodingSystem, service_date: date | None) -> dict[str, str] | None:
        key = (code.upper(), coding_system, service_date)
        if key not in code_cache:
            code_cache[key] = code_details(code, coding_system, service_date)
        return code_cache[key]

    raw_diagnoses: dict[str, list[FieldProvenance]] = {}
    for item in claims_summary.get("icd10_codes") or []:
        code, provenance = _claim_value_and_verified_span(item, claim_ocr_payloads)
        if code and provenance:
            raw_diagnoses.setdefault(code.upper(), []).append(provenance)

    candidates: list[dict[str, Any]] = []
    contexts: dict[int, dict[str, Any]] = {}
    for index, line in enumerate(service_lines, start=1):
        hcpcs, hcpcs_span = _claim_value_and_verified_span(line.get("hcpcs"), claim_ocr_payloads)
        revenue_code, revenue_span = _claim_value_and_verified_span(line.get("rev_code"), claim_ocr_payloads)
        line_number = int(line.get("line_number", index))
        service_date_raw, _ = _claim_value_and_verified_span(line.get("service_date"), claim_ocr_payloads)
        service_date = parse_medical_date(service_date_raw) if service_date_raw else None
        details = None
        service_code = ""
        service_span = None
        service_systems = (CodingSystem.CPT, CodingSystem.HCPCS)
        if hcpcs and hcpcs_span:
            for coding_system in service_systems:
                details = get_details(hcpcs, coding_system, service_date)
                if details:
                    service_code = hcpcs
                    service_span = hcpcs_span
                    break
        if details is None and revenue_code and revenue_span:
            details = get_details(revenue_code, CodingSystem.REV_CODE, service_date)
            if details:
                service_code = revenue_code
                service_span = revenue_span
                service_systems = (CodingSystem.REV_CODE,)
        if details is None or service_span is None:
            continue

        diagnosis_candidates: list[dict[str, Any]] = []
        line_context: dict[str, Any] = {
            "service_code": service_code,
            "service_description": details["description"],
            "service_evidence_id": service_span.span_id,
            "diagnosis_evidence_by_code": {},
            "diagnosis_description_by_code": {},
            "clinical_evidence_by_code": {},
        }
        for diagnosis_code in diagnosis_codes:
            diagnosis_details = get_details(diagnosis_code, CodingSystem.ICD10_CM, service_date)
            if not diagnosis_details:
                continue
            clinical_evidence = [
                span for span in clinical_spans
                if span.evidence_source == EvidenceSource.OCR
                and span.verification_status == EvidenceVerificationStatus.VERIFIED
                and span.span_id
                and _clinical_code_matches_text(diagnosis_code, span.text_content)
            ]
            if not clinical_evidence:
                continue
            diagnosis_spans = raw_diagnoses.get(diagnosis_code.upper(), [])
            diagnosis_span = diagnosis_spans[0] if diagnosis_spans else clinical_evidence[0]
            diagnosis_candidates.append({
                "diagnosis_code": diagnosis_code,
                "diagnosis_description": diagnosis_details["description"],
                "diagnosis_evidence_ids": [diagnosis_span.span_id],
                "clinical_evidence_ids": [span.span_id for span in clinical_evidence[:5]],
                "clinical_evidence": [
                    {"id": span.span_id, "text": scrub_phi_text(span.text_content[:500])}
                    for span in clinical_evidence[:5]
                ],
            })
            line_context["diagnosis_evidence_by_code"][diagnosis_code] = diagnosis_span
            line_context["diagnosis_description_by_code"][diagnosis_code] = diagnosis_details["description"]
            line_context["clinical_evidence_by_code"][diagnosis_code] = clinical_evidence[:5]

        if not diagnosis_candidates:
            continue
        candidates.append({
            "line_number": line_number,
            "service_code": service_code,
            "service_coding_system": details["coding_system"],
            "service_description": details["description"],
            "service_evidence_ids": [service_span.span_id],
            "service_evidence": [{"id": service_span.span_id, "text": scrub_phi_text(service_span.text_content[:300])}],
            "diagnosis_candidates": diagnosis_candidates,
        })
        contexts[line_number] = line_context

    return candidates, contexts

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
            storage_backend=storage,
            documents=state.get("documents", []),
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
        elif result.tamper_detected or (getattr(result, "tamper_score", 0.0) > 0.0):
            kyc_reason = "TAMPER_DETECTED"
        elif bool(getattr(result, "id_expired", False)):
            kyc_reason = "ID_EXPIRED"
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
                # Exclude companion documents: a claim form and an itemized bill naturally share the same date and total charge (ADR-022)
                fn_a = str(doc_a.get("file_name", "")).lower()
                fn_b = str(doc_b.get("file_name", "")).lower()
                is_itemized_a = "itemized" in fn_a or "bill" in fn_a
                is_itemized_b = "itemized" in fn_b or "bill" in fn_b
                is_claim_a = ("claim" in fn_a or "form" in fn_a) and not is_itemized_a
                is_claim_b = ("claim" in fn_b or "form" in fn_b) and not is_itemized_b
                is_companion = (is_claim_a and is_itemized_b) or (is_itemized_a and is_claim_b)

                if dates_match and amounts_match and meta_a["amounts"] and not is_companion:
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
        state["policy_payer_product"] = None
        state["policy_jurisdiction"] = None
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
            state["policy_payer_product"] = matched.payer_product
            state["policy_jurisdiction"] = matched.jurisdiction
            state["policy_context_verdict"] = AgentVerdict.PASS.value
        else:
            state["policy_id"] = None
            state["policy_version"] = None
            state["policy_effective_date_valid"] = False
            state["policy_payer_product"] = None
            state["policy_jurisdiction"] = None
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
        state["policy_coverage_summary"]["payer_product"] = state.get("policy_payer_product")
        state["policy_coverage_summary"]["jurisdiction"] = state.get("policy_jurisdiction")
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
            svc_raw = claims_summary.get("service_date")
            svc_date = svc_raw.get("value") if isinstance(svc_raw, dict) else svc_raw
            amt_raw = claims_summary.get("claim_amount")
            amt = amt_raw.get("value") if isinstance(amt_raw, dict) else amt_raw
            npi_raw = claims_summary.get("provider_npi")
            npi = npi_raw.get("value") if isinstance(npi_raw, dict) else npi_raw
            mem_raw = kyc_summary.get("member_id")
            mem_id = mem_raw.get("value") if isinstance(mem_raw, dict) else mem_raw
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
        state["fraud_review_required"] = result.fraud_review_required
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


def _service_code_for_line(line: dict[str, Any]) -> str:
    hcpcs_val = str(line.get("hcpcs", {}).get("value") if isinstance(line.get("hcpcs"), dict) else getattr(line.get("hcpcs"), "value", "") or "").strip()
    svc_code_val = str(line.get("service_code") or "").strip()
    rev_val = str(line.get("rev_code", {}).get("value") if isinstance(line.get("rev_code"), dict) else getattr(line.get("rev_code"), "value", "") or "").strip()
    return hcpcs_val if hcpcs_val and hcpcs_val not in ("UNKNOWN", "None", "null") else (
        svc_code_val if svc_code_val and svc_code_val not in ("UNKNOWN", "None", "null") else (
            rev_val if rev_val and rev_val not in ("UNKNOWN", "None", "null") else ""
        )
    )


def _service_date_for_line(line: dict[str, Any]) -> date | None:
    svc_date_val = line.get("service_date", {}).get("value") if isinstance(line.get("service_date"), dict) else getattr(line.get("service_date"), "value", None)
    return parse_medical_date(svc_date_val) if svc_date_val else None


def _determination_line_for(line: dict[str, Any], idx: int) -> ClinicalDeterminationLine:
    desc = line.get("description")
    desc_val = str((desc.get("value") if isinstance(desc, dict) else getattr(desc, "value", "")) or "").strip()
    code = _service_code_for_line(line)
    svc_date = _service_date_for_line(line)
    return ClinicalDeterminationLine(
        line_number=int(line.get("line_number", idx + 1)),
        service_description=desc_val or code or "Uncatalogued service",
        codes=[code] if code else [],
        service_date=svc_date.isoformat() if svc_date else None,
    )


def clinical_alignment_node(
    state: CaseState,
    storage: StorageBackend | None = None,
    engine: type[DeterministicClinicalAlignmentEngine] | None = None,
    relationship_interpreter: ClinicalRelationshipInterpreter | None = None,
    determination_engine: ClinicalDeterminationEngine | None = None,
) -> CaseState:
    """Node 6C: Clinical Alignment Specialist (ADR-015, ADR-025, Spec 01 § 4, Spec 04 § 3, Spec 05 § 7B).

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
        clinical_ocr_payloads: dict[str, Any] = {}
        clinical_doc_types: dict[str, str] = {}
        clinical_doc_ids: list[str] = []
        disch_analyses: list[DischargeSummaryAnalysis] = []

        for d in clinical_docs:
            doc_id = str(d.get("document_id") or "UNKNOWN")
            clinical_doc_ids.append(doc_id)
            clinical_doc_types[doc_id] = str(d.get("doc_type", "")).upper()
            doc_text = ""
            extracted = False

            if storage:
                payload_key = d.get("ocr_payload_uri") or f"ocr/{case_id}/{doc_id}.json"
                if storage.exists(payload_key):
                    try:
                        payload = retrieve_ocr_payload(case_id, doc_id, storage)
                        clinical_ocr_payloads[doc_id] = payload
                        p_texts = []
                        for page in getattr(payload, "pages", []):
                            for s in getattr(page, "spans", []):
                                p_texts.append(s.text)
                                candidate = FieldProvenance(
                                    document_id=doc_id,
                                    page_number=page.page_number,
                                    span_id=s.span_id,
                                    text_content=s.text,
                                    bbox=s.bbox,
                                    bounding_box=s.bbox.to_list(),
                                    provenance_type=ProvenanceType.SPAN,
                                    evidence_quality_score=s.confidence,
                                    model_confidence=s.confidence,
                                    evidence_source=EvidenceSource.OCR,
                                )
                                _, verified = verify_clinical_evidence_reference(candidate, payload)
                                clinical_spans.append(verified)
                        if p_texts:
                            doc_text = "\n".join(p_texts)
                            clinical_texts.append(doc_text)
                            extracted = True
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Failed to retrieve OCR payload for clinical doc %s: %s", doc_id, exc)

                if not extracted:
                    obj_key = d.get("object_key")
                    if obj_key and storage.exists(obj_key):
                        try:
                            raw = storage.get_bytes(obj_key).decode("utf-8", errors="ignore")
                            doc_text = raw
                            clinical_texts.append(raw)
                            clinical_spans.append(
                                FieldProvenance(
                                    document_id=doc_id,
                                    page_number=1,
                                    text_content=raw[:300],
                                    evidence_quality_score=0.95,
                                    model_confidence=0.90,
                                    evidence_source=EvidenceSource.UNKNOWN,
                                )
                            )
                            extracted = True
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("Failed to read raw bytes for clinical doc %s: %s", doc_id, exc)

            if not extracted and d.get("text_content"):
                raw = str(d["text_content"])
                doc_text = raw
                clinical_texts.append(raw)
                clinical_spans.append(
                    FieldProvenance(
                        document_id=doc_id,
                        page_number=1,
                        text_content=raw[:300],
                        evidence_quality_score=0.95,
                        model_confidence=0.90,
                        evidence_source=EvidenceSource.UNKNOWN,
                    )
                )

            dtype = str(d.get("doc_type", "")).upper()
            fname = str(d.get("file_name", "")).lower()
            if (
                dtype in (DocumentType.DISCHARGE_SUMMARY.value, "DISCHARGE_SUMMARY")
                or "discharge" in fname
                or "summary" in fname
                or "discharge" in doc_text.lower()
            ) and doc_text:
                analysis = align_engine.parse_discharge_summary(doc_text)
                disch_analyses.append(analysis)

        combined_text = "\n".join(clinical_texts)
        primary_icd_codes = [c for da in disch_analyses for c in da.primary_icd_codes]
        secondary_icd_codes = [c for da in disch_analyses for c in da.secondary_icd_codes]
        all_disch_icd = [c for da in disch_analyses for c in da.all_icd_codes]
        hospital_course_texts = [da.hospital_course_text for da in disch_analyses if da.hospital_course_text]
        procedures_performed_texts = [da.procedures_performed_text for da in disch_analyses if da.procedures_performed_text]
        primary_diag_texts = [da.primary_diagnosis_text for da in disch_analyses if da.primary_diagnosis_text]
        secondary_diag_texts = [da.secondary_diagnosis_text for da in disch_analyses if da.secondary_diagnosis_text]
        admit_date = next((da.admission_date for da in disch_analyses if da.admission_date), None)
        discharge_date = next((da.discharge_date for da in disch_analyses if da.discharge_date), None)

        pol_cov = state.get("policy_coverage_summary") or {}
        pa_present = bool(
            pol_cov.get("prior_authorization_present")
            or pol_cov.get("prior_authorization_number")
        )

        # 5. Evaluate each claim service line against clinical documentation
        line_assessments: list[LineClinicalEvidence] = []
        room_and_board_lines = []

        raw_icd = claims_summary.get("icd10_codes") or []
        diagnosis_candidates = [
            str(c.get("value") if isinstance(c, dict) else getattr(c, "value", c))
            for c in raw_icd
            if c
        ] + all_disch_icd
        guideline_repo = align_engine._get_default_repo()
        claim_ocr_payloads: dict[str, Any] = {}
        if storage:
            for document in state.get("documents", []):
                doc_type = str(document.get("doc_type", "")).upper()
                file_name = str(document.get("file_name", "")).lower()
                if doc_type not in (DocumentType.CLAIM_FORM.value, "CLAIM_FORM") and "claim" not in file_name:
                    continue
                document_id = str(document.get("document_id") or "UNKNOWN")
                payload_key = document.get("ocr_payload_uri") or f"ocr/{case_id}/{document_id}.json"
                if storage.exists(payload_key):
                    try:
                        claim_ocr_payloads[document_id] = retrieve_ocr_payload(case_id, document_id, storage)
                    except (ValueError, TypeError, KeyError):
                        logger.warning("Unable to validate claim-code provenance for %s", document_id)
        relationship_candidates, relationship_contexts = _clinical_relationship_candidates(
            raw_service_lines,
            list(dict.fromkeys(diagnosis_candidates)),
            claims_summary,
            clinical_spans,
            guideline_repo,
            claim_ocr_payloads,
        )
        relationship_proposals = (
            (relationship_interpreter or ClinicalRelationshipInterpreter()).assess(relationship_candidates)
            if relationship_candidates
            else {}
        )

        determination_lines = [
            _determination_line_for(l, idx) for idx, l in enumerate(raw_service_lines)
        ]
        determinations: dict[int, ValidatedDetermination] = (
            (determination_engine or ClinicalDeterminationEngine()).determine(determination_lines, clinical_spans)
            if determination_lines
            else {}
        )
        for idx, l in enumerate(raw_service_lines):
            line_no = int(l.get("line_number", idx + 1))
            code = _service_code_for_line(l)
            desc_val = str(l.get("description", {}).get("value") if isinstance(l.get("description"), dict) else getattr(l.get("description"), "value", "") or "").strip()

            amt = float(l.get("amount", {}).get("value") if isinstance(l.get("amount"), dict) else getattr(l.get("amount"), "value", l.get("billed_amount", 0.0)) or 0.0)
            units = float(l.get("units", {}).get("value") if isinstance(l.get("units"), dict) else getattr(l.get("units"), "value", l.get("billed_units", 1.0)) or 1.0)
            svc_date = _service_date_for_line(l)

            modality = align_engine.classify_modality(code)
            # If code was unclassified or empty, check description for institutional services
            if modality == ServiceModality.OTHER_SERVICE and desc_val:
                desc_lower = desc_val.lower()
                if any(w in desc_lower for w in ("room and board", "semi-private", "private room", "icu", "ccu", "bed charges", "ward", "accommodation")):
                    modality = ServiceModality.ROOM_AND_BOARD
                elif any(w in desc_lower for w in ("pharmacy", "medicine", "medication", "drugs", "iv fluid", "oral dose")):
                    modality = ServiceModality.PHARMACEUTICAL
                elif any(w in desc_lower for w in ("diagnostics", "diagnostic", "investigation", "pathology", "lab")):
                    modality = ServiceModality.LABORATORY_TEST

            if modality == ServiceModality.ROOM_AND_BOARD:
                room_and_board_lines.append((line_no, code or "0120", units, amt))

            procedure_text = "\n".join(procedures_performed_texts).casefold()
            indication_rule = guideline_repo.get_indication(code) if code else None
            assertion_patterns = (indication_rule or {}).get("assertion_patterns") or {}
            performance_spans = [
                item for item in clinical_spans
                if item.verification_status == EvidenceVerificationStatus.VERIFIED
                and item.evidence_source == EvidenceSource.OCR
                and code
                and code.casefold() in item.text_content.casefold()
                and code.casefold() in procedure_text
            ]
            negated_performance = any(
                re.search(pattern, " ".join(item.text_content for item in performance_spans), re.IGNORECASE)
                for pattern in assertion_patterns.get("negated_performance_patterns", [])
            )
            uncertain_performance = any(
                re.search(pattern, " ".join(item.text_content for item in performance_spans), re.IGNORECASE)
                for pattern in assertion_patterns.get("uncertain_performance_patterns", [])
            )
            affirmed_performance = bool(performance_spans) and any(
                re.search(pattern, " ".join(item.text_content for item in performance_spans), re.IGNORECASE)
                for pattern in assertion_patterns.get("affirmed_performance_patterns", [])
            )
            if negated_performance:
                perf_status = PerformanceEvidenceStatus.EXPLICITLY_CONTRADICTED
            elif uncertain_performance or not affirmed_performance:
                perf_status = PerformanceEvidenceStatus.NOT_DOCUMENTED
            else:
                perf_status = PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE
            if perf_status == PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE:
                performance_spans = [
                    item.model_copy(update={
                        "normalized_interpretation": code,
                        "assertion_status": ClinicalAssertionStatus.AFFIRMED,
                        "evidence_relevance_verified": True,
                    })
                    for item in performance_spans
                ]

            diagnosis_evidence: list[FieldProvenance] = []
            affirmed_diagnosis_codes: list[str] = []
            indication_conflict = False
            line_context = relationship_contexts.get(line_no) or {}
            documented_diagnosis_codes = list(line_context.get("diagnosis_description_by_code", {}))
            diagnosis_context = "; ".join(
                f"{line_context['diagnosis_description_by_code'][diagnosis_code]} ({diagnosis_code})"
                for diagnosis_code in documented_diagnosis_codes
            ) or "no verified diagnosis"
            procedure_context = f"{line_context.get('service_description', 'Uncatalogued service')} ({code or 'UNKNOWN'})"
            indication_override = ClinicalIndicationStatus.POTENTIALLY_RELATED
            indication_note = (
                f"Diagnosis vs procedure: {diagnosis_context} vs {procedure_context}. "
                "The relationship could not be assessed from verified record evidence."
            )
            pair_proposals = []
            for diagnosis_code, clinical_spans_for_code in line_context.get("clinical_evidence_by_code", {}).items():
                pair = relationship_proposals.get((line_no, code, diagnosis_code))
                if pair is None:
                    continue
                cited = next((span for span in clinical_spans_for_code if span.span_id == pair.clinical_evidence_id), None)
                if cited is not None:
                    pair_proposals.append((pair, cited))

            supported_pairs = [(pair, cited) for pair, cited in pair_proposals if pair.relationship == "SUPPORTED"]
            expected_pair_count = len(line_context.get("clinical_evidence_by_code", {}))
            if supported_pairs:
                indication_override = ClinicalIndicationStatus.EXPLICITLY_INDICATED
                supported_codes = [pair.diagnosis_code for pair, _ in supported_pairs]
                affirmed_diagnosis_codes.extend(supported_codes)
                for pair, cited in supported_pairs:
                    diagnosis_evidence.append(cited.model_copy(update={
                        "normalized_interpretation": pair.diagnosis_code,
                        "assertion_status": ClinicalAssertionStatus.AFFIRMED,
                        "evidence_relevance_verified": True,
                    }))
                indication_note = None
            elif expected_pair_count and len(pair_proposals) == expected_pair_count and all(
                pair.relationship == "NOT_ESTABLISHED" for pair, _ in pair_proposals
            ):
                indication_override = ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION
                diagnosis_descriptions = "; ".join(
                    f"{line_context['diagnosis_description_by_code'].get(pair.diagnosis_code, pair.diagnosis_code)} "
                    f"({pair.diagnosis_code})"
                    for pair, _ in pair_proposals
                )
                indication_note = (
                    f"Diagnosis vs procedure: {diagnosis_descriptions} vs {procedure_context}. "
                    "The cited record did not establish a clinical relationship."
                )

            determination = determinations.get(line_no)
            necessity_status = (
                MedicalNecessityStatus.MET
                if determination is not None and determination.outcome == "SUPPORTED"
                else MedicalNecessityStatus.CRITERIA_INCOMPLETE
            )
            necessity_spans = (
                [span for span in clinical_spans if span.span_id in determination.evidence_ids]
                if determination is not None and determination.outcome == "SUPPORTED"
                else []
            )

            pa_req = None
            pa_ver = None
            template = guideline_repo.get_guideline(code.replace("REV_", ""), service_date=svc_date)
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
                payer_product=pol_cov.get("payer_product"),
                jurisdiction=pol_cov.get("jurisdiction"),
                performance_status=perf_status,
                indication_status=indication_override,
                discrepancy_description=indication_note,
                prior_auth_requirement=pa_req,
                prior_auth_verification=pa_ver,
                supporting_spans=performance_spans + diagnosis_evidence + necessity_spans,
                performance_evidence=performance_spans,
                indication_evidence=diagnosis_evidence,
                necessity_status=necessity_status,
                indication_conflict=indication_conflict,
                clinical_notes_text=combined_text,
                diagnosis_codes=affirmed_diagnosis_codes,
                primary_icd10_codes=[c for c in primary_icd_codes if c in documented_diagnosis_codes],
                secondary_icd10_codes=[c for c in secondary_icd_codes if c in documented_diagnosis_codes],
                primary_diagnosis_text=" ".join(primary_diag_texts),
                secondary_diagnosis_text=" ".join(secondary_diag_texts),
                hospital_course_text=" ".join(hospital_course_texts),
            )
            if indication_override == ClinicalIndicationStatus.EXPLICITLY_INDICATED:
                supported_diagnoses = "; ".join(
                    f"{line_context['diagnosis_description_by_code'].get(diagnosis_code, diagnosis_code)} "
                    f"({diagnosis_code})"
                    for diagnosis_code in affirmed_diagnosis_codes
                )
                finding = (
                    f"Diagnosis vs procedure: {supported_diagnoses} vs {procedure_context}. "
                    "The cited record supports the relationship."
                )
                if line_ev.line_verdict != AgentVerdict.PASS:
                    additional_review = []
                    if line_ev.performance_status in (
                        PerformanceEvidenceStatus.NOT_DOCUMENTED,
                        PerformanceEvidenceStatus.ORDERED_ONLY,
                    ):
                        additional_review.append("documentation that the service was performed")
                    if line_ev.necessity_status == MedicalNecessityStatus.GUIDELINE_UNAVAILABLE:
                        additional_review.append("an applicable clinical guideline")
                    elif line_ev.necessity_status == MedicalNecessityStatus.CRITERIA_INCOMPLETE:
                        additional_review.append("required clinical guideline evidence")
                    if line_ev.prior_auth_verification in (
                        PriorAuthVerificationStatus.MISSING_OR_UNVERIFIED,
                        PriorAuthVerificationStatus.AUTH_SERVICE_UNAVAILABLE,
                    ):
                        additional_review.append("verified prior authorization")
                    if additional_review:
                        finding += f" Additional review needed for {', '.join(additional_review)}."
            else:
                finding = indication_note
            if finding:
                line_ev = line_ev.model_copy(update={"clinical_finding_summary": finding})
            line_assessments.append(line_ev)

        # 6. Reconcile bed days if room and board lines present
        bed_day_rec: BedDayReconciliation | None = None
        if room_and_board_lines:
            total_bed_days = sum(units for _, _, units, _ in room_and_board_lines)
            rule = (
                BedDayBillingRuleContract(
                    rule_id="BR-ACUTE-INP-01",
                    rule_version="2026.1",
                    facility_type="INPATIENT_ACUTE",
                    claim_type="INPATIENT",
                    effective_date=date(2025, 1, 1),
                    is_approved=True,
                    convention=FacilityBillingConvention.CALENDAR_DAY_SPAN,
                )
                if (admit_date and discharge_date)
                else None
            )
            bed_day_rec = align_engine.reconcile_bed_days(
                billed_bed_days=total_bed_days,
                admission_date=admit_date,
                discharge_date=discharge_date,
                billing_rule=rule,
                claim_facility_type="INPATIENT_ACUTE" if rule else None,
                claim_type="INPATIENT" if rule else None,
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
            code_catalog=state.get("code_catalog"),
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
            clinical_support_approval_min_ratio=settings.CLINICAL_SUPPORT_APPROVAL_MIN_RATIO,
            clinical_uncertainty_approval_enabled=settings.CLINICAL_UNCERTAINTY_APPROVAL_ENABLED,
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
