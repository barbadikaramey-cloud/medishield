"""Comprehensive Unit & Synthetic Benchmark Tests for Clinical Alignment (ADR-015).

Tests domain contracts, fail-closed Pydantic programmatic validators, the deterministic
clinical alignment engine, and all 20 synthetic evaluation benchmark cases (EVAL-CLIN-01 to EVAL-CLIN-20).
"""

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from medishield.db.enums import (
    AgentVerdict,
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
    WorkflowState,
)
from medishield.domain.clinical_align import DeterministicClinicalAlignmentEngine
from medishield.domain.contracts import (
    BedDayBillingRuleContract,
    BedDayReconciliation,
    ClaimsAgentOutput,
    ClinicalAlignmentOutput,
    ClinicalIndicationStatus,
    CriterionEvaluationOutcome,
    FacilityBillingConvention,
    FraudAgentOutput,
    GuidelineCitation,
    KYCAgentOutput,
    LineClinicalEvidence,
    MandatoryGuidelineCriterion,
    MedicalNecessityStatus,
    PerformanceEvidenceStatus,
    PolicyCoverageOutput,
    PriorAuthRequirementStatus,
    PriorAuthVerificationStatus,
    RepeatServiceStatus,
    RiskLevel,
    ServiceLine,
    ServiceModality,
    build_clinical_alignment_output,
)
from medishield.domain.provenance import FieldProvenance, ProvenanceValue
from medishield.domain.decision import (
    CLINICAL_CONFIDENCE_THRESHOLD,
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
from medishield.domain.provenance import FieldProvenance
from medishield.workflows.nodes import clinical_alignment_node, decision_engine_node
from medishield.workflows.state import DocumentReference, create_initial_case_state

# ── Test Helpers ─────────────────────────────────────────────────────────────

def make_provenance_span(
    doc_id: str = "DOC-CLIN-01",
    page: int = 1,
    snippet: str = "Evidence text snippet",
    conf: float = 0.95,
    model_conf: float = 0.90,
    evidence_score: float = 0.92,
) -> FieldProvenance:
    return FieldProvenance(
        document_id=doc_id,
        page_number=page,
        text_content=snippet,
        evidence_quality_score=evidence_score,
        model_confidence=model_conf,
    )


def make_clean_line_evidence(
    line_number: int = 1,
    service_code: str = "99214",
    billed_units: float = 1.0,
    billed_amount: float = 150.00,
    service_date: date = date(2026, 8, 1),
) -> LineClinicalEvidence:
    span = make_provenance_span()
    crit = MandatoryGuidelineCriterion(
        criterion_id="CRIT-99214-01",
        description="Moderate complexity decision making",
        is_mandatory=True,
        evaluation_outcome=CriterionEvaluationOutcome.SATISFIED,
        supporting_spans=[span],
        rationale="Documented clinical visit notes confirm moderate complexity.",
    )
    guideline = GuidelineCitation(
        guideline_id="LCD-99214-01",
        guideline_version="2026.1",
        guideline_title="E/M Services Level 4",
        effective_date=date(2025, 1, 1),
        criteria_evaluations=[crit],
    )
    return LineClinicalEvidence(
        line_number=line_number,
        service_code=service_code,
        modality=ServiceModality.MEDICAL_VISIT_OR_EVAL,
        billed_units=billed_units,
        billed_amount=billed_amount,
        service_date=service_date,
        performance_status=PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
        indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
        necessity_status=MedicalNecessityStatus.MET,
        guideline_citation=guideline,
        prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
        prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
        repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
        supporting_spans=[span],
        line_verdict=AgentVerdict.PASS,
        clinical_finding_summary="Fully supported clinical line item.",
    )


def make_clean_decision_context(
    case_id: str = "CASE-CLIN-CLEAN",
    clinical_output: ClinicalAlignmentOutput | None = None,
    present_docs: list[DocumentType] | None = None,
    cpt_codes: list[str] | None = None,
    service_lines: list[ServiceLine] | None = None,
    claim_amount: float = 150.00,
) -> DecisionInputContext:
    codes = cpt_codes or ["99214"]
    lines = service_lines or [
        ServiceLine(
            line_number=1,
            hcpcs=ProvenanceValue(value=codes[0], confidence=0.98),
            units=ProvenanceValue(value=1.0, confidence=1.0),
            amount=ProvenanceValue(value=claim_amount, confidence=0.98),
            service_date=ProvenanceValue(value="2026-08-01", confidence=0.99),
        )
    ]
    return DecisionInputContext(
        case_id=case_id,
        execution_id="EXEC-" + case_id,
        present_document_types=present_docs or [DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT, DocumentType.DISCHARGE_SUMMARY],
        kyc_output=KYCAgentOutput(verdict=AgentVerdict.PASS, confidence=0.95, latency_ms=10),
        claims_output=ClaimsAgentOutput(
            verdict=AgentVerdict.PASS,
            confidence=0.95,
            claim_amount=ProvenanceValue(value=str(claim_amount), confidence=0.99),
            service_date=ProvenanceValue(value="2026-08-01", confidence=0.99),
            provider_npi=ProvenanceValue(value="1234567890", confidence=0.99),
            cpt_codes=[ProvenanceValue(value=c, confidence=0.95) for c in codes],
            service_lines=lines,
            schema_valid=True,
            line_items_sum_valid=True,
            calculated_lines_sum=claim_amount,
            latency_ms=10,
        ),
        policy_output=PolicyCoverageOutput(
            verdict=AgentVerdict.PASS,
            confidence=0.95,
            covered=True,
            coverage_percentage=100.0,
            applicable_service_codes=codes,
            reasoning_summary="All billed codes covered.",
            latency_ms=10,
        ),
        fraud_output=FraudAgentOutput(
            agent_name="fraud",
            verdict=AgentVerdict.PASS,
            confidence=0.95,
            fraud_score=0.10,
            risk_level=RiskLevel.LOW,
            latency_ms=10,
        ),
        clinical_output=clinical_output,
    )


# ── Contract & Invariant Validation Tests ─────────────────────────────────────

class TestClinicalContractsAndInvariants:
    """Verifies that programmatic domain validators prevent invalid state entries."""

    def test_line_clinical_evidence_pass_without_spans_fails(self) -> None:
        """Line verdict PASS strictly requires non-empty supporting_spans."""
        span = make_provenance_span()
        crit = MandatoryGuidelineCriterion(
            criterion_id="CRIT-99214-01",
            description="Moderate complexity",
            is_mandatory=True,
            evaluation_outcome=CriterionEvaluationOutcome.SATISFIED,
            supporting_spans=[span],
        )
        guideline = GuidelineCitation(
            guideline_id="LCD-99214-01",
            guideline_version="2026.1",
            guideline_title="E/M Services",
            effective_date=date(2025, 1, 1),
            criteria_evaluations=[crit],
        )
        with pytest.raises(ValidationError, match="requires non-empty supporting_spans provenance"):
            LineClinicalEvidence(
                line_number=1,
                service_code="99214",
                modality=ServiceModality.MEDICAL_VISIT_OR_EVAL,
                billed_units=1.0,
                billed_amount=100.0,
                performance_status=PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
                indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
                necessity_status=MedicalNecessityStatus.MET,
                guideline_citation=guideline,
                prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
                prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
                repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
                supporting_spans=[],  # Empty!
                line_verdict=AgentVerdict.PASS,
                clinical_finding_summary="Test",
            )

    def test_line_clinical_evidence_pass_without_guideline_citation_fails(self) -> None:
        """Line verdict PASS requires a valid GuidelineCitation."""
        span = make_provenance_span()
        with pytest.raises(ValidationError, match="requires a valid GuidelineCitation"):
            LineClinicalEvidence(
                line_number=1,
                service_code="99214",
                modality=ServiceModality.MEDICAL_VISIT_OR_EVAL,
                billed_units=1.0,
                billed_amount=100.0,
                performance_status=PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
                indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
                necessity_status=MedicalNecessityStatus.MET,
                guideline_citation=None,  # Missing!
                prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
                prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
                repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
                supporting_spans=[span],
                line_verdict=AgentVerdict.PASS,
                clinical_finding_summary="Test",
            )

    def test_bed_day_reconciliation_verdict_pass_requires_is_reconciled(self) -> None:
        """BedDayReconciliation verdict PASS strictly requires is_reconciled=True."""
        rule = BedDayBillingRuleContract(
            rule_id="BR-01",
            rule_version="1.0",
            facility_type="INPATIENT_ACUTE",
            claim_type="INPATIENT",
            effective_date=date(2025, 1, 1),
            is_approved=True,
            convention=FacilityBillingConvention.MIDNIGHT_CENSUS,
        )
        with pytest.raises(ValidationError, match="reconciliation_verdict=PASS strictly requires is_reconciled=True"):
            BedDayReconciliation(
                billing_rule=rule,
                claim_facility_type="INPATIENT_ACUTE",
                claim_type="INPATIENT",
                admission_datetime=datetime(2026, 8, 1, 10, 0),
                discharge_datetime=datetime(2026, 8, 2, 10, 0),
                billed_bed_days=1.0,
                is_reconciled=False,  # Contradicts PASS!
                reconciliation_verdict=AgentVerdict.PASS,
            )

    def test_bed_day_reconciliation_pass_requires_admission_and_discharge(self) -> None:
        """Reconciliation PASS cannot occur without admission and discharge evidence."""
        rule = BedDayBillingRuleContract(
            rule_id="BR-01",
            rule_version="1.0",
            facility_type="INPATIENT_ACUTE",
            claim_type="INPATIENT",
            effective_date=date(2025, 1, 1),
            is_approved=True,
            convention=FacilityBillingConvention.MIDNIGHT_CENSUS,
        )
        with pytest.raises(ValidationError, match="strictly requires documented admission and discharge"):
            BedDayReconciliation(
                billing_rule=rule,
                claim_facility_type="INPATIENT_ACUTE",
                claim_type="INPATIENT",
                admission_datetime=datetime(2026, 8, 1, 10, 0),
                discharge_datetime=None,  # Missing!
                billed_bed_days=1.0,
                is_reconciled=True,
                reconciliation_verdict=AgentVerdict.PASS,
            )

    def test_bed_day_reconciliation_unapproved_rule_cannot_pass(self) -> None:
        """Reconciliation PASS cannot occur with is_approved=False."""
        rule = BedDayBillingRuleContract(
            rule_id="BR-01",
            rule_version="1.0",
            facility_type="INPATIENT_ACUTE",
            claim_type="INPATIENT",
            effective_date=date(2025, 1, 1),
            is_approved=False,  # Unapproved!
            convention=FacilityBillingConvention.MIDNIGHT_CENSUS,
        )
        with pytest.raises(ValidationError, match="Cannot pass bed-day reconciliation under an unapproved billing rule"):
            BedDayReconciliation(
                billing_rule=rule,
                claim_facility_type="INPATIENT_ACUTE",
                claim_type="INPATIENT",
                admission_datetime=datetime(2026, 8, 1, 10, 0),
                discharge_datetime=datetime(2026, 8, 2, 10, 0),
                billed_bed_days=1.0,
                is_reconciled=True,
                reconciliation_verdict=AgentVerdict.PASS,
            )


# ── EVAL-CLIN-01 to EVAL-CLIN-20 Synthetic Benchmark Matrix ───────────────────

class TestClinicalAlignmentBenchmarkSuite:
    """Rigorous evaluation across all 20 canonical synthetic evaluation benchmark cases."""

    def test_eval_clin_01_clean_concordance(self) -> None:
        """EVAL-CLIN-01: Note documents chest pain, ECG resulted -> PASS, PASS -> Rule P6 APPROVE."""
        line1 = make_clean_line_evidence(1, "99214", 1.0, 150.00)
        span_ecg = make_provenance_span(snippet="ECG performed and resulted: normal sinus rhythm.")
        crit_ecg = MandatoryGuidelineCriterion(
            criterion_id="CRIT-93000-01",
            description="Cardiac symptoms or rhythm evaluation",
            is_mandatory=True,
            evaluation_outcome=CriterionEvaluationOutcome.SATISFIED,
            supporting_spans=[span_ecg],
        )
        line2 = LineClinicalEvidence(
            line_number=2,
            service_code="93000",
            modality=ServiceModality.MEDICAL_VISIT_OR_EVAL,
            billed_units=1.0,
            billed_amount=75.00,
            service_date=date(2026, 8, 1),
            performance_status=PerformanceEvidenceStatus.RESULTED,
            indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
            necessity_status=MedicalNecessityStatus.MET,
            guideline_citation=GuidelineCitation(
                guideline_id="LCD-93000-01",
                guideline_version="2026.1",
                guideline_title="Diagnostic ECG",
                effective_date=date(2025, 1, 1),
                criteria_evaluations=[crit_ecg],
            ),
            prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[span_ecg],
            line_verdict=AgentVerdict.PASS,
            clinical_finding_summary="ECG performed with sinus rhythm documented.",
        )

        output = build_clinical_alignment_output(
            expected_line_numbers=[1, 2],
            line_assessments=[line1, line2],
            bed_day_reconciliation=None,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[],
            latency_ms=15,
        )
        assert output.verdict == AgentVerdict.PASS
        assert output.confidence is not None and output.confidence >= CLINICAL_CONFIDENCE_THRESHOLD

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-01",
            clinical_output=output,
            cpt_codes=["99214", "93000"],
            service_lines=[
                ServiceLine(
                    line_number=1,
                    hcpcs=ProvenanceValue(value="99214", confidence=0.98),
                    units=ProvenanceValue(value=1.0, confidence=1.0),
                    amount=ProvenanceValue(value=150.00, confidence=0.98),
                    service_date=ProvenanceValue(value="2026-08-01", confidence=0.99),
                ),
                ServiceLine(
                    line_number=2,
                    hcpcs=ProvenanceValue(value="93000", confidence=0.98),
                    units=ProvenanceValue(value=1.0, confidence=1.0),
                    amount=ProvenanceValue(value=75.00, confidence=0.98),
                    service_date=ProvenanceValue(value="2026-08-01", confidence=0.99),
                ),
            ],
            claim_amount=225.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P6_CLEAN_PASS
        assert decision.decision == BusinessDecision.APPROVE

    def test_eval_clin_02_secondary_record_support(self) -> None:
        """EVAL-CLIN-02: Op note confirms appendectomy; lab sheet confirms CMP -> PASS, PASS -> APPROVE."""
        span_op = make_provenance_span(doc_id="DOC-OP-NOTE", snippet="Laparoscopic appendectomy performed cleanly.")
        span_lab = make_provenance_span(doc_id="DOC-LAB-SHEET", snippet="Comprehensive metabolic panel resulted.")

        line1 = LineClinicalEvidence(
            line_number=1,
            service_code="44970",
            modality=ServiceModality.SURGICAL_PROCEDURE,
            billed_units=1.0,
            billed_amount=2500.00,
            service_date=date(2026, 8, 1),
            performance_status=PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
            indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
            necessity_status=MedicalNecessityStatus.MET,
            guideline_citation=DeterministicClinicalAlignmentEngine.get_guideline_for_code("44970", date(2026, 8, 1), spans_override=[span_op]),
            prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[span_op],
            line_verdict=AgentVerdict.PASS,
            clinical_finding_summary="Operative note confirms laparoscopy.",
        )
        line2 = LineClinicalEvidence(
            line_number=2,
            service_code="80053",
            modality=ServiceModality.LABORATORY_TEST,
            billed_units=1.0,
            billed_amount=120.00,
            service_date=date(2026, 8, 1),
            performance_status=PerformanceEvidenceStatus.RESULTED,
            indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
            necessity_status=MedicalNecessityStatus.MET,
            guideline_citation=DeterministicClinicalAlignmentEngine.get_guideline_for_code("80053", date(2026, 8, 1), spans_override=[span_lab]),
            prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[span_lab],
            line_verdict=AgentVerdict.PASS,
            clinical_finding_summary="Lab report confirms blood chemistry resulted.",
        )

        output = build_clinical_alignment_output(
            expected_line_numbers=[1, 2],
            line_assessments=[line1, line2],
            bed_day_reconciliation=None,
            clinical_documents_present=["OPERATIVE_NOTE", "LAB_SHEET"],
            low_ocr_quality_pages=[],
            latency_ms=15,
        )
        assert output.verdict == AgentVerdict.PASS

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-02",
            clinical_output=output,
            cpt_codes=["44970", "80053"],
            service_lines=[
                ServiceLine(line_number=1, hcpcs=ProvenanceValue(value="44970", confidence=0.98), units=ProvenanceValue(value=1.0, confidence=1.0), amount=ProvenanceValue(value=2500.00, confidence=0.98), service_date=ProvenanceValue(value="2026-08-01", confidence=0.99)),
                ServiceLine(line_number=2, hcpcs=ProvenanceValue(value="80053", confidence=0.98), units=ProvenanceValue(value=1.0, confidence=1.0), amount=ProvenanceValue(value=120.00, confidence=0.98), service_date=ProvenanceValue(value="2026-08-01", confidence=0.99)),
            ],
            claim_amount=2620.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P6_CLEAN_PASS
        assert decision.decision == BusinessDecision.APPROVE

    def test_eval_clin_03_prior_auth_required_and_verified_approved(self) -> None:
        """EVAL-CLIN-03: CPT 70553 (REQUIRED) with VERIFIED_APPROVED prior auth -> PASS -> APPROVE."""
        span = make_provenance_span(snippet="Brain MRI completed; prior authorization verified: AUTH-98765.")
        line1 = LineClinicalEvidence(
            line_number=1,
            service_code="70553",
            modality=ServiceModality.DIAGNOSTIC_IMAGING,
            billed_units=1.0,
            billed_amount=950.00,
            service_date=date(2026, 8, 1),
            performance_status=PerformanceEvidenceStatus.RESULTED,
            indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
            necessity_status=MedicalNecessityStatus.MET,
            guideline_citation=DeterministicClinicalAlignmentEngine.get_guideline_for_code("70553", date(2026, 8, 1), spans_override=[span]),
            prior_auth_requirement=PriorAuthRequirementStatus.REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.VERIFIED_APPROVED,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[span],
            line_verdict=AgentVerdict.PASS,
            clinical_finding_summary="MRI Brain performed with active verified prior authorization.",
        )
        output = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line1],
            bed_day_reconciliation=None,
            clinical_documents_present=["PRIOR_AUTH_LETTER", "RADIOLOGY_REPORT"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output.verdict == AgentVerdict.PASS

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-03",
            clinical_output=output,
            cpt_codes=["70553"],
            claim_amount=950.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P6_CLEAN_PASS
        assert decision.decision == BusinessDecision.APPROVE

    def test_eval_clin_04_prior_auth_required_but_missing_or_unverified(self) -> None:
        """EVAL-CLIN-04: CPT 70553 (REQUIRED) with missing auth -> INDETERMINATE -> ESCALATE(AMBIGUITY)."""
        span = make_provenance_span(snippet="Brain MRI performed; no auth number.")
        line1 = LineClinicalEvidence(
            line_number=1,
            service_code="70553",
            modality=ServiceModality.DIAGNOSTIC_IMAGING,
            billed_units=1.0,
            billed_amount=950.00,
            service_date=date(2026, 8, 1),
            performance_status=PerformanceEvidenceStatus.RESULTED,
            indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
            necessity_status=MedicalNecessityStatus.MET,
            guideline_citation=None,
            prior_auth_requirement=PriorAuthRequirementStatus.REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.MISSING_OR_UNVERIFIED,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[span],
            line_verdict=AgentVerdict.INDETERMINATE,
            clinical_finding_summary="MRI Brain performed but prior authorization unverified.",
            discrepancy_flag=True,
            discrepancy_description="Prior authorization required but missing.",
        )
        output = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line1],
            bed_day_reconciliation=None,
            clinical_documents_present=["RADIOLOGY_REPORT"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output.verdict == AgentVerdict.INDETERMINATE

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-04",
            clinical_output=output,
            cpt_codes=["70553"],
            claim_amount=950.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P3_AMBIGUITY
        assert decision.decision == BusinessDecision.ESCALATE
        assert decision.escalation_sub_reason == EscalationSubReason.AMBIGUITY

    def test_eval_clin_05_prior_auth_required_and_verified_denied(self) -> None:
        """EVAL-CLIN-05: CPT 70553 (REQUIRED) with VERIFIED_DENIED -> FAIL -> ESCALATE(HUMAN_REVIEW)."""
        span = make_provenance_span(snippet="Brain MRI performed; registry shows prior auth denied on 2026-07-28.")
        line1 = LineClinicalEvidence(
            line_number=1,
            service_code="70553",
            modality=ServiceModality.DIAGNOSTIC_IMAGING,
            billed_units=1.0,
            billed_amount=950.00,
            service_date=date(2026, 8, 1),
            performance_status=PerformanceEvidenceStatus.RESULTED,
            indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
            necessity_status=MedicalNecessityStatus.MET,
            guideline_citation=None,
            prior_auth_requirement=PriorAuthRequirementStatus.REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.VERIFIED_DENIED,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[span],
            line_verdict=AgentVerdict.FAIL,
            clinical_finding_summary="Prior authorization formally denied by payer.",
            discrepancy_flag=True,
            discrepancy_description="Prior authorization formally denied.",
        )
        output = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line1],
            bed_day_reconciliation=None,
            clinical_documents_present=["RADIOLOGY_REPORT"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output.verdict == AgentVerdict.FAIL

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-05",
            clinical_output=output,
            cpt_codes=["70553"],
            claim_amount=950.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P3_AMBIGUITY
        assert decision.decision == BusinessDecision.ESCALATE
        assert decision.escalation_sub_reason == EscalationSubReason.HUMAN_REVIEW

    def test_eval_clin_06_mandatory_criterion_failed(self) -> None:
        """EVAL-CLIN-06: CPT 27447 Knee Replacement: only 2 weeks conservative therapy -> FAIL -> ESCALATE(HUMAN_REVIEW)."""
        span = make_provenance_span(snippet="Patient completed 2 weeks of physical therapy before electing total knee replacement.")
        guideline = DeterministicClinicalAlignmentEngine.get_guideline_for_code(
            "27447",
            date(2026, 8, 1),
            criteria_satisfied_overrides={"CRIT-27447-CONSERVATIVE": False},
            spans_override=[span],
        )
        line1 = LineClinicalEvidence(
            line_number=1,
            service_code="27447",
            modality=ServiceModality.SURGICAL_PROCEDURE,
            billed_units=1.0,
            billed_amount=15000.00,
            service_date=date(2026, 8, 1),
            performance_status=PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
            indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
            necessity_status=MedicalNecessityStatus.NOT_MET,
            guideline_citation=guideline,
            prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[span],
            line_verdict=AgentVerdict.FAIL,
            clinical_finding_summary="Conservative therapy duration insufficient (2 weeks vs required 6 weeks).",
            discrepancy_flag=True,
            discrepancy_description="Mandatory guideline criterion failed.",
        )
        output = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line1],
            bed_day_reconciliation=None,
            clinical_documents_present=["PROGRESS_NOTES", "OPERATIVE_NOTE"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output.verdict == AgentVerdict.FAIL

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-06",
            clinical_output=output,
            cpt_codes=["27447"],
            claim_amount=15000.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P3_AMBIGUITY
        assert decision.decision == BusinessDecision.ESCALATE
        assert decision.escalation_sub_reason == EscalationSubReason.HUMAN_REVIEW

    def test_eval_clin_07_missing_intake_document(self) -> None:
        """EVAL-CLIN-07: Inpatient packet missing mandatory hospital discharge documentation -> RULE-P2-INCOMPLETE."""
        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-07",
            present_docs=[DocumentType.CLAIM_FORM],  # ID_DOCUMENT missing!
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P2_INCOMPLETE
        assert decision.decision == BusinessDecision.ESCALATE
        assert decision.escalation_sub_reason == EscalationSubReason.MISSING_INFORMATION

    def test_eval_clin_08_missing_finding_in_complete_packet(self) -> None:
        """EVAL-CLIN-08: Pneumonia treated; chest X-ray unmentioned in complete packet -> INDETERMINATE -> ESCALATE(HUMAN_REVIEW)."""
        line1 = make_clean_line_evidence(1, "99214", 1.0, 150.00)
        # Line 2: Chest X-Ray completely unmentioned in notes
        line2 = LineClinicalEvidence(
            line_number=2,
            service_code="71045",
            modality=ServiceModality.DIAGNOSTIC_IMAGING,
            billed_units=1.0,
            billed_amount=95.00,
            service_date=date(2026, 8, 1),
            performance_status=PerformanceEvidenceStatus.NOT_DOCUMENTED,
            indication_status=ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION,
            necessity_status=MedicalNecessityStatus.GUIDELINE_UNAVAILABLE,
            guideline_citation=None,
            prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[],
            line_verdict=AgentVerdict.INDETERMINATE,
            clinical_finding_summary="Billed chest X-ray is unmentioned in complete clinical records.",
            discrepancy_flag=True,
            discrepancy_description="Absence of clinical mention.",
        )
        output = build_clinical_alignment_output(
            expected_line_numbers=[1, 2],
            line_assessments=[line1, line2],
            bed_day_reconciliation=None,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output.verdict == AgentVerdict.INDETERMINATE
        assert output.lines_fully_supported_count == 1
        assert output.lines_indeterminate_count == 1

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-08",
            clinical_output=output,
            cpt_codes=["99214", "71045"],
            service_lines=[
                ServiceLine(line_number=1, hcpcs=ProvenanceValue(value="99214", confidence=0.98), units=ProvenanceValue(value=1.0, confidence=1.0), amount=ProvenanceValue(value=150.00, confidence=0.98), service_date=ProvenanceValue(value="2026-08-01", confidence=0.99)),
                ServiceLine(line_number=2, hcpcs=ProvenanceValue(value="71045", confidence=0.98), units=ProvenanceValue(value=1.0, confidence=1.0), amount=ProvenanceValue(value=95.00, confidence=0.98), service_date=ProvenanceValue(value="2026-08-01", confidence=0.99)),
            ],
            claim_amount=245.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P3_AMBIGUITY
        assert decision.decision == BusinessDecision.ESCALATE
        assert decision.escalation_sub_reason == EscalationSubReason.HUMAN_REVIEW

    def test_eval_clin_09_explicit_clinical_contradiction(self) -> None:
        """EVAL-CLIN-09: Note states 'Patient refused surgery; managed conservatively' -> CONFLICT -> ESCALATE(AMBIGUITY)."""
        line1 = DeterministicClinicalAlignmentEngine.evaluate_line_item(
            line_number=1,
            service_code="47562",
            billed_units=1.0,
            billed_amount=4500.00,
            service_date=date(2026, 8, 1),
            clinical_notes_text="Patient refused surgery; managed conservatively with oral antibiotics.",
        )
        assert line1.line_verdict == AgentVerdict.CONFLICT
        assert line1.performance_status == PerformanceEvidenceStatus.EXPLICITLY_CONTRADICTED

        output = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line1],
            bed_day_reconciliation=None,
            clinical_documents_present=["OPERATIVE_NOTE"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output.verdict == AgentVerdict.CONFLICT

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-09",
            clinical_output=output,
            cpt_codes=["47562"],
            claim_amount=4500.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P3_AMBIGUITY
        assert decision.decision == BusinessDecision.ESCALATE
        assert decision.escalation_sub_reason == EscalationSubReason.AMBIGUITY

    def test_eval_clin_10_confirmed_non_performance(self) -> None:
        """EVAL-CLIN-10: Brain MRI at 18:00 when patient was discharged at 11:00 AM -> CONFLICT -> ESCALATE(AMBIGUITY)."""
        line1 = DeterministicClinicalAlignmentEngine.evaluate_line_item(
            line_number=1,
            service_code="70553",
            billed_units=1.0,
            billed_amount=1200.00,
            service_date=date(2026, 8, 1),
            service_timestamp=datetime(2026, 8, 1, 18, 0),
            encounter_discharge_datetime=datetime(2026, 8, 1, 11, 0),
        )
        assert line1.line_verdict == AgentVerdict.CONFLICT
        assert line1.performance_status == PerformanceEvidenceStatus.CONFIRMED_NON_PERFORMANCE

        output = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line1],
            bed_day_reconciliation=None,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output.verdict == AgentVerdict.CONFLICT

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-10",
            clinical_output=output,
            cpt_codes=["70553"],
            claim_amount=1200.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P3_AMBIGUITY
        assert decision.decision == BusinessDecision.ESCALATE
        assert decision.escalation_sub_reason == EscalationSubReason.AMBIGUITY

    def test_eval_clin_11_direct_mismatched_line_identifiers(self) -> None:
        """EVAL-CLIN-11: Assessed lines [1, 3] vs Expected [1, 2] raises ValidationError."""
        line1 = make_clean_line_evidence(1)
        line3 = make_clean_line_evidence(3)
        with pytest.raises(ValidationError, match="Line assessment identifier mismatch"):
            ClinicalAlignmentOutput(
                verdict=AgentVerdict.PASS,
                expected_claim_line_numbers=[1, 2],
                total_lines_assessed=2,
                line_assessments=[line1, line3],
                lines_fully_supported_count=2,
                clinical_summary="Test",
                latency_ms=10,
            )

    def test_eval_clin_12_duplicate_expected_line_identifiers_via_factory(self) -> None:
        """EVAL-CLIN-12: Factory with duplicate expected IDs [1, 1, 2] returns fail-closed INDETERMINATE -> ESCALATE(AMBIGUITY)."""
        line1 = make_clean_line_evidence(1)
        line2 = make_clean_line_evidence(2)
        output = build_clinical_alignment_output(
            expected_line_numbers=[1, 1, 2],
            line_assessments=[line1, line2],
            bed_day_reconciliation=None,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output.verdict == AgentVerdict.INDETERMINATE
        assert output.assessment_sufficient is False
        assert any("DUPLICATE_EXPECTED_LINE_NUMBERS" in d for d in output.blocking_discrepancies)

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-12",
            clinical_output=output,
            cpt_codes=["99214"],
            claim_amount=150.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P3_AMBIGUITY
        assert decision.decision == BusinessDecision.ESCALATE

    def test_eval_clin_13_unassessed_repeat_service_status(self) -> None:
        """EVAL-CLIN-13: Repeated CPT with repeat_service_status=UNASSESSED -> INDETERMINATE -> ESCALATE(AMBIGUITY)."""
        line1 = make_clean_line_evidence(1, "70553", 1.0, 950.00)
        span = make_provenance_span()
        line2 = LineClinicalEvidence(
            line_number=2,
            service_code="70553",
            modality=ServiceModality.DIAGNOSTIC_IMAGING,
            billed_units=1.0,
            billed_amount=950.00,
            service_date=date(2026, 8, 1),
            performance_status=PerformanceEvidenceStatus.RESULTED,
            indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
            necessity_status=MedicalNecessityStatus.MET,
            guideline_citation=None,
            prior_auth_requirement=PriorAuthRequirementStatus.REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.VERIFIED_APPROVED,
            repeat_service_status=RepeatServiceStatus.UNASSESSED,  # Unassessed!
            supporting_spans=[span],
            line_verdict=AgentVerdict.INDETERMINATE,
            clinical_finding_summary="Repeated service on same date has unassessed justification.",
            discrepancy_flag=True,
            discrepancy_description="Repeat service unassessed.",
        )
        output = build_clinical_alignment_output(
            expected_line_numbers=[1, 2],
            line_assessments=[line1, line2],
            bed_day_reconciliation=None,
            clinical_documents_present=["RADIOLOGY_REPORT"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output.verdict == AgentVerdict.INDETERMINATE

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-13",
            clinical_output=output,
            cpt_codes=["70553"],
            claim_amount=1900.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P3_AMBIGUITY
        assert decision.decision == BusinessDecision.ESCALATE
        assert decision.escalation_sub_reason == EscalationSubReason.AMBIGUITY

    def test_eval_clin_14_date_only_bed_day_range_ambiguity(self) -> None:
        """EVAL-CLIN-14: Date admit Oct 10, discharge Oct 11; timestamps missing; range [1, 2] -> INDETERMINATE -> ESCALATE(AMBIGUITY)."""
        rule = BedDayBillingRuleContract(
            rule_id="BR-ACUTE-01",
            rule_version="2026.1",
            facility_type="INPATIENT_ACUTE",
            claim_type="INPATIENT",
            effective_date=date(2025, 1, 1),
            is_approved=True,
            convention=FacilityBillingConvention.MIDNIGHT_CENSUS,
        )
        recon = DeterministicClinicalAlignmentEngine.reconcile_bed_days(
            billed_bed_days=2.0,
            admission_date=date(2026, 10, 10),
            discharge_date=date(2026, 10, 11),
            admission_datetime=None,
            discharge_datetime=None,
            billing_rule=rule,
            claim_facility_type="INPATIENT_ACUTE",
            claim_type="INPATIENT",
        )
        assert recon.is_reconciled is False
        assert recon.uncertainty_flag is True
        assert recon.reconciliation_verdict == AgentVerdict.INDETERMINATE
        assert recon.expected_billable_units_min == 1.0
        assert recon.expected_billable_units_max == 2.0

        line1 = LineClinicalEvidence(
            line_number=1,
            service_code="0120",
            modality=ServiceModality.ROOM_AND_BOARD,
            billed_units=2.0,
            billed_amount=3000.00,
            service_date=date(2026, 10, 11),
            performance_status=PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
            indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
            necessity_status=MedicalNecessityStatus.MET,
            guideline_citation=None,
            prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[make_provenance_span()],
            line_verdict=AgentVerdict.INDETERMINATE,
            clinical_finding_summary="Bed day stay requires timestamp disambiguation.",
            discrepancy_flag=True,
            discrepancy_description="Bed-day range ambiguity.",
        )
        output = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line1],
            bed_day_reconciliation=recon,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output.verdict == AgentVerdict.INDETERMINATE

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-14",
            clinical_output=output,
            cpt_codes=["0120"],
            service_lines=[
                ServiceLine(
                    line_number=1,
                    rev_code=ProvenanceValue(value="0120", confidence=0.98),
                    units=ProvenanceValue(value=2.0, confidence=1.0),
                    amount=ProvenanceValue(value=3000.00, confidence=0.98),
                    service_date=ProvenanceValue(value="2026-10-11", confidence=0.99),
                )
            ],
            claim_amount=3000.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P3_AMBIGUITY
        assert decision.decision == BusinessDecision.ESCALATE

    def test_eval_clin_15_bed_day_is_reconciled_false_with_proposed_pass(self) -> None:
        """EVAL-CLIN-15: Direct instantiation with is_reconciled=False and PASS raises ValidationError."""
        rule = BedDayBillingRuleContract(
            rule_id="BR-ACUTE-01",
            rule_version="2026.1",
            facility_type="INPATIENT_ACUTE",
            claim_type="INPATIENT",
            effective_date=date(2025, 1, 1),
            is_approved=True,
            convention=FacilityBillingConvention.MIDNIGHT_CENSUS,
        )
        with pytest.raises(ValidationError, match="reconciliation_verdict=PASS strictly requires is_reconciled=True"):
            BedDayReconciliation(
                billing_rule=rule,
                claim_facility_type="INPATIENT_ACUTE",
                claim_type="INPATIENT",
                admission_datetime=datetime(2026, 10, 10, 14, 0),
                discharge_datetime=datetime(2026, 10, 11, 10, 0),
                billed_bed_days=1.0,
                is_reconciled=False,  # Validator barrier
                reconciliation_verdict=AgentVerdict.PASS,
            )

    def test_eval_clin_16_confirmed_bed_day_excess(self) -> None:
        """EVAL-CLIN-16: Billed 6 units, midnight census = 1 day -> CONFLICT -> ESCALATE(AMBIGUITY)."""
        rule = BedDayBillingRuleContract(
            rule_id="BR-ACUTE-01",
            rule_version="2026.1",
            facility_type="INPATIENT_ACUTE",
            claim_type="INPATIENT",
            effective_date=date(2025, 1, 1),
            is_approved=True,
            convention=FacilityBillingConvention.MIDNIGHT_CENSUS,
        )
        recon = DeterministicClinicalAlignmentEngine.reconcile_bed_days(
            billed_bed_days=6.0,
            admission_datetime=datetime(2026, 10, 10, 14, 0),
            discharge_datetime=datetime(2026, 10, 11, 10, 0),
            billing_rule=rule,
            claim_facility_type="INPATIENT_ACUTE",
            claim_type="INPATIENT",
        )
        assert recon.is_reconciled is False
        assert recon.reconciliation_verdict == AgentVerdict.CONFLICT
        assert recon.discrepancy_units == 5.0

        line1 = LineClinicalEvidence(
            line_number=1,
            service_code="0120",
            modality=ServiceModality.ROOM_AND_BOARD,
            billed_units=6.0,
            billed_amount=9000.00,
            service_date=date(2026, 10, 11),
            performance_status=PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
            indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
            necessity_status=MedicalNecessityStatus.MET,
            guideline_citation=None,
            prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[make_provenance_span()],
            line_verdict=AgentVerdict.CONFLICT,
            clinical_finding_summary="Billed bed days exceeds documented stay duration by 5 units.",
            discrepancy_flag=True,
            discrepancy_description="Confirmed bed-day excess.",
        )
        output = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line1],
            bed_day_reconciliation=recon,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output.verdict == AgentVerdict.CONFLICT

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-16",
            clinical_output=output,
            cpt_codes=["0120"],
            service_lines=[
                ServiceLine(
                    line_number=1,
                    rev_code=ProvenanceValue(value="0120", confidence=0.98),
                    units=ProvenanceValue(value=6.0, confidence=1.0),
                    amount=ProvenanceValue(value=9000.00, confidence=0.98),
                    service_date=ProvenanceValue(value="2026-10-11", confidence=0.99),
                )
            ],
            claim_amount=9000.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P3_AMBIGUITY
        assert decision.decision == BusinessDecision.ESCALATE

    def test_eval_clin_17_legitimate_resubmission(self) -> None:
        """EVAL-CLIN-17: Replacement claim (Box 22 Freq 7) evaluated cleanly on merits -> PASS -> APPROVE."""
        line1 = make_clean_line_evidence(1, "99214", 1.0, 150.00)
        output = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line1],
            bed_day_reconciliation=None,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output.verdict == AgentVerdict.PASS

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-17",
            clinical_output=output,
            cpt_codes=["99214"],
            claim_amount=150.00,
        )
        # Fraud output verifies Freq 7 replacement claim with low score
        ctx.fraud_output = FraudAgentOutput(
            agent_name="fraud",
            verdict=AgentVerdict.PASS,
            confidence=0.95,
            fraud_score=0.05,
            risk_level=RiskLevel.LOW,
            duplicate_claim_detected=False,
            anomalies=[],
            latency_ms=10,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P6_CLEAN_PASS
        assert decision.decision == BusinessDecision.APPROVE

    def test_eval_clin_18_duplicate_tuple_collision_canonical_resolution(self) -> None:
        """EVAL-CLIN-18: F-01 collision (score 0.30) canonically falls in [0.30, 0.70) -> RULE-P7-BORDERLINE-REVIEW."""
        line1 = make_clean_line_evidence(1, "99214", 1.0, 150.00)
        output = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line1],
            bed_day_reconciliation=None,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output.verdict == AgentVerdict.PASS

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-18",
            clinical_output=output,
            cpt_codes=["99214"],
            claim_amount=150.00,
        )
        # Fraud score = 0.30 (Medium Risk interval [0.30, 0.70))
        ctx.fraud_output = FraudAgentOutput(
            agent_name="fraud",
            verdict=AgentVerdict.PASS,
            confidence=0.95,
            fraud_score=0.30,
            risk_level=RiskLevel.MEDIUM,
            duplicate_claim_detected=False,  # Canonical score without unapproved hard-stop override
            anomalies=["F-01: Historical claim tuple collision"],
            latency_ms=10,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P7_BORDERLINE_REVIEW
        assert decision.decision == BusinessDecision.ESCALATE
        assert decision.escalation_sub_reason == EscalationSubReason.HUMAN_REVIEW

    def test_eval_clin_19_canonical_fraud_rule_concordance(self) -> None:
        """EVAL-CLIN-19: Cumulative fraud score 0.80 >= 0.70 triggers RULE-P4-HIGH-FRAUD -> ESCALATE(FRAUD_REVIEW)."""
        line1 = LineClinicalEvidence(
            line_number=1,
            service_code="49000",
            modality=ServiceModality.SURGICAL_PROCEDURE,
            billed_units=1.0,
            billed_amount=5000.00,
            service_date=date(2026, 8, 1),
            performance_status=PerformanceEvidenceStatus.NOT_DOCUMENTED,
            indication_status=ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION,
            necessity_status=MedicalNecessityStatus.NOT_MET,
            guideline_citation=None,
            prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[],
            line_verdict=AgentVerdict.FAIL,
            clinical_finding_summary="Exploratory laparotomy completely absent from operative records.",
        )
        output = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line1],
            bed_day_reconciliation=None,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output.verdict == AgentVerdict.FAIL

        ctx = make_clean_decision_context(
            case_id="EVAL-CLIN-19",
            clinical_output=output,
            cpt_codes=["49000"],
            claim_amount=5000.00,
        )
        # Cumulative fraud score 0.80 (High Risk >= 0.70)
        ctx.fraud_output = FraudAgentOutput(
            agent_name="fraud",
            verdict=AgentVerdict.FAIL,
            confidence=0.95,
            fraud_score=0.80,
            risk_level=RiskLevel.HIGH,
            duplicate_claim_detected=True,
            anomalies=["F-01 collision", "F-02 document anomaly", "F-03 submission velocity"],
            latency_ms=10,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P4_HIGH_FRAUD
        assert decision.decision == BusinessDecision.ESCALATE
        assert decision.escalation_sub_reason == EscalationSubReason.FRAUD_REVIEW

    def test_eval_clin_20_spec_06_composite_calibrated_confidence(self) -> None:
        """EVAL-CLIN-20: Spec 06 composite confidence formula. Missing calibration inputs produces None -> P7 ESCALATE."""
        # 1. Clean case with complete calibration spans:
        # prov_quality = 0.92, val_completeness = 1.0, model_conf = 0.90
        # Confidence = 0.40(0.92) + 0.35(1.0) + 0.25(0.90) = 0.368 + 0.35 + 0.225 = 0.943 >= 0.75
        span = make_provenance_span(conf=0.95, model_conf=0.90, evidence_score=0.92)
        crit = MandatoryGuidelineCriterion(
            criterion_id="CRIT-99214-01",
            description="Moderate complexity",
            is_mandatory=True,
            evaluation_outcome=CriterionEvaluationOutcome.SATISFIED,
            supporting_spans=[span],
        )
        guideline = GuidelineCitation(
            guideline_id="LCD-99214-01",
            guideline_version="2026.1",
            guideline_title="E/M Services",
            effective_date=date(2025, 1, 1),
            criteria_evaluations=[crit],
        )
        line1 = LineClinicalEvidence(
            line_number=1,
            service_code="99214",
            modality=ServiceModality.MEDICAL_VISIT_OR_EVAL,
            billed_units=1.0,
            billed_amount=150.00,
            service_date=date(2026, 8, 1),
            performance_status=PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
            indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
            necessity_status=MedicalNecessityStatus.MET,
            guideline_citation=guideline,
            prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[span],
            line_verdict=AgentVerdict.PASS,
            clinical_finding_summary="Documented E/M visit.",
        )
        output_clean = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line1],
            bed_day_reconciliation=None,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output_clean.verdict == AgentVerdict.PASS
        assert output_clean.confidence == pytest.approx(0.943, abs=0.002)

        ctx_clean = make_clean_decision_context(
            case_id="EVAL-CLIN-20-CLEAN",
            clinical_output=output_clean,
            cpt_codes=["99214"],
            claim_amount=150.00,
        )
        decision_clean = DeterministicDecisionEngine.adjudicate(ctx_clean)
        assert decision_clean.rule_id_matched == RULE_P6_CLEAN_PASS
        assert decision_clean.decision == BusinessDecision.APPROVE

        # 2. Output with confidence=None fails P6 gate (>= 0.75) and routes to Rule P7 ESCALATE(HUMAN_REVIEW)
        output_uncalibrated = output_clean.model_copy(update={"confidence": None})
        ctx_uncalibrated = make_clean_decision_context(
            case_id="EVAL-CLIN-20-UNCALIBRATED",
            clinical_output=output_uncalibrated,
            cpt_codes=["99214"],
            claim_amount=150.00,
        )
        decision_uncalibrated = DeterministicDecisionEngine.adjudicate(ctx_uncalibrated)
        assert decision_uncalibrated.rule_id_matched == RULE_P7_BORDERLINE_REVIEW
        assert decision_uncalibrated.decision == BusinessDecision.ESCALATE
        assert decision_uncalibrated.escalation_sub_reason == EscalationSubReason.HUMAN_REVIEW
        assert "uncalibrated" in decision_uncalibrated.justification

    def test_p6_missing_clinical_output_safety_guard_regression(self) -> None:
        """Regression test (Phase 0): clinical_output=None must not bypass the clinical gate or produce APPROVE.

        Proves that when clinical assessment is missing/unavailable, Rule P6 (RULE-P6-CLEAN-PASS)
        refuses straight-through approval and fail-closes to Rule P7 (RULE-P7-BORDERLINE-REVIEW)
        with ESCALATE(HUMAN_REVIEW).
        """
        ctx_missing_clinical = make_clean_decision_context(
            case_id="EVAL-CLIN-REGRESSION-NONE",
            clinical_output=None,
            cpt_codes=["99214"],
            claim_amount=150.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx_missing_clinical)
        assert decision.decision != BusinessDecision.APPROVE, (
            "Safety violation: clinical_output=None must not produce APPROVE on Rule P6"
        )
        assert decision.rule_id_matched == RULE_P7_BORDERLINE_REVIEW
        assert decision.decision == BusinessDecision.ESCALATE
        assert decision.escalation_sub_reason == EscalationSubReason.HUMAN_REVIEW
        assert "missing required clinical alignment assessment" in decision.justification


# ── Focused Decision Routing Regression Suite ────────────────────────────────

class TestClinicalDecisionRoutingRegressions:
    """End-to-End and Focused Decision Routing Regression Tests for Clinical Alignment (ADR-015).

    Covers all 10 canonical clinical condition routing behaviors:
    1. test_missing_clinical_output_cannot_produce_approve
    2. test_clinical_indeterminate_routes_to_ambiguity_review
    3. test_clinical_conflict_cannot_produce_approve
    4. test_clinical_unavailable_preserves_p1_precedence
    5. test_low_or_missing_clinical_confidence_blocks_p6
    6. test_high_clinical_confidence_cannot_bypass_failing_core_specialist
    7. test_clinical_fail_does_not_silently_become_autonomous_denial
    8. test_precedence_p1_p2_p4_p5_retained_over_clinical
    9. test_node_6c_traceable_output_when_clinical_docs_present
    10. test_missing_clinical_documentation_cannot_produce_clean_approval
    """

    def test_missing_clinical_output_cannot_produce_approve(self) -> None:
        """1. Missing clinical output (None) must fail closed into P7 Borderline Review, never APPROVE."""
        ctx = make_clean_decision_context(
            case_id="REG-CLIN-01-MISSING",
            clinical_output=None,
            cpt_codes=["99214"],
            claim_amount=150.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.decision != BusinessDecision.APPROVE
        assert decision.decision == BusinessDecision.ESCALATE
        assert decision.rule_id_matched == RULE_P7_BORDERLINE_REVIEW
        assert decision.escalation_sub_reason == EscalationSubReason.HUMAN_REVIEW
        assert "missing required clinical alignment assessment" in decision.justification
        assert decision.recommendation_tag is None

    def test_clinical_indeterminate_routes_to_ambiguity_review(self) -> None:
        """2. Indeterminate clinical output must route to Rule P3 Ambiguity and never convert uncertainty into rejection."""
        # Subcase A: General indeterminate assessment
        output_ind = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[],
            bed_day_reconciliation=None,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[1],  # Degraded OCR quality -> INDETERMINATE
            latency_ms=10,
        )
        assert output_ind.verdict == AgentVerdict.INDETERMINATE

        ctx_a = make_clean_decision_context(
            case_id="REG-CLIN-02A",
            clinical_output=output_ind,
            cpt_codes=["99214"],
            claim_amount=150.00,
        )
        dec_a = DeterministicDecisionEngine.adjudicate(ctx_a)
        assert dec_a.decision != BusinessDecision.APPROVE
        assert dec_a.decision != BusinessDecision.REJECT  # Rule 13: Never convert uncertainty into rejection
        assert dec_a.decision == BusinessDecision.ESCALATE
        assert dec_a.rule_id_matched == RULE_P3_AMBIGUITY
        assert dec_a.escalation_sub_reason == EscalationSubReason.AMBIGUITY
        assert dec_a.recommendation_tag is None

        # Subcase B: Billed service unmentioned in complete packet -> P3 with HUMAN_REVIEW
        line1 = make_clean_line_evidence(1, "99214", 1.0, 150.00)
        line2 = LineClinicalEvidence(
            line_number=2,
            service_code="71045",
            modality=ServiceModality.DIAGNOSTIC_IMAGING,
            billed_units=1.0,
            billed_amount=95.00,
            service_date=date(2026, 8, 1),
            performance_status=PerformanceEvidenceStatus.NOT_DOCUMENTED,
            indication_status=ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION,
            necessity_status=MedicalNecessityStatus.GUIDELINE_UNAVAILABLE,
            guideline_citation=None,
            prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[],
            line_verdict=AgentVerdict.INDETERMINATE,
            clinical_finding_summary="Billed chest X-ray unmentioned in medical records.",
        )
        output_unmentioned = build_clinical_alignment_output(
            expected_line_numbers=[1, 2],
            line_assessments=[line1, line2],
            bed_day_reconciliation=None,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output_unmentioned.verdict == AgentVerdict.INDETERMINATE
        assert output_unmentioned.lines_indeterminate_count == 1
        assert output_unmentioned.lines_fully_supported_count == 1

        ctx_b = make_clean_decision_context(
            case_id="REG-CLIN-02B",
            clinical_output=output_unmentioned,
            cpt_codes=["99214", "71045"],
            service_lines=[
                ServiceLine(line_number=1, hcpcs=ProvenanceValue(value="99214", confidence=0.98), units=ProvenanceValue(value=1.0, confidence=1.0), amount=ProvenanceValue(value=150.00, confidence=0.98), service_date=ProvenanceValue(value="2026-08-01", confidence=0.99)),
                ServiceLine(line_number=2, hcpcs=ProvenanceValue(value="71045", confidence=0.98), units=ProvenanceValue(value=1.0, confidence=1.0), amount=ProvenanceValue(value=95.00, confidence=0.98), service_date=ProvenanceValue(value="2026-08-01", confidence=0.99)),
            ],
            claim_amount=245.00,
        )
        dec_b = DeterministicDecisionEngine.adjudicate(ctx_b)
        assert dec_b.decision != BusinessDecision.APPROVE
        assert dec_b.decision != BusinessDecision.REJECT
        assert dec_b.decision == BusinessDecision.ESCALATE
        assert dec_b.rule_id_matched == RULE_P3_AMBIGUITY
        assert dec_b.escalation_sub_reason == EscalationSubReason.HUMAN_REVIEW
        assert dec_b.recommendation_tag is None

    def test_clinical_conflict_cannot_produce_approve(self) -> None:
        """3. Clinical conflict (contradiction in clinical records) must route to P3 and cannot produce APPROVE."""
        line_conflict = DeterministicClinicalAlignmentEngine.evaluate_line_item(
            line_number=1,
            service_code="47562",
            billed_units=1.0,
            billed_amount=4500.00,
            service_date=date(2026, 8, 1),
            clinical_notes_text="Patient refused surgery; managed conservatively with oral antibiotics.",
        )
        assert line_conflict.line_verdict == AgentVerdict.CONFLICT

        output_conflict = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line_conflict],
            bed_day_reconciliation=None,
            clinical_documents_present=["OPERATIVE_NOTE"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output_conflict.verdict == AgentVerdict.CONFLICT

        ctx = make_clean_decision_context(
            case_id="REG-CLIN-03",
            clinical_output=output_conflict,
            cpt_codes=["47562"],
            claim_amount=4500.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.decision != BusinessDecision.APPROVE
        assert decision.decision == BusinessDecision.ESCALATE
        assert decision.rule_id_matched == RULE_P3_AMBIGUITY
        assert decision.escalation_sub_reason == EscalationSubReason.AMBIGUITY
        assert "contradictory data detected across documents (CONFLICT)" in decision.justification
        assert decision.recommendation_tag is None

    def test_clinical_unavailable_preserves_p1_precedence(self) -> None:
        """4. Clinical output UNAVAILABLE triggers Priority 1 Technical Failure, preserving fail-closed precedence."""
        line_unav = LineClinicalEvidence(
            line_number=1,
            service_code="99214",
            modality=ServiceModality.MEDICAL_VISIT_OR_EVAL,
            billed_units=1.0,
            billed_amount=150.00,
            service_date=date(2026, 8, 1),
            performance_status=PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
            indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
            necessity_status=MedicalNecessityStatus.MET,
            guideline_citation=None,
            prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[],
            line_verdict=AgentVerdict.UNAVAILABLE,
            clinical_finding_summary="Guideline lookup service unavailable.",
        )
        output_unav = ClinicalAlignmentOutput(
            agent_name="clinical_alignment_specialist",
            verdict=AgentVerdict.UNAVAILABLE,
            confidence=0.0,
            expected_claim_line_numbers=[1],
            total_lines_assessed=1,
            line_assessments=[line_unav],
            bed_day_reconciliation=None,
            lines_fully_supported_count=0,
            lines_indeterminate_count=0,
            lines_contradicted_count=0,
            lines_failed_necessity_count=0,
            lines_unavailable_count=1,
            unsupported_billed_amount=150.00,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[],
            assessment_sufficient=False,
            clinical_summary="Clinical alignment specialist dependency UNAVAILABLE.",
            latency_ms=10,
        )

        ctx = make_clean_decision_context(
            case_id="REG-CLIN-04",
            clinical_output=output_unav,
            cpt_codes=["99214"],
            claim_amount=150.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.rule_id_matched == RULE_P1_TECH_FAIL
        assert decision.decision == BusinessDecision.ESCALATE
        assert decision.escalation_sub_reason == EscalationSubReason.TECHNICAL_FAILURE
        assert "Technical Escalation (P1)" in decision.justification
        assert decision.recommendation_tag is None

    def test_low_or_missing_clinical_confidence_blocks_p6(self) -> None:
        """5. Clinical PASS with insufficient (< 0.75) or None confidence blocks P6 and routes to P7 Borderline Review."""
        line1 = make_clean_line_evidence(1, "99214", 1.0, 150.00)
        output_clean = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line1],
            bed_day_reconciliation=None,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output_clean.verdict == AgentVerdict.PASS

        # Scenario A: Low confidence (0.60 < 0.75 threshold)
        output_low = output_clean.model_copy(update={"confidence": 0.60})
        ctx_low = make_clean_decision_context(
            case_id="REG-CLIN-05-LOW",
            clinical_output=output_low,
            cpt_codes=["99214"],
            claim_amount=150.00,
        )
        dec_low = DeterministicDecisionEngine.adjudicate(ctx_low)
        assert dec_low.decision != BusinessDecision.APPROVE
        assert dec_low.decision == BusinessDecision.ESCALATE
        assert dec_low.rule_id_matched == RULE_P7_BORDERLINE_REVIEW
        assert dec_low.escalation_sub_reason == EscalationSubReason.HUMAN_REVIEW
        assert "borderline clinical confidence" in dec_low.justification
        assert dec_low.recommendation_tag is None

        # Scenario B: Missing confidence (confidence=None)
        output_none = output_clean.model_copy(update={"confidence": None})
        ctx_none = make_clean_decision_context(
            case_id="REG-CLIN-05-NONE",
            clinical_output=output_none,
            cpt_codes=["99214"],
            claim_amount=150.00,
        )
        dec_none = DeterministicDecisionEngine.adjudicate(ctx_none)
        assert dec_none.decision != BusinessDecision.APPROVE
        assert dec_none.decision == BusinessDecision.ESCALATE
        assert dec_none.rule_id_matched == RULE_P7_BORDERLINE_REVIEW
        assert dec_none.escalation_sub_reason == EscalationSubReason.HUMAN_REVIEW
        assert "uncalibrated clinical alignment confidence" in dec_none.justification
        assert dec_none.recommendation_tag is None

    def test_high_clinical_confidence_cannot_bypass_failing_core_specialist(self) -> None:
        """6. High clinical confidence (e.g. 0.98) cannot override failing KYC, Policy, or Fraud specialists."""
        line1 = make_clean_line_evidence(1, "99214", 1.0, 150.00)
        output_high = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line1],
            bed_day_reconciliation=None,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output_high.verdict == AgentVerdict.PASS
        assert output_high.confidence is not None and output_high.confidence >= CLINICAL_CONFIDENCE_THRESHOLD

        # Scenario A: KYC Identity FAIL -> P5 HARD_REJECT
        ctx_kyc_fail = make_clean_decision_context(
            case_id="REG-CLIN-06-KYC",
            clinical_output=output_high,
            cpt_codes=["99214"],
            claim_amount=150.00,
        )
        ctx_kyc_fail.kyc_output = KYCAgentOutput(verdict=AgentVerdict.FAIL, confidence=0.99, latency_ms=10)
        dec_kyc = DeterministicDecisionEngine.adjudicate(ctx_kyc_fail)
        assert dec_kyc.decision == BusinessDecision.REJECT
        assert dec_kyc.rule_id_matched == RULE_P5_HARD_REJECT
        assert dec_kyc.recommendation_tag == EscalationSubReason.RECOMMEND_DENIAL.value

        # Scenario B: Policy Confirmed Exclusion -> P5 HARD_REJECT
        ctx_pol_fail = make_clean_decision_context(
            case_id="REG-CLIN-06-POL",
            clinical_output=output_high,
            cpt_codes=["99214"],
            claim_amount=150.00,
        )
        ctx_pol_fail.policy_output = PolicyCoverageOutput(
            verdict=AgentVerdict.FAIL,
            confidence=0.95,
            covered=False,
            coverage_percentage=0.0,
            exclusion_clause_verified=True,
            applicable_service_codes=["99214"],
            reasoning_summary="Service is an excluded cosmetic procedure.",
            latency_ms=10,
        )
        dec_pol = DeterministicDecisionEngine.adjudicate(ctx_pol_fail)
        assert dec_pol.decision == BusinessDecision.REJECT
        assert dec_pol.rule_id_matched == RULE_P5_HARD_REJECT
        assert dec_pol.recommendation_tag == EscalationSubReason.RECOMMEND_DENIAL.value

        # Scenario C: High Fraud Risk -> P4 FRAUD_REVIEW
        ctx_fraud = make_clean_decision_context(
            case_id="REG-CLIN-06-FRAUD",
            clinical_output=output_high,
            cpt_codes=["99214"],
            claim_amount=150.00,
        )
        ctx_fraud.fraud_output = FraudAgentOutput(
            agent_name="fraud",
            verdict=AgentVerdict.FAIL,
            confidence=0.95,
            fraud_score=0.88,
            risk_level=RiskLevel.HIGH,
            anomalies=["Rapid re-billing within 24h", "Provider billing velocity spike"],
            latency_ms=10,
        )
        dec_fraud = DeterministicDecisionEngine.adjudicate(ctx_fraud)
        assert dec_fraud.decision == BusinessDecision.ESCALATE
        assert dec_fraud.rule_id_matched == RULE_P4_HIGH_FRAUD
        assert dec_fraud.escalation_sub_reason == EscalationSubReason.FRAUD_REVIEW

    def test_clinical_fail_does_not_silently_become_autonomous_denial(self) -> None:
        """7. Clinical FAIL must not autonomously reject or attach RECOMMEND_DENIAL without explicit governance authorization."""
        span = make_provenance_span(snippet="Prior auth for procedure 70553 was formally denied on 2026-07-28.")
        line_fail = LineClinicalEvidence(
            line_number=1,
            service_code="70553",
            modality=ServiceModality.DIAGNOSTIC_IMAGING,
            billed_units=1.0,
            billed_amount=950.00,
            service_date=date(2026, 8, 1),
            performance_status=PerformanceEvidenceStatus.RESULTED,
            indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
            necessity_status=MedicalNecessityStatus.MET,
            guideline_citation=None,
            prior_auth_requirement=PriorAuthRequirementStatus.REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.VERIFIED_DENIED,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[span],
            line_verdict=AgentVerdict.FAIL,
            clinical_finding_summary="Prior authorization denied by payer registry.",
        )
        output_fail = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line_fail],
            bed_day_reconciliation=None,
            clinical_documents_present=["RADIOLOGY_REPORT"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )
        assert output_fail.verdict == AgentVerdict.FAIL

        ctx = make_clean_decision_context(
            case_id="REG-CLIN-07",
            clinical_output=output_fail,
            cpt_codes=["70553"],
            claim_amount=950.00,
        )
        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.decision != BusinessDecision.REJECT  # Must NOT be an autonomous rejection
        assert decision.decision != BusinessDecision.APPROVE
        assert decision.decision == BusinessDecision.ESCALATE
        assert decision.rule_id_matched == RULE_P3_AMBIGUITY
        assert decision.escalation_sub_reason == EscalationSubReason.HUMAN_REVIEW
        assert decision.recommendation_tag is None  # Must NOT have RECOMMEND_DENIAL
        assert "clinical alignment criteria unfulfilled" in decision.justification

    def test_precedence_p1_p2_p4_p5_retained_over_clinical(self) -> None:
        """8. Precedence hierarchy (P1 Security/Tech, P2 Incomplete, P4 Fraud, P5 Hard Reject) strictly retained over clinical findings."""
        line_fail = LineClinicalEvidence(
            line_number=1,
            service_code="99214",
            modality=ServiceModality.MEDICAL_VISIT_OR_EVAL,
            billed_units=1.0,
            billed_amount=150.00,
            service_date=date(2026, 8, 1),
            performance_status=PerformanceEvidenceStatus.NOT_DOCUMENTED,
            indication_status=ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION,
            necessity_status=MedicalNecessityStatus.NOT_MET,
            guideline_citation=None,
            prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
            prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
            repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
            supporting_spans=[],
            line_verdict=AgentVerdict.FAIL,
            clinical_finding_summary="Clinical failure",
        )
        output_fail = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[line_fail],
            bed_day_reconciliation=None,
            clinical_documents_present=["DISCHARGE_SUMMARY"],
            low_ocr_quality_pages=[],
            latency_ms=10,
        )

        # 8.1 P1 Security Prompt Injection overrides clinical
        ctx_p1_inject = make_clean_decision_context(
            case_id="REG-CLIN-08-P1-INJ",
            clinical_output=output_fail,
            cpt_codes=["99214"],
        )
        ctx_p1_inject.prompt_injection_detected = True
        dec_p1_inj = DeterministicDecisionEngine.adjudicate(ctx_p1_inject)
        assert dec_p1_inj.rule_id_matched == RULE_P1_SECURITY
        assert dec_p1_inj.escalation_sub_reason == EscalationSubReason.SECURITY_REVIEW

        # 8.2 P1 Security Tamper overrides clinical
        ctx_p1_tamper = make_clean_decision_context(
            case_id="REG-CLIN-08-P1-TAMP",
            clinical_output=output_fail,
            cpt_codes=["99214"],
        )
        ctx_p1_tamper.security_tamper_score = 0.95
        dec_p1_tamp = DeterministicDecisionEngine.adjudicate(ctx_p1_tamper)
        assert dec_p1_tamp.rule_id_matched == RULE_P1_SECURITY
        assert dec_p1_tamp.escalation_sub_reason == EscalationSubReason.SECURITY_REVIEW

        # 8.3 P2 Incomplete Intake Documentation overrides clinical
        ctx_p2 = make_clean_decision_context(
            case_id="REG-CLIN-08-P2",
            clinical_output=output_fail,
            cpt_codes=["99214"],
            present_docs=[DocumentType.CLAIM_FORM],  # ID_DOCUMENT missing
        )
        dec_p2 = DeterministicDecisionEngine.adjudicate(ctx_p2)
        assert dec_p2.rule_id_matched == RULE_P2_INCOMPLETE
        assert dec_p2.escalation_sub_reason == EscalationSubReason.MISSING_INFORMATION

        # 8.4 P4 High Fraud overrides clinical
        ctx_p4 = make_clean_decision_context(
            case_id="REG-CLIN-08-P4",
            clinical_output=output_fail,
            cpt_codes=["99214"],
        )
        ctx_p4.fraud_output = FraudAgentOutput(
            agent_name="fraud",
            verdict=AgentVerdict.FAIL,
            confidence=0.95,
            fraud_score=0.85,
            risk_level=RiskLevel.HIGH,
            duplicate_claim_detected=True,
            latency_ms=10,
        )
        dec_p4 = DeterministicDecisionEngine.adjudicate(ctx_p4)
        assert dec_p4.rule_id_matched == RULE_P4_HIGH_FRAUD
        assert dec_p4.escalation_sub_reason == EscalationSubReason.FRAUD_REVIEW

        # 8.5 P5 Confirmed KYC Identity Hard Reject overrides clinical
        ctx_p5 = make_clean_decision_context(
            case_id="REG-CLIN-08-P5",
            clinical_output=output_fail,
            cpt_codes=["99214"],
        )
        ctx_p5.kyc_output = KYCAgentOutput(verdict=AgentVerdict.FAIL, confidence=0.99, latency_ms=10)
        dec_p5 = DeterministicDecisionEngine.adjudicate(ctx_p5)
        assert dec_p5.rule_id_matched == RULE_P5_HARD_REJECT
        assert dec_p5.decision == BusinessDecision.REJECT

    def test_node_6c_traceable_output_when_clinical_docs_present(self) -> None:
        """9. Node 6C (clinical_alignment_node) populates summary, verdict, confidence, and provenance spans."""
        state = create_initial_case_state("CASE-TRACE-01", execution_id="EXEC-TRACE-01")
        state["claims_summary"] = {
            "cpt_codes": [{"value": "99214"}],
            "service_lines": [
                {
                    "line_number": 1,
                    "hcpcs": {"value": "99214"},
                    "service_code": "99214",
                    "amount": {"value": 150.0},
                    "billed_amount": 150.0,
                    "units": {"value": 1.0},
                    "billed_units": 1.0,
                    "service_date": {"value": "2026-08-01"},
                }
            ],
        }
        # Add a clinical document reference with clinical text content
        clin_doc = DocumentReference(
            document_id="DOC-CLIN-01",
            object_key="uploads/CASE-TRACE-01/clin_note.txt",
            mime_type="text/plain",
            checksum="hash123",
            split_source_page_indices=[1],
        )
        clin_doc["doc_type"] = "DISCHARGE_SUMMARY"
        clin_doc["file_name"] = "discharge_summary.txt"
        clin_doc["text_content"] = (
            "Patient seen in clinic for established office visit, moderate complexity decision making (CPT 99214). "
            "Vital signs stable. Treatment plan continued."
        )
        state["documents"] = [clin_doc]

        # Execute Node 6C directly
        state = clinical_alignment_node(state)

        assert "clinical_alignment_node" in state.get("completed_nodes", [])
        assert state.get("clinical_alignment_verdict") == AgentVerdict.PASS.value
        clin_conf = state.get("clinical_alignment_confidence")
        assert clin_conf is not None and clin_conf >= 0.75

        summary = state.get("clinical_alignment_summary")
        assert summary is not None
        assert summary.get("total_lines_assessed") == 1
        assert summary.get("lines_fully_supported_count") == 1
        assert summary.get("assessment_sufficient") is True

        # Verify line-level evidence and provenance spans
        line_assessments = summary.get("line_assessments", [])
        assert len(line_assessments) == 1
        line1 = line_assessments[0]
        assert line1.get("line_verdict") == AgentVerdict.PASS.value
        assert line1.get("service_code") == "99214"
        spans = line1.get("supporting_spans", [])
        assert len(spans) > 0
        assert spans[0].get("document_id") == "DOC-CLIN-01"
        assert spans[0].get("evidence_quality_score") is not None

    def test_missing_clinical_documentation_cannot_produce_clean_approval(self) -> None:
        """10. Packet without clinical documentation cannot produce clean APPROVE; routes to human review."""
        state = create_initial_case_state("CASE-MISSING-CLIN-DOC", execution_id="EXEC-MISSING-01")
        # Documents only include claim form and driver license (intake complete, but NO clinical docs)
        claim_doc = DocumentReference(
            document_id="DOC-CLAIM-01",
            object_key="uploads/claim.txt",
            mime_type="text/plain",
            checksum="h1",
            split_source_page_indices=[1],
        )
        claim_doc["doc_type"] = DocumentType.CLAIM_FORM.value
        claim_doc["file_name"] = "claim_form.txt"

        id_doc = DocumentReference(
            document_id="DOC-ID-01",
            object_key="uploads/id.txt",
            mime_type="text/plain",
            checksum="h2",
            split_source_page_indices=[1],
        )
        id_doc["doc_type"] = DocumentType.ID_DOCUMENT.value
        id_doc["file_name"] = "drivers_license.txt"

        state["documents"] = [claim_doc, id_doc]
        state["is_complete"] = True

        # Core specialists all report clean pass
        state["kyc_summary"] = {
            "verdict": AgentVerdict.PASS.value,
            "confidence": 0.95,
            "latency_ms": 10,
        }
        state["claims_summary"] = {
            "verdict": AgentVerdict.PASS.value,
            "confidence": 0.95,
            "claim_amount": {"value": "150.00", "confidence": 0.99},
            "service_date": {"value": "2026-08-01", "confidence": 0.99},
            "provider_npi": {"value": "1234567890", "confidence": 0.99},
            "cpt_codes": [{"value": "99214", "confidence": 0.95}],
            "service_lines": [
                {
                    "line_number": 1,
                    "hcpcs": {"value": "99214", "confidence": 0.98},
                    "units": {"value": 1.0, "confidence": 1.0},
                    "amount": {"value": 150.00, "confidence": 0.98},
                    "service_date": {"value": "2026-08-01", "confidence": 0.99},
                }
            ],
            "schema_valid": True,
            "line_items_sum_valid": True,
            "calculated_lines_sum": 150.00,
            "latency_ms": 10,
        }
        state["policy_coverage_summary"] = {
            "verdict": AgentVerdict.PASS.value,
            "confidence": 0.95,
            "covered": True,
            "coverage_percentage": 100.0,
            "applicable_service_codes": ["99214"],
            "reasoning_summary": "Covered outpatient benefit.",
            "latency_ms": 10,
        }
        state["fraud_summary"] = {
            "agent_name": "fraud",
            "verdict": AgentVerdict.PASS.value,
            "confidence": 0.95,
            "fraud_score": 0.10,
            "risk_level": RiskLevel.LOW.value,
            "duplicate_claim_detected": False,
            "latency_ms": 10,
        }

        # Execute Node 6C: detects missing clinical docs and produces fail-closed output
        state = clinical_alignment_node(state)
        assert state["clinical_alignment_verdict"] == AgentVerdict.INDETERMINATE.value
        summary = state.get("clinical_alignment_summary") or {}
        assert summary.get("clinical_documents_present") == []
        assert summary.get("assessment_sufficient") is False

        # Execute Decision Engine Node
        state = decision_engine_node(state)

        # Adjudication MUST NOT be APPROVE
        assert state["decision"] != BusinessDecision.APPROVE.value
        assert state["decision"] == BusinessDecision.ESCALATE.value
        assert state["rule_id_matched"] == RULE_P3_AMBIGUITY
        assert state["workflow_state"] == WorkflowState.AWAITING_HUMAN.value


