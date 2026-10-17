import logging
from typing import Any

logger = logging.getLogger(__name__)

from medishield.agents.claims import ClaimsAgent
from medishield.agents.classifier import DocumentClassifierAgent
from medishield.agents.kyc import KYCAgent
from medishield.agents.policy import (
    PolicyContextAgent,
    PolicyCorpusRepository,
    PolicyCoverageAgent,
)
from medishield.core.tracing import trace_span
from medishield.db.enums import BusinessDecision, WorkflowState
from medishield.domain.clinical_align import DeterministicClinicalAlignmentEngine
from medishield.domain.decision import DeterministicDecisionEngine
from medishield.domain.fraud import DeterministicFraudEngine
from medishield.intake.storage import InMemoryStorageBackend, StorageBackend
from medishield.ocr.preprocessor import OCRPreprocessorEngine
from medishield.workflows.nodes import (
    audit_persistence_node,
    case_completeness_node,
    claims_extraction_node,
    classifier_node,
    clinical_alignment_node,
    decision_engine_node,
    faithfulness_gate_node,
    fraud_agent_node,
    join_all_barrier,
    join_claim_ready_barrier,
    kyc_agent_node,
    ocr_preprocessor_node,
    policy_context_node,
    policy_coverage_rag_node,
    split_group_node,
)
from medishield.workflows.state import CaseState


class CaseWorkflowCoordinator:
    """
    Authoritative Multi-Agent Workflow Coordinator (Spec 04 § 2, ADR-003, ADR-005).
    Coordinates intake processing, parallel specialist branches, synchronization barriers,
    faithfulness gate checks, and deterministic decision evaluation.
    """

    def __init__(
        self,
        storage: StorageBackend | None = None,
        ocr_engine: OCRPreprocessorEngine | None = None,
        classifier_agent: DocumentClassifierAgent | None = None,
        kyc_agent: KYCAgent | None = None,
        claims_agent: ClaimsAgent | None = None,
        policy_context_agent: PolicyContextAgent | None = None,
        policy_coverage_agent: PolicyCoverageAgent | None = None,
        fraud_engine: type[DeterministicFraudEngine] | None = None,
        clinical_alignment_engine: type[DeterministicClinicalAlignmentEngine] | None = None,
        decision_engine: DeterministicDecisionEngine | None = None,
        policy_corpus: PolicyCorpusRepository | None = None,
        audit_ledger: list[dict[str, Any]] | None = None,
        db_session: Any | None = None,
    ) -> None:
        self.storage = storage or InMemoryStorageBackend()
        self.ocr_engine = ocr_engine or OCRPreprocessorEngine()
        self.classifier_agent = classifier_agent or DocumentClassifierAgent()
        if kyc_agent is not None:
            self.kyc_agent = kyc_agent
        elif db_session is not None:
            from medishield.agents.kyc import SqlMemberRepository
            self.kyc_agent = KYCAgent(member_repo=SqlMemberRepository(db_session))
        else:
            self.kyc_agent = KYCAgent()
        self.claims_agent = claims_agent or ClaimsAgent(storage_backend=self.storage)
        if policy_context_agent is not None:
            self.policy_context_agent = policy_context_agent
        elif db_session is not None:
            from medishield.agents.policy import SqlPolicyEnrollmentRepository
            self.policy_context_agent = PolicyContextAgent(
                enrollment_repo=SqlPolicyEnrollmentRepository(db_session)
            )
        else:
            raise RuntimeError(
                "CaseWorkflowCoordinator requires an active db_session or explicit policy_context_agent injection. "
                "InMemoryPolicyEnrollmentRepository is restricted to unit tests."
            )
        self.policy_corpus = policy_corpus or PolicyCorpusRepository()
        self.policy_coverage_agent = (
            policy_coverage_agent or PolicyCoverageAgent(corpus_repo=self.policy_corpus)
        )
        self.fraud_engine = fraud_engine or DeterministicFraudEngine
        self.clinical_alignment_engine = (
            clinical_alignment_engine or DeterministicClinicalAlignmentEngine
        )
        self.decision_engine = decision_engine or DeterministicDecisionEngine()
        self.audit_ledger = audit_ledger if audit_ledger is not None else []

    def execute(self, state: CaseState) -> CaseState:
        """
        Executes the end-to-end directed workflow graph.
        Respects synchronization barriers and early-halt branches.
        """
        case_id = state.get("case_id", "UNKNOWN")
        doc_count = len(state.get("documents", []))

        with trace_span(
            name="case_workflow_execute",
            run_type="chain",
            case_id=case_id,
            component="CaseWorkflowCoordinator",
            operation="execute",
            inputs={"document_count": doc_count},
        ) as span:
            # ── Phase 1: Intake Sequential Pipeline (Nodes 1 - 4) ─────────────────
            state = ocr_preprocessor_node(state, self.storage, self.ocr_engine)
            state = split_group_node(state)
            state = classifier_node(state, self.storage, self.classifier_agent)
            state = case_completeness_node(state)

            logger.info(
                "[%s] Phase 1 Intake: %d documents classified: %s",
                case_id,
                len(state.get("documents", [])),
                [(d.get("file_name"), d.get("doc_type")) for d in state.get("documents", [])],
            )

            # Early exit check: Missing mandatory documents -> pause for upload
            if not state.get("is_complete", False):
                logger.info(
                    "[%s] Phase 1 Early Halt: Incomplete case, missing %s",
                    case_id,
                    state.get("missing_required_documents", []),
                )
                span.set_summary(
                    status="SUCCESS",
                    document_count=len(state.get("documents", [])),
                    decision="ESCALATE",
                    rule_id_matched="RULE-P2-INCOMPLETE",
                    escalation_sub_reason="MISSING_INFORMATION",
                    approval_safety_passed=False,
                    decision_summary="Incomplete case: Required intake documentation missing (awaiting supplementary upload).",
                    decisive_factors="intake",
                    blocking_condition_count=1,
                )
                state = audit_persistence_node(state, self.audit_ledger)
                return state

            # ── Phase 2: Parallel Specialist Extractions (Nodes 5A, 5B, 5C) ───────
            state = kyc_agent_node(state, self.storage, self.kyc_agent)
            state = claims_extraction_node(state, self.storage, self.claims_agent)
            state = policy_context_node(state, self.policy_context_agent)

            kyc_s = state.get("kyc_summary") or {}
            cl_s = state.get("claims_summary") or {}

            def _val(x: Any) -> Any:
                return x.get("value") if isinstance(x, dict) else x

            logger.info(
                "[%s] Phase 2 [KYC Specialist]: Verdict=%s (conf=%.2f) | MemberID=%s | Name=%s %s | DBMatched=%s | Flags=%s",
                case_id,
                state.get("kyc_verdict"),
                state.get("kyc_confidence", 0.0),
                _val(kyc_s.get("member_id")),
                _val(kyc_s.get("first_name")),
                _val(kyc_s.get("last_name")),
                kyc_s.get("member_db_matched"),
                kyc_s.get("flags", []),
            )

            logger.info(
                "[%s] Phase 2 [Claims Specialist]: Verdict=%s (conf=%.2f) | Amount=%s | ServiceDate=%s | NPI=%s | ICD10=%s | CPT=%s | ValidationErrors=%s",
                case_id,
                state.get("claims_verdict"),
                state.get("claims_confidence", 0.0),
                _val(cl_s.get("claim_amount")),
                _val(cl_s.get("service_date")),
                _val(cl_s.get("provider_npi")),
                [_val(c) for c in cl_s.get("icd10_codes", [])],
                [_val(c) for c in cl_s.get("cpt_codes", [])],
                cl_s.get("validation_errors", []),
            )

            # ── Phase 3: JoinClaimReady Barrier & Coverage Evaluation ─────────────
            state = join_claim_ready_barrier(state)
            state = policy_coverage_rag_node(state, self.policy_coverage_agent)

            pol_s = state.get("policy_coverage_summary") or {}
            logger.info(
                "[%s] Phase 3 [Policy Specialist]: Verdict=%s | Reason='%s' | ServiceDateValid=%s",
                case_id,
                state.get("policy_coverage_verdict"),
                pol_s.get("reasoning_summary", "N/A"),
                state.get("policy_effective_date_valid"),
            )

            # ── Phase 4: Independent Concurrent Fraud & Clinical Alignment Specialist Evaluation ───
            state = fraud_agent_node(state, self.fraud_engine, storage=self.storage)
            state = clinical_alignment_node(state, self.storage, self.clinical_alignment_engine)

            logger.info(
                "[%s] Phase 4 [Fraud Specialist]: Verdict=%s | FraudScore=%.2f | Risk=%s | Anomalies=%s",
                case_id,
                state.get("fraud_verdict", "PASS"),
                state.get("fraud_score", 0.0),
                state.get("fraud_risk_level", "LOW"),
                state.get("fraud_anomalies", []),
            )

            clin_s = state.get("clinical_alignment_summary") or {}
            logger.info(
                "[%s] Phase 4 [Clinical Specialist]: Verdict=%s (conf=%s) | Lines=%d | Supported=%d | Sufficient=%s",
                case_id,
                state.get("clinical_alignment_verdict"),
                state.get("clinical_alignment_confidence"),
                clin_s.get("total_lines_assessed", 0),
                clin_s.get("lines_fully_supported_count", 0),
                clin_s.get("assessment_sufficient"),
            )

            # ── Phase 5: JoinAll Barrier & Downstream Gate ─────────────────────────
            state = join_all_barrier(state)
            state = faithfulness_gate_node(state, self.storage)

            # ── Phase 6: Deterministic Decision Engine (Priority Rules P1 - P8) ───
            state = decision_engine_node(state, self.decision_engine)

            logger.info(
                "[%s] Phase 6 Decision Engine: %s (sub_reason=%s, rule=%s, conf=%.2f) -> %s",
                case_id,
                state.get("decision"),
                state.get("escalation_sub_reason"),
                state.get("rule_id_matched"),
                state.get("decision_confidence", 0.0),
                state.get("justification"),
            )

            # ── Phase 7: Tamper-Evident Audit & State Persistence ─────────────────
            state = audit_persistence_node(state, self.audit_ledger)

            raw_decision = state.get("decision")
            decision_str = (
                raw_decision.value
                if hasattr(raw_decision, "value")
                else str(raw_decision)
                if raw_decision
                else None
            )
            rule_id = state.get("rule_id_matched")
            sub_reason = state.get("escalation_sub_reason")
            sub_reason_str = (
                sub_reason.value
                if hasattr(sub_reason, "value")
                else str(sub_reason)
                if sub_reason
                else None
            )
            dec_summary = state.get("decision_summary")
            decisive_factors = state.get("decisive_factors")
            approval_passed = (decision_str == "APPROVE")

            span.set_summary(
                status="SUCCESS",
                document_count=len(state.get("documents", [])),
                decision=decision_str if decision_str in ("APPROVE", "REJECT", "ESCALATE") else None,
                rule_id_matched=str(rule_id) if rule_id else None,
                escalation_sub_reason=sub_reason_str,
                approval_safety_passed=approval_passed,
                decision_summary=dec_summary,
                decisive_factors=decisive_factors,
            )

            return state

    def resume_with_human_override(
        self,
        state: CaseState,
        override_decision: BusinessDecision,
        reviewer_id: str,
        reason: str,
    ) -> CaseState:
        """
        Resumes an escalated case from AWAITING_HUMAN with an authoritative operator decision (ADR-005).
        """
        state["decision"] = override_decision.value
        state["workflow_state"] = WorkflowState.COMPLETED.value
        state["justification"] = f"Manual reviewer override by {reviewer_id}: {reason}"
        state["is_auto_decided"] = False
        state["decision_confidence"] = 1.0

        state = audit_persistence_node(state, self.audit_ledger)
        return state
