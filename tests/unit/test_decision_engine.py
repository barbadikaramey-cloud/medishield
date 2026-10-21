"""Unit tests for Deterministic Decision Engine (P1-P8) and Workflow State Machine."""

import pytest

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
    PolicyCoverageOutput,
    ProvenanceValue,
    RiskLevel,
    ServiceLine,
    build_clinical_alignment_output,
)
from medishield.domain.decision import (
    RULE_P1_SECURITY,
    RULE_P1_TECH_FAIL,
    RULE_P2_INCOMPLETE,
    RULE_P3_AMBIGUITY,
    RULE_P4_HIGH_FRAUD,
    RULE_P5_HARD_REJECT,
    RULE_P6_CLEAN_PASS,
    RULE_P7_BORDERLINE_REVIEW,
    DecisionInputContext,
    DeterministicDecisionEngine,
)
from medishield.domain.state_machine import (
    InvalidStateTransitionError,
    can_transition,
    resolve_decision_workflow_state,
    validate_transition,
)
from tests.unit.test_clinical_alignment import make_clean_line_evidence


def make_claims_output(
    verdict: AgentVerdict = AgentVerdict.PASS,
    confidence: float = 0.95,
) -> ClaimsAgentOutput:
    prov = ProvenanceValue(value="150.00", confidence=0.98)
    return ClaimsAgentOutput(
        verdict=verdict,
        confidence=confidence,
        claim_amount=prov,
        service_date=ProvenanceValue(value="2026-08-01", confidence=0.99),
        provider_npi=ProvenanceValue(value="1234567890", confidence=0.95),
        cpt_codes=[ProvenanceValue(value="99213", confidence=0.96)],
        service_lines=[
            ServiceLine(
                line_number=1,
                hcpcs=ProvenanceValue(value="99213", confidence=0.96),
                service_date=ProvenanceValue(value="2026-08-01", confidence=0.99),
                units=ProvenanceValue(value=1.0, confidence=1.0),
                amount=ProvenanceValue(value=150.00, confidence=0.98),
            )
        ],
        schema_valid=True,
        line_items_sum_valid=True,
        calculated_lines_sum=150.00,
        latency_ms=100,
    )


def make_policy_output(
    verdict: AgentVerdict = AgentVerdict.PASS,
    confidence: float = 0.90,
) -> PolicyCoverageOutput:
    return PolicyCoverageOutput(
        verdict=verdict,
        confidence=confidence,
        covered=True,
        coverage_percentage=80.0,
        applicable_service_codes=["99213"],
        reasoning_summary="Eligible covered service.",
        latency_ms=100,
    )


# ── State Machine Tests ───────────────────────────────────────────────────────

class TestWorkflowStateMachine:
    def test_valid_transitions(self) -> None:
        assert can_transition(WorkflowState.RECEIVED, WorkflowState.SCANNING) is True
        assert can_transition(WorkflowState.SCANNING, WorkflowState.SPLIT) is True
        assert can_transition(WorkflowState.SPLIT, WorkflowState.CLASSIFYING) is True
        assert can_transition(WorkflowState.CLASSIFYING, WorkflowState.ANALYZING) is True
        assert can_transition(WorkflowState.ANALYZING, WorkflowState.COMPLETED) is True
        assert can_transition(WorkflowState.ANALYZING, WorkflowState.AWAITING_HUMAN) is True
        assert can_transition(WorkflowState.ANALYZING, WorkflowState.AWAITING_INFO) is True

    def test_invalid_transitions(self) -> None:
        assert can_transition(WorkflowState.RECEIVED, WorkflowState.COMPLETED) is False
        assert can_transition(WorkflowState.COMPLETED, WorkflowState.ANALYZING) is False
        assert can_transition(WorkflowState.FAILED, WorkflowState.RECEIVED) is False

    def test_validate_transition_raises_error(self) -> None:
        with pytest.raises(InvalidStateTransitionError):
            validate_transition(WorkflowState.RECEIVED, WorkflowState.COMPLETED)

    def test_resolve_decision_workflow_state(self) -> None:
        assert resolve_decision_workflow_state(BusinessDecision.APPROVE) == WorkflowState.COMPLETED
        assert resolve_decision_workflow_state(BusinessDecision.REJECT) == WorkflowState.COMPLETED
        assert (
            resolve_decision_workflow_state(
                BusinessDecision.ESCALATE,
                EscalationSubReason.MISSING_INFORMATION,
            )
            == WorkflowState.AWAITING_INFO
        )
        assert (
            resolve_decision_workflow_state(
                BusinessDecision.ESCALATE,
                EscalationSubReason.HUMAN_REVIEW,
            )
            == WorkflowState.AWAITING_HUMAN
        )


# ── Decision Engine Rules Tests (P1–P8) ───────────────────────────────────────

class TestDeterministicDecisionEngine:
    @pytest.fixture
    def engine(self) -> DeterministicDecisionEngine:
        return DeterministicDecisionEngine()

    def test_rule_p1_security_tampering(self, engine: DeterministicDecisionEngine) -> None:
        context = DecisionInputContext(
            case_id="CASE-P1",
            execution_id="EXEC-P1",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            security_tamper_score=0.95,
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.ESCALATE
        assert output.escalation_sub_reason == EscalationSubReason.SECURITY_REVIEW
        assert output.rule_id_matched == RULE_P1_SECURITY

    def test_rule_p1_technical_failure(self, engine: DeterministicDecisionEngine) -> None:
        context = DecisionInputContext(
            case_id="CASE-P1-TECH",
            execution_id="EXEC-P1-TECH",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=KYCAgentOutput(
                agent_name="kyc",
                verdict=AgentVerdict.UNAVAILABLE,
                confidence=0.0,
                latency_ms=100,
            ),
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.ESCALATE
        assert output.escalation_sub_reason == EscalationSubReason.TECHNICAL_FAILURE
        assert output.rule_id_matched == RULE_P1_TECH_FAIL

    def test_rule_p2_missing_documents(self, engine: DeterministicDecisionEngine) -> None:
        context = DecisionInputContext(
            case_id="CASE-P2",
            execution_id="EXEC-P2",
            present_document_types=[DocumentType.CLAIM_FORM],  # Missing ID_DOCUMENT
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.ESCALATE
        assert output.escalation_sub_reason == EscalationSubReason.MISSING_INFORMATION
        assert output.rule_id_matched == RULE_P2_INCOMPLETE

    def test_rule_p3_indeterminate_ambiguity(self, engine: DeterministicDecisionEngine) -> None:
        context = DecisionInputContext(
            case_id="CASE-P3",
            execution_id="EXEC-P3",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=KYCAgentOutput(
                agent_name="kyc",
                verdict=AgentVerdict.INDETERMINATE,
                confidence=0.5,
                latency_ms=100,
            ),
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.ESCALATE
        assert output.escalation_sub_reason == EscalationSubReason.AMBIGUITY
        assert output.rule_id_matched == RULE_P3_AMBIGUITY

    def test_rule_p4_high_fraud(self, engine: DeterministicDecisionEngine) -> None:
        context = DecisionInputContext(
            case_id="CASE-P4",
            execution_id="EXEC-P4",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=KYCAgentOutput(
                agent_name="kyc",
                verdict=AgentVerdict.PASS,
                confidence=0.9,
                latency_ms=100,
            ),
            claims_output=make_claims_output(AgentVerdict.PASS, confidence=0.9),
            policy_output=make_policy_output(AgentVerdict.PASS, confidence=0.9),
            fraud_output=FraudAgentOutput(
                agent_name="fraud",
                verdict=AgentVerdict.PASS,
                confidence=0.9,
                fraud_score=0.85,
                risk_level=RiskLevel.HIGH,
                latency_ms=100,
            ),
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.ESCALATE
        assert output.escalation_sub_reason == EscalationSubReason.FRAUD_REVIEW
        assert output.rule_id_matched == RULE_P4_HIGH_FRAUD

    def test_rule_p4_duplicate_claim_triggers_fraud_review(
        self, engine: DeterministicDecisionEngine
    ) -> None:
        context = DecisionInputContext(
            case_id="CASE-P4-DUP",
            execution_id="EXEC-P4-DUP",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=KYCAgentOutput(verdict=AgentVerdict.PASS, confidence=0.9, latency_ms=100),
            claims_output=make_claims_output(AgentVerdict.PASS, confidence=0.9),
            policy_output=make_policy_output(AgentVerdict.PASS, confidence=0.9),
            fraud_output=FraudAgentOutput(
                agent_name="fraud",
                verdict=AgentVerdict.INDETERMINATE,
                confidence=0.9,
                fraud_score=0.30,
                risk_level=RiskLevel.MEDIUM,
                duplicate_claim_detected=True,
                duplicate_claim_id="CLM-2024-000005",
                anomalies=["Collision detected with historical claim 'CLM-2024-000005'"],
                latency_ms=100,
            ),
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.ESCALATE
        assert output.escalation_sub_reason == EscalationSubReason.FRAUD_REVIEW
        assert output.rule_id_matched == RULE_P4_HIGH_FRAUD

    def test_rule_p4_single_binary_factor_triggers_fraud_review(
        self, engine: DeterministicDecisionEngine
    ) -> None:
        context = DecisionInputContext(
            case_id="CASE-P4-SINGLE-FACTOR",
            execution_id="EXEC-P4-SINGLE-FACTOR",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=KYCAgentOutput(verdict=AgentVerdict.PASS, confidence=0.95, latency_ms=10),
            claims_output=make_claims_output(AgentVerdict.PASS, confidence=0.95),
            policy_output=make_policy_output(AgentVerdict.PASS, confidence=0.95),
            fraud_output=FraudAgentOutput(
                verdict=AgentVerdict.INDETERMINATE,
                confidence=1.0,
                fraud_score=1.0,
                fraud_review_required=True,
                risk_level=RiskLevel.REVIEW_REQUIRED,
                anomalies=["Single factor detected"],
                latency_ms=1,
            ),
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.ESCALATE
        assert output.escalation_sub_reason == EscalationSubReason.FRAUD_REVIEW
        assert output.rule_id_matched == RULE_P4_HIGH_FRAUD
        assert "one deterministic fraud factor" in output.justification.lower()

    def test_rule_p4_routes_positive_legacy_score_to_review(
        self, engine: DeterministicDecisionEngine
    ) -> None:
        context = DecisionInputContext(
            case_id="CASE-P4-LEGACY-SCORE",
            execution_id="EXEC-P4-LEGACY-SCORE",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            fraud_output=FraudAgentOutput(
                verdict=AgentVerdict.PASS,
                confidence=1.0,
                fraud_score=0.05,
                risk_level=RiskLevel.LOW,
                anomalies=["Legacy factor detector reported an anomaly"],
                latency_ms=1,
            ),
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.ESCALATE
        assert output.escalation_sub_reason == EscalationSubReason.FRAUD_REVIEW
        assert output.rule_id_matched == RULE_P4_HIGH_FRAUD

    def test_rule_p5_hard_reject(self, engine: DeterministicDecisionEngine) -> None:
        context = DecisionInputContext(
            case_id="CASE-P5",
            execution_id="EXEC-P5",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=KYCAgentOutput(
                agent_name="kyc",
                verdict=AgentVerdict.FAIL,
                confidence=0.95,
                latency_ms=100,
            ),
            claims_output=make_claims_output(AgentVerdict.PASS, confidence=0.9),
            policy_output=make_policy_output(AgentVerdict.PASS, confidence=0.9),
            fraud_output=FraudAgentOutput(
                agent_name="fraud",
                verdict=AgentVerdict.PASS,
                confidence=0.9,
                fraud_score=0.1,
                risk_level=RiskLevel.LOW,
                latency_ms=100,
            ),
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.REJECT
        assert output.recommendation_tag == EscalationSubReason.RECOMMEND_DENIAL.value
        assert output.is_auto_decided is True
        assert output.rule_id_matched == RULE_P5_HARD_REJECT

    def test_policy_fail_without_verified_exclusion_cannot_recommend_denial(
        self, engine: DeterministicDecisionEngine
    ) -> None:
        context = DecisionInputContext(
            case_id="CASE-P5-UNVERIFIED",
            execution_id="EXEC-P5-UNVERIFIED",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=KYCAgentOutput(verdict=AgentVerdict.PASS, confidence=0.9, latency_ms=1),
            claims_output=make_claims_output(AgentVerdict.PASS, confidence=0.9),
            policy_output=make_policy_output(AgentVerdict.FAIL, confidence=0.9),
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.ESCALATE
        assert output.escalation_sub_reason == EscalationSubReason.AMBIGUITY
        assert output.recommendation_tag is None

    def test_policy_indeterminate_empty_corpus_cannot_recommend_denial(
        self, engine: DeterministicDecisionEngine
    ) -> None:
        context = DecisionInputContext(
            case_id="CASE-P5-EMPTY",
            execution_id="EXEC-P5-EMPTY",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=KYCAgentOutput(verdict=AgentVerdict.PASS, confidence=0.9, latency_ms=1),
            claims_output=make_claims_output(AgentVerdict.PASS, confidence=0.9),
            policy_output=make_policy_output(AgentVerdict.INDETERMINATE, confidence=0.45),
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.ESCALATE
        assert output.escalation_sub_reason == EscalationSubReason.AMBIGUITY
        assert output.recommendation_tag is None

    def test_rule_p6_clean_auto_approve(self, engine: DeterministicDecisionEngine) -> None:
        clin_evidence = make_clean_line_evidence(
            line_number=1,
            service_code="99213",
            billed_units=1.0,
            billed_amount=150.00,
        )
        clin_output = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[clin_evidence],
            clinical_documents_present=True,
        )
        context = DecisionInputContext(
            case_id="CASE-P6",
            execution_id="EXEC-P6",
            present_document_types=[
                DocumentType.CLAIM_FORM,
                DocumentType.ID_DOCUMENT,
                DocumentType.DISCHARGE_SUMMARY,
            ],
            kyc_output=KYCAgentOutput(
                agent_name="kyc",
                verdict=AgentVerdict.PASS,
                confidence=0.92,
                latency_ms=100,
            ),
            claims_output=make_claims_output(AgentVerdict.PASS, confidence=0.95),
            policy_output=make_policy_output(AgentVerdict.PASS, confidence=0.90),
            fraud_output=FraudAgentOutput(
                agent_name="fraud",
                verdict=AgentVerdict.PASS,
                confidence=0.95,
                fraud_score=0.12,
                risk_level=RiskLevel.LOW,
                latency_ms=100,
            ),
            clinical_output=clin_output,
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.APPROVE
        assert output.is_auto_decided is True
        assert output.rule_id_matched == RULE_P6_CLEAN_PASS

    def test_rule_p7_nonfraud_inadmissible_case(self, engine: DeterministicDecisionEngine) -> None:
        context = DecisionInputContext(
            case_id="CASE-P7",
            execution_id="EXEC-P7",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=KYCAgentOutput(
                agent_name="kyc",
                verdict=AgentVerdict.PASS,
                confidence=0.9,
                latency_ms=100,
            ),
            claims_output=make_claims_output(AgentVerdict.PASS, confidence=0.9),
            policy_output=make_policy_output(AgentVerdict.PASS, confidence=0.9),
            fraud_output=FraudAgentOutput(
                agent_name="fraud",
                verdict=AgentVerdict.PASS,
                confidence=0.9,
                fraud_score=0.0,
                risk_level=RiskLevel.LOW,
                latency_ms=100,
            ),
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.ESCALATE
        assert output.escalation_sub_reason == EscalationSubReason.HUMAN_REVIEW
        assert output.rule_id_matched == RULE_P7_BORDERLINE_REVIEW

    def test_rule_p6_inadmissible_missing_cpt_codes(self, engine: DeterministicDecisionEngine) -> None:
        """Proves that a claim with no CPT codes cannot auto-approve and escalates to human review."""
        claims = make_claims_output(AgentVerdict.PASS, confidence=0.95)
        claims.cpt_codes = []
        claims.service_lines = []

        context = DecisionInputContext(
            case_id="CASE-P6-NO-CPT",
            execution_id="EXEC-P6-NO-CPT",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=KYCAgentOutput(verdict=AgentVerdict.PASS, confidence=0.95, latency_ms=10),
            claims_output=claims,
            policy_output=make_policy_output(AgentVerdict.PASS, confidence=0.95),
            fraud_output=FraudAgentOutput(verdict=AgentVerdict.PASS, confidence=0.95, fraud_score=0.1, risk_level=RiskLevel.LOW, latency_ms=10),
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.ESCALATE
        assert output.rule_id_matched == RULE_P7_BORDERLINE_REVIEW
        assert "incomplete claims coding" in output.justification

    def test_rule_p6_inadmissible_missing_policy_scope(self, engine: DeterministicDecisionEngine) -> None:
        """Proves that policy PASS without verified applicable service codes cannot auto-approve."""
        pol = make_policy_output(AgentVerdict.PASS, confidence=0.95)
        pol.applicable_service_codes = []

        context = DecisionInputContext(
            case_id="CASE-P6-NO-SCOPE",
            execution_id="EXEC-P6-NO-SCOPE",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=KYCAgentOutput(verdict=AgentVerdict.PASS, confidence=0.95, latency_ms=10),
            claims_output=make_claims_output(AgentVerdict.PASS, confidence=0.95),
            policy_output=pol,
            fraud_output=FraudAgentOutput(verdict=AgentVerdict.PASS, confidence=0.95, fraud_score=0.1, risk_level=RiskLevel.LOW, latency_ms=10),
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.ESCALATE
        assert output.rule_id_matched == RULE_P7_BORDERLINE_REVIEW
        assert "policy coverage lacks verified applicable service code mapping" in output.justification

    def test_rule_p6_requires_policy_scope_for_every_billed_code(
        self, engine: DeterministicDecisionEngine
    ) -> None:
        """A match for one CPT must not cover another unmatched billed CPT."""
        claims = make_claims_output(AgentVerdict.PASS, confidence=0.95)
        claims.cpt_codes.append(ProvenanceValue(value="93000", confidence=0.96))
        policy = make_policy_output(AgentVerdict.PASS, confidence=0.95)
        policy.applicable_service_codes = ["99213"]

        context = DecisionInputContext(
            case_id="CASE-P6-PARTIAL-SCOPE",
            execution_id="EXEC-P6-PARTIAL-SCOPE",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=KYCAgentOutput(verdict=AgentVerdict.PASS, confidence=0.95, latency_ms=10),
            claims_output=claims,
            policy_output=policy,
            fraud_output=FraudAgentOutput(verdict=AgentVerdict.PASS, confidence=0.95, fraud_score=0.1, risk_level=RiskLevel.LOW, latency_ms=10),
        )

        output = engine.evaluate(context)

        assert output.decision == BusinessDecision.ESCALATE
        assert output.rule_id_matched == RULE_P7_BORDERLINE_REVIEW
        assert "policy coverage lacks verified applicable service code mapping" in output.justification

    def test_rule_p6_inadmissible_missing_fraud_evaluation(self, engine: DeterministicDecisionEngine) -> None:
        """Proves that a claim with missing fraud evaluation cannot auto-approve."""
        context = DecisionInputContext(
            case_id="CASE-P6-NO-FRAUD",
            execution_id="EXEC-P6-NO-FRAUD",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=KYCAgentOutput(verdict=AgentVerdict.PASS, confidence=0.95, latency_ms=10),
            claims_output=make_claims_output(AgentVerdict.PASS, confidence=0.95),
            policy_output=make_policy_output(AgentVerdict.PASS, confidence=0.95),
            fraud_output=None,
        )
        output = engine.evaluate(context)
        assert output.decision == BusinessDecision.ESCALATE
        assert output.rule_id_matched == RULE_P7_BORDERLINE_REVIEW
        assert "fraud evaluation incomplete" in output.justification
