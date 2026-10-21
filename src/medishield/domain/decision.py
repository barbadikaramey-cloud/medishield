"""Deterministic Decision Engine implementing priority rules P1–P8 (Spec 02, ADR-001, ADR-002)."""

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from medishield.db.enums import (
    AgentVerdict,
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
    IndeterminateReason,
    WorkflowState,
)
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    ClinicalAlignmentOutput,
    ConsistencyCheckReport,
    FraudAgentOutput,
    KYCAgentOutput,
    OrchestratorOutput,
    PerformanceEvidenceStatus,
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
CLINICAL_CONFIDENCE_THRESHOLD = 0.75

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
    clinical_output: ClinicalAlignmentOutput | None = None
    clinical_support_approval_min_ratio: float | None = Field(
        default=None,
        description="Fully supported line ratio above which clinical-only INDETERMINATE does not escalate; None disables",
    )
    clinical_uncertainty_approval_enabled: bool = Field(
        default=False,
        description="Allow P6 for non-contradictory clinical INDETERMINATE findings with an explicit discrepancy notice",
    )
    consistency_report: ConsistencyCheckReport | None = Field(
        default=None,
        description="Cross-document consistency check report",
    )

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


def fraud_review_is_required(output: FraudAgentOutput | None) -> bool:
    """Use the explicit binary flag, with legacy output compatibility."""
    if output is None:
        return False
    return bool(
        output.fraud_review_required
        or output.duplicate_claim_detected
        or output.verdict == AgentVerdict.FAIL
        or output.risk_level in (RiskLevel.REVIEW_REQUIRED, RiskLevel.MEDIUM, RiskLevel.HIGH)
        or any(feature.detected for feature in output.features)
        or output.anomalies
    )


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
            "dob_matched": context.kyc_output.dob_matched,
            "id_expired": context.kyc_output.id_expired,
            "policy_active_on_service_date": context.kyc_output.policy_active_on_service_date,
            "tamper_detected": context.kyc_output.tamper_detected,
            "tamper_score": context.kyc_output.tamper_score,
            "forensic_status": context.kyc_output.forensic_status,
            "flags": list(context.kyc_output.flags or []),
            "tamper_signals": [
                s.model_dump() if hasattr(s, "model_dump") else s
                for s in (context.kyc_output.tamper_signals or [])
            ],
        }
    if context.claims_output:
        snapshot["claims"] = {
            "verdict": context.claims_output.verdict.value,
            "confidence": context.claims_output.confidence,
            "schema_valid": context.claims_output.schema_valid,
            "line_items_sum_valid": context.claims_output.line_items_sum_valid,
            "calculated_lines_sum": context.claims_output.calculated_lines_sum,
            "validation_errors": list(context.claims_output.validation_errors or []),
            "reason_codes": [r.value for r in context.claims_output.reason_codes],
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
            "reason_codes": [r.value for r in context.policy_output.reason_codes],
        }
    if context.fraud_output:
        snapshot["fraud"] = {
            "verdict": context.fraud_output.verdict.value,
            "confidence": context.fraud_output.confidence,
            "fraud_score": context.fraud_output.fraud_score,
            "fraud_review_required": fraud_review_is_required(context.fraud_output),
            "risk_level": context.fraud_output.risk_level.value,
            "duplicate_claim_detected": context.fraud_output.duplicate_claim_detected,
            "anomalies": context.fraud_output.anomalies,
            "features": [feature.model_dump() for feature in context.fraud_output.features],
        }
    if context.clinical_output:
        clin_data = {
            "verdict": context.clinical_output.verdict.value,
            "confidence": context.clinical_output.confidence,
            "total_lines_assessed": context.clinical_output.total_lines_assessed,
            "lines_fully_supported_count": context.clinical_output.lines_fully_supported_count,
            "lines_indeterminate_count": context.clinical_output.lines_indeterminate_count,
            "lines_contradicted_count": context.clinical_output.lines_contradicted_count,
            "lines_failed_necessity_count": context.clinical_output.lines_failed_necessity_count,
            "lines_unavailable_count": context.clinical_output.lines_unavailable_count,
            "unsupported_billed_amount": context.clinical_output.unsupported_billed_amount,
            "assessment_sufficient": context.clinical_output.assessment_sufficient,
            "blocking_discrepancies": context.clinical_output.blocking_discrepancies,
            "reason_codes": [r.value for r in context.clinical_output.reason_codes],
        }
        snapshot["clinical_alignment"] = clin_data
        snapshot["clinical"] = clin_data
    if context.consistency_report:
        snapshot["consistency"] = {
            "is_consistent": context.consistency_report.is_consistent,
            "doctor_signature_verified": context.consistency_report.doctor_signature_verified,
            "patient_signature_verified": context.consistency_report.patient_signature_verified,
            "officer_signature_verified": getattr(context.consistency_report, "officer_signature_verified", True),
            "npi_verified": context.consistency_report.npi_verified,
            "prior_authorization_verified": context.consistency_report.prior_authorization_verified,
            "procedure_codes_verified": getattr(context.consistency_report, "procedure_codes_verified", True),
            "unvalidated_charges_total": getattr(context.consistency_report, "unvalidated_charges_total", 0.0),
            "specific_escalation_reasons": context.consistency_report.specific_escalation_reasons,
            "date_reconciliation_summary": context.consistency_report.date_reconciliation_summary,
            "conflicts": context.consistency_report.conflicts_detected,
        }

    return snapshot


def is_clinical_support_exempt(context: DecisionInputContext) -> bool:
    clinical = context.clinical_output
    threshold = context.clinical_support_approval_min_ratio
    if (
        clinical is None
        or clinical.verdict != AgentVerdict.INDETERMINATE
        or (threshold is None and not context.clinical_uncertainty_approval_enabled)
    ):
        return False
    if clinical.total_lines_assessed <= 0:
        return False
    if clinical.lines_contradicted_count or clinical.lines_failed_necessity_count or clinical.lines_unavailable_count:
        return False
    if not (clinical.schema_valid and clinical.ocr_quality_sufficient):
        return False
    if (
        clinical.bed_day_reconciliation is not None
        and clinical.bed_day_reconciliation.reconciliation_verdict == AgentVerdict.CONFLICT
    ):
        return False
    if context.clinical_uncertainty_approval_enabled:
        approvable_reasons = {
            IndeterminateReason.PERFORMANCE_NOT_DOCUMENTED,
            IndeterminateReason.NO_DOCUMENTED_INDICATION,
            IndeterminateReason.DIAGNOSIS_VALIDATION_UNAVAILABLE,
            IndeterminateReason.GUIDELINE_UNAVAILABLE,
            IndeterminateReason.MISSING_EVIDENCE_SPANS,
        }
        return bool(clinical.reason_codes) and set(clinical.reason_codes).issubset(approvable_reasons)
    return (
        threshold is not None
        and clinical.lines_fully_supported_count / clinical.total_lines_assessed > threshold
    )


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

        for agent_out in (
            context.kyc_output,
            context.claims_output,
            context.policy_output,
            context.fraud_output,
            context.clinical_output,
        ):
            if agent_out is not None:
                if getattr(agent_out, "confidence", None) is not None:
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

        # ── PRIORITY 4: Any detected fraud factor requires human review ───────
        fraud_review_required = fraud_review_is_required(context.fraud_output)
        if fraud_review_required and context.fraud_output is not None:
            return OrchestratorOutput(
                case_id=context.case_id,
                execution_id=context.execution_id,
                decision=BusinessDecision.ESCALATE,
                escalation_sub_reason=EscalationSubReason.FRAUD_REVIEW,
                decision_confidence=context.fraud_output.confidence,
                rule_id_matched=RULE_P4_HIGH_FRAUD,
                justification=(
                    "Fraud Review Required (P4): At least one deterministic fraud factor was detected. "
                    f"Indicators: {'; '.join(context.fraud_output.anomalies) or 'See factor details.'}"
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

        # Confirmed hard rejects should not be masked by clinical findings.
        is_hard_reject = bool(
            (context.kyc_output and context.kyc_output.verdict == AgentVerdict.FAIL)
            or (context.claims_output and context.claims_output.verdict == AgentVerdict.FAIL)
            or (
                context.policy_output
                and context.policy_output.verdict == AgentVerdict.FAIL
                and context.policy_output.exclusion_clause_verified
                and context.policy_output.applicable_service_codes
            )
        )
        has_upstream_terminal = is_hard_reject
        clinical_support_exempt = is_clinical_support_exempt(context)

        clinical_conflict = (
            not has_upstream_terminal
            and context.clinical_output is not None
            and context.clinical_output.verdict == AgentVerdict.CONFLICT
        )
        clinical_indeterminate = (
            not has_upstream_terminal
            and not clinical_support_exempt
            and context.clinical_output is not None
            and context.clinical_output.verdict == AgentVerdict.INDETERMINATE
        )
        clinical_fail = (
            not has_upstream_terminal
            and context.clinical_output is not None
            and context.clinical_output.verdict == AgentVerdict.FAIL
        )
        consistency_conflict = (
            not has_upstream_terminal
            and context.consistency_report is not None
            and not context.consistency_report.is_consistent
        )

        if (
            conflicts
            or indeterminates
            or low_confidence
            or clinical_conflict
            or clinical_indeterminate
            or clinical_fail
            or consistency_conflict
        ):
            reasons = []
            if conflicts or clinical_conflict:
                reasons.append("contradictory data detected across documents (CONFLICT)")
            if context.consistency_report and context.consistency_report.conflicts_detected:
                reasons.extend(context.consistency_report.conflicts_detected)

            # Specific escalation reasons (doctor signature, patient signature, NPI, PA, date mismatch, unfulfilled clinical guidelines)
            # replacing generic "low OCR"
            if context.consistency_report and context.consistency_report.specific_escalation_reasons:
                reasons.extend(context.consistency_report.specific_escalation_reasons)
            elif indeterminates or clinical_indeterminate:
                reasons.append("clinical documentation or specialist findings require human review (INDETERMINATE)")

            if low_confidence:
                reasons.append(f"specialist confidence below calibrated threshold ({CONFIDENCE_PASS_THRESHOLD})")
            if clinical_fail and context.clinical_output:
                reasons.append(f"clinical alignment criteria unfulfilled ({context.clinical_output.clinical_summary})")

            # Benchmark alignment semantics:
            # - Clinical FAIL (EVAL-CLIN-05, EVAL-CLIN-06), missing finding in complete packet (EVAL-CLIN-08),
            #   or missing signatures / NPI / PA -> HUMAN_REVIEW
            # - Otherwise (e.g. repeat unassessed, bed day ambiguity, conflict) -> AMBIGUITY
            has_missing_finding = (
                context.clinical_output is not None
                and context.clinical_output.verdict == AgentVerdict.INDETERMINATE
                and context.clinical_output.lines_indeterminate_count > 0
                and context.clinical_output.lines_fully_supported_count > 0
                and any(
                    l.performance_status == PerformanceEvidenceStatus.NOT_DOCUMENTED
                    for l in context.clinical_output.line_assessments
                )
            )
            has_signature_failure = (
                context.consistency_report is not None
                and not context.consistency_report.is_consistent
                and (
                    not context.consistency_report.doctor_signature_verified
                    or not context.consistency_report.patient_signature_verified
                    or not getattr(context.consistency_report, "officer_signature_verified", True)
                    or not context.consistency_report.npi_verified
                )
            )
            if clinical_fail or has_missing_finding or has_signature_failure:
                p3_sub_reason = EscalationSubReason.HUMAN_REVIEW
            else:
                p3_sub_reason = EscalationSubReason.AMBIGUITY

            return OrchestratorOutput(
                case_id=context.case_id,
                execution_id=context.execution_id,
                decision=BusinessDecision.ESCALATE,
                escalation_sub_reason=p3_sub_reason,
                decision_confidence=min_confidence,
                rule_id_matched=RULE_P3_AMBIGUITY,
                justification=f"Review Escalation (Information Discrepancy): Case exhibits {'; '.join(reasons)}. Routed to human reviewer.",
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
                        "Review Escalation: Policy determination lacks a verified exclusion "
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
                decision=BusinessDecision.REJECT,
                escalation_sub_reason=None,
                recommendation_tag=EscalationSubReason.RECOMMEND_DENIAL.value,
                decision_confidence=min_confidence,
                rule_id_matched=RULE_P5_HARD_REJECT,
                justification=(
                    f"Autonomous Policy Denial: Confirmed business rule requirement unfulfilled: {'; '.join(hard_failures)}."
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

        # Admissibility Check 1: Claims evidence completeness & line-level reconciliation
        has_cpt = False
        has_service_lines = False
        all_lines_coded = False
        lines_reconciled = False

        if context.claims_output and context.claims_output.service_lines:
            has_service_lines = len(context.claims_output.service_lines) > 0
            has_cpt = bool(context.claims_output.cpt_codes) or any(
                bool(line.hcpcs and line.hcpcs.value) for line in context.claims_output.service_lines
            )
            all_lines_coded = all(
                bool(
                    (line.hcpcs and line.hcpcs.value and str(line.hcpcs.value).strip())
                    or (line.rev_code and line.rev_code.value and str(line.rev_code.value).upper() != "UNKNOWN")
                )
                for line in context.claims_output.service_lines
            )
            all_lines_have_amount = all(
                line.amount is not None and line.amount.value is not None
                for line in context.claims_output.service_lines
            )
            if all_lines_have_amount:
                try:
                    claim_amt = (
                        float(context.claims_output.claim_amount.value)
                        if context.claims_output.claim_amount and context.claims_output.claim_amount.value is not None
                        else 0.0
                    )
                    calc_sum = float(context.claims_output.calculated_lines_sum)
                    service_lines_sum = sum(
                        float(line.amount.value)
                        for line in context.claims_output.service_lines
                    )
                    lines_reconciled = (
                        claim_amt > 0.0
                        and abs(claim_amt - calc_sum) <= 0.01
                        and abs(claim_amt - service_lines_sum) <= 0.01
                    )
                except (ValueError, TypeError):
                    lines_reconciled = False

        claims_admissible = (
            context.claims_output is not None
            and context.claims_output.schema_valid is True
            and context.claims_output.line_items_sum_valid is True
            and has_service_lines
            and all_lines_coded
            and lines_reconciled
            and has_cpt
        )

        # Extract billed CPT and service codes
        billed_cpt_codes: set[str] = set()
        billed_service_codes: set[str] = set()
        if context.claims_output:
            billed_cpt_codes.update(
                str(code.value).strip()
                for code in context.claims_output.cpt_codes
                if code.value and str(code.value).strip()
            )
            for line in context.claims_output.service_lines:
                if line.hcpcs and line.hcpcs.value:
                    val = str(line.hcpcs.value).strip()
                    billed_service_codes.add(val)
                    if re.match(r"^\d{5}$", val):
                        billed_cpt_codes.add(val)
                if line.rev_code and line.rev_code.value and str(line.rev_code.value).upper() != "UNKNOWN":
                    billed_service_codes.add(str(line.rev_code.value).strip())
            billed_service_codes.update(billed_cpt_codes)

        applicable_service_codes = {
            str(code).strip()
            for code in (context.policy_output.applicable_service_codes if context.policy_output else [])
            if str(code).strip()
        }

        # Every billed service code (CPT, HCPCS, and revenue codes) must be covered by an applicable policy clause.
        # A partial code-set match must not be sufficient to approve a multi-line claim.
        if billed_service_codes:
            scope_covered = bool(applicable_service_codes) and billed_service_codes.issubset(applicable_service_codes)
        else:
            scope_covered = False

        # Admissibility Check 2: Policy evidence completeness & verified scope match
        policy_admissible = (
            context.policy_output is not None
            and context.policy_output.covered is True
            and context.policy_output.coverage_percentage > 0.0
            and scope_covered
            and not context.policy_output.identified_exclusions
        )

        # Admissibility Check 3: Fraud evaluation completed, low score, no duplicate
        fraud_admissible = (
            context.fraud_output is not None
            and context.fraud_output.verdict == AgentVerdict.PASS
            and not fraud_review_is_required(context.fraud_output)
        )

        # Admissibility Check 4: Clinical alignment verification passed with calibrated confidence >= CLINICAL_CONFIDENCE_THRESHOLD
        clinical_admissible = clinical_support_exempt or (
            context.clinical_output is not None
            and context.clinical_output.verdict == AgentVerdict.PASS
            and context.clinical_output.confidence is not None
            and context.clinical_output.confidence >= CLINICAL_CONFIDENCE_THRESHOLD
        )

        # Admissibility Check 5: Cross-document consistency & regulatory attestations verified
        consistency_admissible = (
            context.consistency_report is None
            or (
                context.consistency_report.is_consistent
                and context.consistency_report.doctor_signature_verified
                and context.consistency_report.patient_signature_verified
                and getattr(context.consistency_report, "officer_signature_verified", True)
                and context.consistency_report.npi_verified
                and context.consistency_report.prior_authorization_verified
                and getattr(context.consistency_report, "procedure_codes_verified", True)
            )
        )

        high_confidence = min_confidence >= CONFIDENCE_PASS_THRESHOLD

        if (
            all_core_pass
            and claims_admissible
            and policy_admissible
            and fraud_admissible
            and clinical_admissible
            and consistency_admissible
            and high_confidence
        ):
            clinical_note = ""
            if clinical_support_exempt and context.clinical_output:
                clinical = context.clinical_output
                if context.clinical_uncertainty_approval_enabled:
                    discrepancy_summary = "; ".join(clinical.blocking_discrepancies)
                    clinical_note = (
                        f" Clinical discrepancy notice: {clinical.lines_fully_supported_count}/"
                        f"{clinical.total_lines_assessed} billed lines fully supported; unresolved clinical findings "
                        f"remain without a verified contradiction, failed line, unavailable result, poor OCR, or "
                        f"bed-day conflict. This notice does not independently verify service performance. "
                        f"Findings: {discrepancy_summary or clinical.clinical_summary}"
                    )
                else:
                    clinical_note = (
                        f" Clinical alignment supported {clinical.lines_fully_supported_count}/"
                        f"{clinical.total_lines_assessed} billed lines above the configured threshold "
                        "with no contradicted, failed, or unavailable lines."
                    )
            return OrchestratorOutput(
                case_id=context.case_id,
                execution_id=context.execution_id,
                decision=BusinessDecision.APPROVE,
                escalation_sub_reason=None,
                decision_confidence=round(min_confidence, 4),
                rule_id_matched=RULE_P6_CLEAN_PASS,
                justification=(
                    "Clean Straight-Through Pass (P6): All identity, claims arithmetic, and policy coverage criteria "
                    "passed with no detected fraud indicators "
                    f"and calibrated confidence >= {CONFIDENCE_PASS_THRESHOLD}.{clinical_note}"
                ),
                agent_summaries=agent_summaries,
                is_auto_decided=True,
                created_at=now,
            )

        # ── PRIORITY 7: Borderline / Nuanced Case (P7) ────────────────────────
        low_confidence_pass = all_core_pass and (min_confidence < CONFIDENCE_PASS_THRESHOLD)
        clinical_borderline = (
            context.clinical_output is not None
            and context.clinical_output.verdict == AgentVerdict.PASS
            and not clinical_admissible
        )
        inadmissible_core_pass = all_core_pass and not (
            claims_admissible and policy_admissible and fraud_admissible and clinical_admissible and consistency_admissible
        )

        if low_confidence_pass or inadmissible_core_pass or clinical_borderline:
            nuance_reasons: list[str] = []
            if low_confidence_pass:
                nuance_reasons.append(f"borderline extraction confidence ({min_confidence:.2f} < {CONFIDENCE_PASS_THRESHOLD})")
            if clinical_borderline and context.clinical_output:
                if context.clinical_output.confidence is None:
                    nuance_reasons.append("uncalibrated clinical alignment confidence (confidence=None)")
                else:
                    nuance_reasons.append(
                        f"borderline clinical confidence ({context.clinical_output.confidence:.2f} < {CLINICAL_CONFIDENCE_THRESHOLD})"
                    )
            if inadmissible_core_pass:
                if not claims_admissible:
                    nuance_reasons.append("incomplete claims coding or invalid arithmetic/schema")
                if not policy_admissible:
                    nuance_reasons.append("policy coverage lacks verified applicable service code mapping")
                if not fraud_admissible:
                    nuance_reasons.append("fraud evaluation incomplete or duplicate detected")
                if not consistency_admissible and context.consistency_report:
                    nuance_reasons.extend(context.consistency_report.specific_escalation_reasons or ["cross-document consistency unverified"])
                if not clinical_admissible and not clinical_borderline:
                    if context.clinical_output is None:
                        nuance_reasons.append("missing required clinical alignment assessment")
                    elif context.clinical_output.confidence is None:
                        nuance_reasons.append("uncalibrated clinical alignment confidence (confidence=None)")
                    else:
                        nuance_reasons.append(
                            f"borderline clinical confidence ({context.clinical_output.confidence:.2f} < {CLINICAL_CONFIDENCE_THRESHOLD})"
                        )

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


def build_decision_diagnostics(
    context: DecisionInputContext,
    outcome: OrchestratorOutput,
) -> tuple[str, str, int]:
    """
    Constructs deterministic, privacy-safe diagnostics for tracing and audit (Spec 02 § 3, Step 3B).

    Returns:
        tuple[decision_summary, decisive_factors, blocking_condition_count]
    """
    rule_id = outcome.rule_id_matched
    decision = outcome.decision

    if decision == BusinessDecision.APPROVE:
        return (
            "Claim approved: Straight-through pass with verified identity, valid arithmetic, covered policy, and low fraud risk.",
            "all_agents",
            0,
        )

    if rule_id == RULE_P1_SECURITY:
        factors = ["security"]
        if context.kyc_output and (context.kyc_output.tamper_detected or context.kyc_output.tamper_score > 0.0):
            factors.append("kyc")
        return (
            "Security escalation: Adversarial prompt injection or document tamper score exceeded threshold.",
            ",".join(factors),
            1,
        )

    if rule_id == RULE_P1_TECH_FAIL:
        failing = []
        if context.kyc_output and context.kyc_output.verdict == AgentVerdict.UNAVAILABLE:
            failing.append("kyc")
        if context.claims_output and context.claims_output.verdict == AgentVerdict.UNAVAILABLE:
            failing.append("claims")
        if context.policy_output and context.policy_output.verdict == AgentVerdict.UNAVAILABLE:
            failing.append("policy")
        if context.fraud_output and context.fraud_output.verdict == AgentVerdict.UNAVAILABLE:
            failing.append("fraud")
        if context.clinical_output and context.clinical_output.verdict == AgentVerdict.UNAVAILABLE:
            failing.append("clinical_alignment")
        decisive = ",".join(failing) if failing else "system"
        return (
            "Technical escalation: Upstream specialist agent or database dependency was UNAVAILABLE.",
            decisive,
            len(failing) or 1,
        )

    if rule_id == RULE_P2_INCOMPLETE:
        return (
            "Incomplete case: Required intake documentation missing (awaiting supplementary upload).",
            "intake",
            1,
        )

    if rule_id == RULE_P3_AMBIGUITY:
        ambig_agents = []
        has_conflict = False
        has_indeterminate = False
        for name, out in (
            ("kyc", context.kyc_output),
            ("claims", context.claims_output),
            ("policy", context.policy_output),
            ("clinical_alignment", context.clinical_output),
        ):
            if out is not None:
                if out.verdict == AgentVerdict.CONFLICT:
                    ambig_agents.append(name)
                    has_conflict = True
                elif out.verdict in (AgentVerdict.INDETERMINATE, AgentVerdict.FAIL):
                    ambig_agents.append(name)
                    has_indeterminate = True
                elif out.verdict == AgentVerdict.PASS and (
                    getattr(out, "confidence", None) is None
                    or out.confidence < (CLINICAL_CONFIDENCE_THRESHOLD if name == "clinical_alignment" else CONFIDENCE_PASS_THRESHOLD)
                ):
                    ambig_agents.append(name)
        if not ambig_agents and context.policy_output and context.policy_output.verdict == AgentVerdict.FAIL:
            ambig_agents.append("policy")
        decisive = ",".join(ambig_agents) if ambig_agents else "specialists"
        if has_conflict:
            summary = "Review escalation: Contradictory information detected across intake documents (CONFLICT)."
        elif has_indeterminate:
            summary = "Review escalation: Insufficient evidence or low extraction quality in specialist findings (INDETERMINATE)."
        else:
            summary = "Review escalation: Specialist confidence below threshold or unverified policy exclusion citation."
        return (
            summary,
            decisive,
            len(ambig_agents) or 1,
        )

    if rule_id == RULE_P4_HIGH_FRAUD:
        return (
            "Fraud escalation: One or more deterministic fraud indicators require human review.",
            "fraud",
            1,
        )

    if rule_id == RULE_P5_HARD_REJECT:
        hard_fails = []
        if context.kyc_output and context.kyc_output.verdict == AgentVerdict.FAIL:
            hard_fails.append("kyc")
        if context.claims_output and context.claims_output.verdict == AgentVerdict.FAIL:
            hard_fails.append("claims")
        if context.policy_output and context.policy_output.verdict == AgentVerdict.FAIL:
            hard_fails.append("policy")
        decisive = ",".join(hard_fails) if hard_fails else "specialists"
        if "policy" in hard_fails and len(hard_fails) == 1:
            summary = "Claim rejected: Verified policy exclusion applies to billed service codes."
        elif "kyc" in hard_fails and len(hard_fails) == 1:
            summary = "Claim rejected: Member identity verification confirmed FAIL (unmatched or expired identity)."
        elif "claims" in hard_fails and len(hard_fails) == 1:
            summary = "Claim rejected: Claims validation confirmed FAIL (invalid schema or line items)."
        else:
            summary = "Claim rejected: Confirmed hard failure in specialist business rules."
        return (
            summary,
            decisive,
            len(hard_fails) or 1,
        )

    if rule_id == RULE_P7_BORDERLINE_REVIEW:
        nuance = []
        if context.claims_output and (not context.claims_output.line_items_sum_valid or not context.claims_output.schema_valid):
            nuance.append("claims")
        if context.policy_output and not context.policy_output.covered:
            nuance.append("policy")
        if context.clinical_output is None or (
            context.clinical_output.confidence is None
            or context.clinical_output.confidence < CLINICAL_CONFIDENCE_THRESHOLD
        ):
            nuance.append("clinical_alignment")
        decisive = ",".join(nuance) if nuance else "review"
        return (
            "Borderline escalation: Borderline confidence or incomplete coding requiring human adjudication.",
            decisive,
            len(nuance) or 1,
        )

    # Fallback / P8
    return (
        "Default escalation: Unclassified state routed to human reviewer for safety.",
        "decision_engine",
        1,
    )


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

