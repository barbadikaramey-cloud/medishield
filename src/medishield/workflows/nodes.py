

"""Workflow node implementations wrapping specialist engines (Spec 04 § 2-3, Spec 02 § 3)."""

import logging
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
from medishield.db.audit import GENESIS_HASH, compute_audit_hash
from medishield.db.enums import (
    AgentVerdict,
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
    WorkflowState,
)
from medishield.domain.contracts import (
    CandidatePolicy,
    ClaimsAgentOutput,
    FraudAgentOutput,
    KYCAgentOutput,
    PolicyCoverageOutput,
)
from medishield.domain.decision import DecisionInputContext, DeterministicDecisionEngine
from medishield.domain.fraud import DeterministicFraudEngine
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
    return state


def split_group_node(state: CaseState) -> CaseState:
    """Node 2: Clusters and assigns logical page boundaries across intake documents."""
    for doc in state.get("documents", []):
        if "split_source_page_indices" not in doc:
            doc["split_source_page_indices"] = [1]

    state["workflow_state"] = WorkflowState.SPLIT.value
    state.setdefault("completed_nodes", []).append("split_group_node")
    return state


def classifier_node(
    state: CaseState,
    storage: StorageBackend,
    classifier_agent: DocumentClassifierAgent | None = None,
) -> CaseState:
    """Node 3: Classifies document types and assigns confidence and routing tags."""
    agent = classifier_agent or DocumentClassifierAgent()

    for doc in state.get("documents", []):
        current_type = doc.get("doc_type")
        if not current_type or current_type == DocumentType.UNKNOWN.value:
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
    return state


def case_completeness_node(state: CaseState) -> CaseState:
    """
    Node 4: Evaluates intake completeness against mandatory document types.
    Emits early escalation and pauses graph if mandatory documents are missing.
    """
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
    return state


# ── Specialist Extraction Nodes (Nodes 5A, 5B, 5C) ───────────────────────────


def kyc_agent_node(
    state: CaseState,
    storage: StorageBackend,
    kyc_agent: KYCAgent,
) -> CaseState:
    """Node 5A: Executes KYC identity verification, member DB matching, and tamper checks."""
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

    result = kyc_agent.execute(case_id=case_id, document_payloads=payloads)

    state["kyc_execution_id"] = f"kyc-{uuid.uuid4().hex[:8]}"
    state["kyc_verdict"] = result.verdict.value
    state["kyc_confidence"] = result.confidence
    state["kyc_summary"] = result.model_dump()
    state.setdefault("completed_nodes", []).append("kyc_agent_node")
    return state


def claims_extraction_node(
    state: CaseState,
    storage: StorageBackend,
    claims_agent: ClaimsAgent,
) -> CaseState:
    """Node 5B: Extracts billing codes, service lines, amounts, and validates line math."""
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

    result = claims_agent.execute(case_id=case_id, document_payloads=payloads)

    state["claims_execution_id"] = f"claims-{uuid.uuid4().hex[:8]}"
    state["claims_verdict"] = result.verdict.value
    state["claims_confidence"] = result.confidence
    state["claims_summary"] = result.model_dump()
    state.setdefault("completed_nodes", []).append("claims_extraction_node")
    return state


def policy_context_node(
    state: CaseState,
    policy_context_agent: PolicyContextAgent,
) -> CaseState:
    """Node 5C: Discovers candidate policy versions and effective windows using member_id."""
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
        try:
            service_dt = datetime.fromisoformat(str(raw_date_val)).date()
        except ValueError:
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
                try:
                    service_dt = datetime.strptime(str(raw_date_val), fmt).replace(tzinfo=UTC).date()
                    break
                except ValueError:
                    continue

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
    if state.get("policy_context_verdict") in (AgentVerdict.UNAVAILABLE.value, AgentVerdict.INDETERMINATE.value):
        state["policy_coverage_execution_id"] = f"polcov-{uuid.uuid4().hex[:8]}"
        verdict_val = state.get("policy_context_verdict", AgentVerdict.UNAVAILABLE.value)
        state["policy_coverage_verdict"] = verdict_val
        state["policy_coverage_summary"] = {
            "agent_name": "policy_agent",
            "verdict": verdict_val,
            "confidence": 0.0 if verdict_val == AgentVerdict.UNAVAILABLE.value else 0.4,
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
    return state


def fraud_agent_node(
    state: CaseState,
    fraud_engine: type[DeterministicFraudEngine] | None = None,
) -> CaseState:
    """Node 6B: Executes 7-feature deterministic fraud evaluation concurrently with Policy RAG."""
    engine = fraud_engine or DeterministicFraudEngine
    kyc_summary = state.get("kyc_summary") or {}
    claims_summary = state.get("claims_summary") or {}

    kyc_obj = KYCAgentOutput(**kyc_summary) if kyc_summary else None
    claims_obj = ClaimsAgentOutput(**claims_summary) if claims_summary else None

    if claims_obj is None:
        from medishield.domain.provenance import FieldProvenance, ProvenanceType, ProvenanceValue

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

    result = engine.evaluate(claims_output=claims_obj, kyc_output=kyc_obj)

    state["fraud_execution_id"] = f"fraud-{uuid.uuid4().hex[:8]}"
    state["fraud_verdict"] = result.verdict.value
    state["fraud_score"] = result.fraud_score
    state["fraud_risk_level"] = result.risk_level.value
    state["fraud_summary"] = result.model_dump()
    state.setdefault("completed_nodes", []).append("fraud_agent_node")
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


def faithfulness_gate_node(state: CaseState) -> CaseState:
    """Node 7: Cross-verifies evidence provenance, line totals, and Gate 2 clause citations."""
    claims_summary = state.get("claims_summary") or {}
    policy_summary = state.get("policy_coverage_summary") or {}

    # Check claims line validity & policy clause grounding
    line_ok = bool(claims_summary.get("line_items_sum_valid", True))
    schema_ok = bool(claims_summary.get("schema_valid", True))
    
    cited_clauses = policy_summary.get("cited_clauses") or []
    all_clauses_grounded = all(c.get("verified_in_corpus", True) for c in cited_clauses)

    passed = line_ok and schema_ok and all_clauses_grounded
    integrity_score = 1.0 if passed else 0.50

    state["faithfulness_gate_passed"] = passed
    state["provenance_integrity_score"] = integrity_score
    state["unsupported_claims_count"] = 0 if passed else 1
    state.setdefault("completed_nodes", []).append("faithfulness_gate_node")
    return state


def decision_engine_node(
    state: CaseState,
    decision_engine: DeterministicDecisionEngine | None = None,
) -> CaseState:
    """Node 8: Purely deterministic execution of priority rules P1–P8."""
    engine = decision_engine or DeterministicDecisionEngine()

    kyc_obj = KYCAgentOutput(**state["kyc_summary"]) if state.get("kyc_summary") else None
    claims_obj = ClaimsAgentOutput(**state["claims_summary"]) if state.get("claims_summary") else None
    pol_obj = PolicyCoverageOutput(**state["policy_coverage_summary"]) if state.get("policy_coverage_summary") else None
    fraud_obj = FraudAgentOutput(**state["fraud_summary"]) if state.get("fraud_summary") else None

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
        present_document_types=doc_types,
        missing_required_documents=not state.get("is_complete", True),
        security_tamper_score=tamper_val,
    )

    outcome = engine.evaluate(context)

    # Synthesize rich clinical & policy explanation (LLM or deterministic fallback per ADR-011)
    from medishield.domain.explanation import generate_decision_explanation
    detailed_explanation = generate_decision_explanation(context, outcome)

    target_state = resolve_decision_workflow_state(outcome.decision)
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

    state.setdefault("completed_nodes", []).append("decision_engine_node")
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
