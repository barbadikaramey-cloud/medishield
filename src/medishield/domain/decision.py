"""Deterministic Decision Engine implementing priority rules P1–P8 (Spec 02, ADR-001, ADR-002)."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from medishield.db.enums import (
    AgentVerdict,
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
    WorkflowState,
)
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    FraudAgentOutput,
    KYCAgentOutput,
    OrchestratorOutput,
    PolicyCoverageOutput,
    RiskLevel,
)
from medishield.domain.state_machine import resolve_decision_workflow_state

# ── Canonical Priority Rule Identifiers (Spec 02 § 3) ─────────────────────────
RULE_P1_SECURITY = "RULE-P1-SECURITY"
RULE_P1_TECH_FAIL = "RULE-P1-TECH-FAIL"
RULE_P2_INCOMPLETE = "RULE-P2-INCOMPLETE"
RULE_P3_AMBIGUITY = "RULE-P3-AMBIGUITY"
RULE_P4_HIGH_FRAUD = "RULE-P4-HIGH-FRAUD"
RULE_P5_HARD_REJECT = "RULE-P5-HARD-REJECT"
RULE_P6_CLEAN_PASS = "RULE-P6-CLEAN-PASS"
RULE_P7_BORDERLINE_REVIEW = "RULE-P7-BORDERLINE-REVIEW"
RULE_P8_CATCH_ALL = "RULE-P8-CATCH-ALL"

# Threshold Constants
SECURITY_TAMPER_THRESHOLD = 0.80
CONFIDENCE_PASS_THRESHOLD = 0.70
FRAUD_HIGH_THRESHOLD = 0.70
FRAUD_LOW_THRESHOLD = 0.30

# Mandatory Intake Document Types
REQUIRED_INTAKE_DOCUMENTS = {DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT}


class DecisionInputContext(BaseModel):
    """Aggregate specialist outputs and security context passed to the Decision Engine."""

    case_id: str
    execution_id: str
    kyc_output: KYCAgentOutput | None = None
    claims_output: ClaimsAgentOutput | None = None
    policy_output: PolicyCoverageOutput | None = None
    fraud_output: FraudAgentOutput | None = None

    present_document_types: list[DocumentType] = Field(
        default_factory=list,
        description="List of document types classified in this intake submission",
    )
    missing_required_documents: bool = Field(
        default=False,
        description="Explicit flag indicating mandatory document absence",
    )
    prompt_injection_detected: bool = Field(
        default=False,
        description="Flag from input safety / guardrail filter",
    )
    security_tamper_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Global tamper score from security scanners",
    )
    created_at: datetime | None = None


def build_agent_summaries_snapshot(context: DecisionInputContext) -> dict[str, Any]:
    """Serializes a consolidated snapshot of specialist findings for audit history."""
    snapshot: dict[str, Any] = {
        "security": {
            "prompt_injection_detected": context.prompt_injection_detected,
            "security_tamper_score": context.security_tamper_score,
        },
        "documents": [d.value for d in context.present_document_types],
    }

    if context.kyc_output:
        snapshot["kyc"] = {
            "verdict": context.kyc_output.verdict.value,
            "confidence": context.kyc_output.confidence,
            "member_db_matched": context.kyc_output.member_db_matched,
            "tamper_detected": context.kyc_output.tamper_detected,
            "tamper_score": context.kyc_output.tamper_score,
        }
    if context.claims_output:
        snapshot["claims"] = {
            "verdict": context.claims_output.verdict.value,
            "confidence": context.claims_output.confidence,
            "schema_valid": context.claims_output.schema_valid,
            "line_items_sum_valid": context.claims_output.line_items_sum_valid,
            "calculated_lines_sum": context.claims_output.calculated_lines_sum,
        }
    if context.policy_output:
        snapshot["policy"] = {
            "verdict": context.policy_output.verdict.value,
            "confidence": context.policy_output.confidence,
            "covered": context.policy_output.covered,
            "coverage_percentage": context.policy_output.coverage_percentage,
            "exclusions": context.policy_output.identified_exclusions,
            "exclusion_clause_verified": context.policy_output.exclusion_clause_verified,
            "applicable_service_codes": context.policy_output.applicable_service_codes,
        }
    if context.fraud_output:
        snapshot["fraud"] = {
            "verdict": context.fraud_output.verdict.value,
            "confidence": context.fraud_output.confidence,
            "fraud_score": context.fraud_output.fraud_score,
            "risk_level": context.fraud_output.risk_level.value,
            "duplicate_claim_detected": context.fraud_output.duplicate_claim_detected,
            "anomalies": context.fraud_output.anomalies,
        }

    return snapshot


class DeterministicDecisionEngine:
    """
    Authoritative, deterministic adjudication engine for MediShield (Spec 02, ADR-001, ADR-002).
    Evaluates upstream specialist findings in strict first-match-wins priority order (P1 through P8).
    Zero generative AI decisioning. Preserves uncertainty without converting to false rejections.
    """

    @classmethod
    def adjudicate(cls, context: DecisionInputContext) -> OrchestratorOutput:
        """
        Executes priority rules P1–P8 in strict sequence.
        Returns the legally binding OrchestratorOutput.
        """
        now = context.created_at or datetime.now(UTC)
        agent_summaries = build_agent_summaries_snapshot(context)

        # Collect available confidences and verdicts
        confidences: list[float] = []
        verdicts: list[AgentVerdict] = []

        for agent_out in (context.kyc_output, context.claims_output, context.policy_output, context.fraud_output):
            if agent_out is not None:
                confidences.append(agent_out.confidence)
                verdicts.append(agent_out.verdict)

        min_confidence = min(confidences) if confidences else 0.0

        # Maximum effective tamper score across security scans and KYC agent
        kyc_tamper = context.kyc_output.tamper_score if context.kyc_output else 0.0
        effective_tamper_score = max(context.security_tamper_score, kyc_tamper)

        # ── PRIORITY 1: Security or System Fault (P1) ─────────────────────────
        if context.prompt_injection_detected:
            return OrchestratorOutput(
                case_id=context.case_id,
                execution_id=context.execution_id,
                decision=BusinessDecision.ESCALATE,
                escalation_sub_reason=EscalationSubReason.SECURITY_REVIEW,
                decision_confidence=min_confidence,
                rule_id_matched=RULE_P1_SECURITY,
                justification="Security Escalation (P1): Adversarial prompt injection detected in case payload.",
                agent_summaries=agent_summaries,
                is_auto_decided=True,
                created_at=now,
            )

        if effective_tamper_score > SECURITY_TAMPER_THRESHOLD:
            return OrchestratorOutput(
                case_id=context.case_id,
                execution_id=context.execution_id,
                decision=BusinessDecision.ESCALATE,
                escalation_sub_reason=EscalationSubReason.SECURITY_REVIEW,
                decision_confidence=min_confidence,
                rule_id_matched=RULE_P1_SECURITY,
                justification=(
                    f"Security Escalation (P1): Severe image or font tampering detected "
                    f"(tamper score: {effective_tamper_score:.2f} > {SECURITY_TAMPER_THRESHOLD})."
                ),
                agent_summaries=agent_summaries,
                is_auto_decided=True,
                created_at=now,
            )

        if any(v == AgentVerdict.UNAVAILABLE for v in verdicts):
            return OrchestratorOutput(
                case_id=context.case_id,
                execution_id=context.execution_id,
                decision=BusinessDecision.ESCALATE,
                escalation_sub_reason=EscalationSubReason.TECHNICAL_FAILURE,
                decision_confidence=min_confidence,
                rule_id_matched=RULE_P1_TECH_FAIL,
                justification="Technical Escalation (P1): Upstream specialist agent or database dependency was UNAVAILABLE.",
                agent_summaries=agent_summaries,
                is_auto_decided=True,
                created_at=now,
            )

        # ── PRIORITY 2: Case Incompleteness (P2) ──────────────────────────────
        missing_docs: list[str] = []
        if context.missing_required_documents:
            missing_docs.append("Mandatory documents flagged missing by intake")
        elif context.present_document_types:
            present_set = set(context.present_document_types)
            for req_doc in REQUIRED_INTAKE_DOCUMENTS:
                if req_doc not in present_set:
                    missing_docs.append(req_doc.value)

        if missing_docs:
            return OrchestratorOutput(
                case_id=context.case_id,
                execution_id=context.execution_id,
                decision=BusinessDecision.ESCALATE,
                escalation_sub_reason=EscalationSubReason.MISSING_INFORMATION,
                decision_confidence=min_confidence,
                rule_id_matched=RULE_P2_INCOMPLETE,
                justification=(
                    f"Incomplete Case (P2): Required intake documentation missing ({', '.join(missing_docs)}). "
                    "Case paused in AWAITING_INFO for supplementary upload."
                ),
                agent_summaries=agent_summaries,
                is_auto_decided=True,
                created_at=now,
            )

        # ── PRIORITY 3: Material Ambiguity / Conflict (P3) ────────────────────
        core_outputs = [
            a for a in (context.kyc_output, context.claims_output, context.policy_output)
            if a is not None
        ]
        
        conflicts = [
            a.verdict for a in core_outputs 
            if a.verdict == AgentVerdict.CONFLICT
        ]
        indeterminates = [
            a.verdict for a in core_outputs 
            if a.verdict == AgentVerdict.INDETERMINATE
        ]
        low_confidence = [
            a for a in core_outputs
            if a.verdict == AgentVerdict.PASS and a.confidence < CONFIDENCE_PASS_THRESHOLD
        ]

        if conflicts or indeterminates or low_confidence:
            reasons = []
            if conflicts:
                reasons.append("contradictory data detected across documents (CONFLICT)")
            if indeterminates:
                reasons.append("insufficient evidence or low OCR quality in core specialist fact (INDETERMINATE)")
            if low_confidence:
                reasons.append(f"specialist confidence below calibrated threshold ({CONFIDENCE_PASS_THRESHOLD})")

            return OrchestratorOutput(
                case_id=context.case_id,
                execution_id=context.execution_id,
                decision=BusinessDecision.ESCALATE,
                escalation_sub_reason=EscalationSubReason.AMBIGUITY,
                decision_confidence=min_confidence,
                rule_id_matched=RULE_P3_AMBIGUITY,
                justification=f"Ambiguity Escalation (P3): Case exhibits {'; '.join(reasons)}. Routed to human reviewer.",
                agent_summaries=agent_summaries,
                is_auto_decided=True,
                created_at=now,
            )

        # ── PRIORITY 4: High Fraud Risk (P4) ──────────────────────────────────
        if context.fraud_output is not None:
            is_high_fraud = (
                context.fraud_output.fraud_score >= FRAUD_HIGH_THRESHOLD
                or context.fraud_output.risk_level == RiskLevel.HIGH
                or context.fraud_output.verdict == AgentVerdict.FAIL
            )
            if is_high_fraud:
                return OrchestratorOutput(
                    case_id=context.case_id,
                    execution_id=context.execution_id,
                    decision=BusinessDecision.ESCALATE,
                    escalation_sub_reason=EscalationSubReason.FRAUD_REVIEW,
                    decision_confidence=context.fraud_output.confidence,
                    rule_id_matched=RULE_P4_HIGH_FRAUD,
                    justification=(
                        f"Fraud Escalation (P4): High fraud risk score ({context.fraud_output.fraud_score:.2f} "
                        f">= {FRAUD_HIGH_THRESHOLD}). Anomalies: {'; '.join(context.fraud_output.anomalies) or 'None'}."
                    ),
                    agent_summaries=agent_summaries,
                    is_auto_decided=True,
                    created_at=now,
                )

        # ── PRIORITY 5: Confirmed Hard Failure (P5, ADR-002) ──────────────────
        hard_failures: list[str] = []
        if context.kyc_output and context.kyc_output.verdict == AgentVerdict.FAIL:
            hard_failures.append("KYC Identity verification confirmed FAIL (unmatched member or expired identity)")
        if context.claims_output and context.claims_output.verdict == AgentVerdict.FAIL:
            hard_failures.append("Claims validation confirmed FAIL (invalid schema or corrupted line items)")
        if context.policy_output and context.policy_output.verdict == AgentVerdict.FAIL:
            if (
                context.policy_output.exclusion_clause_verified
                and context.policy_output.applicable_service_codes
            ):
                hard_failures.append(
                    "Policy Coverage confirmed FAIL (verified exclusion applies to billed service)"
                )
            else:
                # A generic FAIL is not denial evidence. P3 has already handled an
                # explicit INDETERMINATE; this guard makes uncertainty visible.
                return OrchestratorOutput(
                    case_id=context.case_id,
                    execution_id=context.execution_id,
                    decision=BusinessDecision.ESCALATE,
                    escalation_sub_reason=EscalationSubReason.AMBIGUITY,
                    decision_confidence=min_confidence,
                    rule_id_matched=RULE_P3_AMBIGUITY,
                    justification=(
                        "Ambiguity Escalation (P3): policy FAIL lacks a verified exclusion "
                        "citation applicable to a billed service. Routed to human review."
                    ),
                    agent_summaries=agent_summaries,
                    is_auto_decided=True,
                    created_at=now,
                )

        if hard_failures:
            return OrchestratorOutput(
                case_id=context.case_id,
                execution_id=context.execution_id,
                decision=BusinessDecision.ESCALATE,
                escalation_sub_reason=EscalationSubReason.RECOMMEND_DENIAL,
                decision_confidence=min_confidence,
                rule_id_matched=RULE_P5_HARD_REJECT,
                justification=(
                    f"Recommended Denial (P5): Confirmed hard business rule failure requires human confirmation: {'; '.join(hard_failures)}."
                ),
                agent_summaries=agent_summaries,
                is_auto_decided=True,
                created_at=now,
            )

        # ── PRIORITY 6: Clean Straight-Through Pass (P6) ──────────────────────
        def is_core_pass(a):
            return a is not None and a.verdict == AgentVerdict.PASS and a.confidence >= CONFIDENCE_PASS_THRESHOLD

        all_core_pass = (
            is_core_pass(context.kyc_output)
            and is_core_pass(context.claims_output)
            and is_core_pass(context.policy_output)
        )
        fraud_clean = (
            context.fraud_output is None
            or (
                context.fraud_output.fraud_score < FRAUD_LOW_THRESHOLD
                and context.fraud_output.verdict == AgentVerdict.PASS
            )
        )
        high_confidence = min_confidence >= CONFIDENCE_PASS_THRESHOLD

        if all_core_pass and fraud_clean and high_confidence:
            return OrchestratorOutput(
                case_id=context.case_id,
                execution_id=context.execution_id,
                decision=BusinessDecision.APPROVE,
                escalation_sub_reason=None,
                decision_confidence=round(min_confidence, 4),
                rule_id_matched=RULE_P6_CLEAN_PASS,
                justification=(
                    "Clean Straight-Through Pass (P6): All identity, claims arithmetic, and policy coverage criteria "
                    f"passed with low fraud risk (score: {context.fraud_output.fraud_score if context.fraud_output else 0.0:.2f}) "
                    f"and calibrated confidence >= {CONFIDENCE_PASS_THRESHOLD}."
                ),
                agent_summaries=agent_summaries,
                is_auto_decided=True,
                created_at=now,
            )

        # ── PRIORITY 7: Borderline / Nuanced Case (P7) ────────────────────────
        is_medium_fraud = (
            context.fraud_output is not None
            and FRAUD_LOW_THRESHOLD <= context.fraud_output.fraud_score < FRAUD_HIGH_THRESHOLD
        )
        low_confidence_pass = all_core_pass and (min_confidence < CONFIDENCE_PASS_THRESHOLD)

        if is_medium_fraud or low_confidence_pass:
            nuance_reasons: list[str] = []
            if is_medium_fraud and context.fraud_output:
                nuance_reasons.append(f"medium fraud risk tier (score: {context.fraud_output.fraud_score:.2f})")
            if low_confidence_pass:
                nuance_reasons.append(f"borderline extraction confidence ({min_confidence:.2f} < {CONFIDENCE_PASS_THRESHOLD})")

            return OrchestratorOutput(
                case_id=context.case_id,
                execution_id=context.execution_id,
                decision=BusinessDecision.ESCALATE,
                escalation_sub_reason=EscalationSubReason.HUMAN_REVIEW,
                decision_confidence=min_confidence,
                rule_id_matched=RULE_P7_BORDERLINE_REVIEW,
                justification=(
                    f"Borderline Escalation (P7): Case requires human adjudication due to {'; '.join(nuance_reasons)}."
                ),
                agent_summaries=agent_summaries,
                is_auto_decided=True,
                created_at=now,
            )

        # ── PRIORITY 8: Default Catch-All Guard (P8) ──────────────────────────
        return OrchestratorOutput(
            case_id=context.case_id,
            execution_id=context.execution_id,
            decision=BusinessDecision.ESCALATE,
            escalation_sub_reason=EscalationSubReason.HUMAN_REVIEW,
            decision_confidence=min_confidence,
            rule_id_matched=RULE_P8_CATCH_ALL,
            justification="Default Guard Escalation (P8): Unclassified state routed to human reviewer for safety.",
            agent_summaries=agent_summaries,
            is_auto_decided=True,
            created_at=now,
        )

    evaluate = adjudicate


def adjudicate_case(context: DecisionInputContext) -> OrchestratorOutput:
    """Convenience functional interface to DeterministicDecisionEngine.adjudicate."""
    return DeterministicDecisionEngine.adjudicate(context)


def evaluate_and_transition(
    current_state: WorkflowState,
    context: DecisionInputContext,
) -> tuple[OrchestratorOutput, WorkflowState]:
    """
    Evaluates business decision and calculates the next operational workflow state.
    """
    decision_output = DeterministicDecisionEngine.adjudicate(context)
    next_state = resolve_decision_workflow_state(
        decision_output.decision,
        decision_output.escalation_sub_reason,
    )
    return decision_output, next_state

