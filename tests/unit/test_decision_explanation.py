"""Unit Tests for Decision Explanation Engine (ADR-011, Spec 02, Spec 04).

Tests deterministic markdown synthesis, LLM-aided generation, graceful offline fallbacks,
and API schema compatibility.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from medishield.api.schemas.cases import CaseDetailResponse
from medishield.db.enums import (
    AgentVerdict,
    BusinessDecision,
    EscalationSubReason,
)
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    FraudAgentOutput,
    FraudFeature,
    KYCAgentOutput,
    OrchestratorOutput,
    PolicyCoverageOutput,
    RiskLevel,
    ServiceLine,
)
from medishield.domain.decision import DecisionInputContext
from medishield.domain.explanation import (
    generate_decision_explanation,
    generate_deterministic_explanation,
)
from medishield.domain.provenance import ProvenanceValue


def _build_sample_context(
    case_id: str = "CASE-20261011-TEST01",
    kyc_verdict: AgentVerdict = AgentVerdict.PASS,
    claims_verdict: AgentVerdict = AgentVerdict.PASS,
    policy_verdict: AgentVerdict = AgentVerdict.PASS,
    fraud_score: float = 0.05,
) -> tuple[DecisionInputContext, OrchestratorOutput]:
    now = datetime.now(UTC)

    kyc_output = KYCAgentOutput(
        verdict=kyc_verdict,
        confidence=0.95,
        member_id=ProvenanceValue(value="MEM-10023", confidence=0.99),
        member_db_matched=True,
        tamper_score=0.0,
        flags=[],
        latency_ms=120,
    )

    claims_output = ClaimsAgentOutput(
        verdict=claims_verdict,
        confidence=0.98,
        claim_amount=ProvenanceValue(value=4095.00, confidence=0.99),
        service_date=ProvenanceValue(value="2026-09-15", confidence=0.99),
        provider_npi=ProvenanceValue(value="1982736450", confidence=0.99),
        service_lines=[
            ServiceLine(
                line_number=1,
                hcpcs=ProvenanceValue(value="99213", confidence=0.99),
                service_date=ProvenanceValue(value="2026-09-15", confidence=0.99),
                units=ProvenanceValue(value=1.0, confidence=0.99),
                amount=ProvenanceValue(value=3750.00, confidence=0.99),
            ),
            ServiceLine(
                line_number=2,
                hcpcs=ProvenanceValue(value="80053", confidence=0.99),
                service_date=ProvenanceValue(value="2026-09-15", confidence=0.99),
                units=ProvenanceValue(value=1.0, confidence=0.99),
                amount=ProvenanceValue(value=250.00, confidence=0.99),
            ),
            ServiceLine(
                line_number=3,
                hcpcs=ProvenanceValue(value="85025", confidence=0.99),
                service_date=ProvenanceValue(value="2026-09-15", confidence=0.99),
                units=ProvenanceValue(value=1.0, confidence=0.99),
                amount=ProvenanceValue(value=95.00, confidence=0.99),
            ),
        ],
        line_items_sum_valid=True,
        calculated_lines_sum=4095.00,
        schema_valid=True,
        cpt_codes=[
            ProvenanceValue(value="99213", confidence=0.99),
            ProvenanceValue(value="80053", confidence=0.99),
            ProvenanceValue(value="85025", confidence=0.99),
        ],
        icd10_codes=[ProvenanceValue(value="J06.9", confidence=0.99)],
        validation_errors=[],
        latency_ms=180,
    )

    policy_output = PolicyCoverageOutput(
        verdict=policy_verdict,
        confidence=0.92,
        covered=True,
        coverage_percentage=100.0,
        effective_on_service_date=True,
        exclusion_clause_verified=False,
        cited_clauses=[],
        reasoning_summary="All billed diagnostic and lab services covered at 100% under Gold plan.",
        latency_ms=210,
    )

    fraud_output = FraudAgentOutput(
        verdict=AgentVerdict.PASS,
        confidence=0.94,
        fraud_score=fraud_score,
        risk_level=RiskLevel.LOW,
        features=[
            FraudFeature(
                feature_name="duplicate_claim",
                raw_value=0.0,
                normalized_score=0.0,
                weight=0.25,
                description="Duplicate collision",
            )
        ],
        anomalies=[],
        latency_ms=95,
    )

    context = DecisionInputContext(
        case_id=case_id,
        execution_id="exec-test-01",
        kyc_output=kyc_output,
        claims_output=claims_output,
        policy_output=policy_output,
        fraud_output=fraud_output,
        present_document_types=[],
        missing_required_documents=False,
        security_tamper_score=0.0,
    )

    decision_output = OrchestratorOutput(
        case_id=case_id,
        execution_id="exec-test-01",
        decision=BusinessDecision.APPROVE,
        escalation_sub_reason=None,
        decision_confidence=0.92,
        rule_id_matched="RULE-P6-CLEAN-PASS",
        justification="Clean Straight-Through Pass (P6): All criteria verified.",
        agent_summaries={},
        is_auto_decided=True,
        created_at=now,
    )

    return context, decision_output


def test_deterministic_explanation_approve():
    """Verifies that an approved case generates complete, structured markdown explanation."""
    context, decision_output = _build_sample_context()
    explanation = generate_deterministic_explanation(context, decision_output)

    assert "Adjudication Decision: APPROVE" in explanation
    assert "RULE-P6-CLEAN-PASS" in explanation
    assert "Member Identity & KYC Verification" in explanation
    assert "MEM-10023" in explanation
    assert "Claims Financial & Line-Item Validation" in explanation
    assert "$4,095.00" in explanation
    assert "99213" in explanation
    assert "Policy Coverage & Benefit Grounding" in explanation
    assert "Fraud Risk & Anomaly Assessment" in explanation
    assert "Operator Guidance & Recommended Action" in explanation
    assert "Straight-Through Processing" in explanation


def test_deterministic_explanation_escalate():
    """Verifies that an escalated case highlights escalation sub-reason and operator action."""
    context, decision_output = _build_sample_context()
    decision_output.decision = BusinessDecision.ESCALATE
    decision_output.escalation_sub_reason = EscalationSubReason.AMBIGUITY
    decision_output.rule_id_matched = "RULE-P3-AMBIGUITY"
    decision_output.justification = "Ambiguity Escalation (P3): Line math mismatch."

    explanation = generate_deterministic_explanation(context, decision_output)

    assert "Adjudication Decision: ESCALATE" in explanation
    assert "RULE-P3-AMBIGUITY" in explanation
    assert "AMBIGUITY" in explanation
    assert "Human Review Required" in explanation


def test_generate_decision_explanation_llm_fallback():
    """Verifies graceful fallback to deterministic generator when LLM client is offline or fails."""
    context, decision_output = _build_sample_context()

    # Mock LLM client that throws an exception
    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.generate_json.side_effect = RuntimeError("Groq API Timeout")

    explanation = generate_decision_explanation(context, decision_output, llm_client=mock_client)

    # Must not raise; must return deterministic explanation
    assert "Adjudication Decision: APPROVE" in explanation
    assert "RULE-P6-CLEAN-PASS" in explanation
    assert "$4,095.00" in explanation


def test_generate_decision_explanation_llm_success():
    """Verifies that when Groq returns a rich JSON explanation, it is adopted."""
    context, decision_output = _build_sample_context()

    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.generate_json.return_value = {
        "detailed_explanation": "### Clinical Rationale\nThis claim was fully approved with zero copay based on verified Gold plan."
    }

    explanation = generate_decision_explanation(context, decision_output, llm_client=mock_client)

    assert "### Clinical Rationale" in explanation
    assert "Gold plan" in explanation


def test_case_detail_schema_has_detailed_explanation():
    """Verifies CaseDetailResponse Pydantic schema includes detailed_explanation field."""
    now = datetime.now(UTC)
    resp = CaseDetailResponse(
        case_id="CASE-123",
        workflow_state="COMPLETED",
        rule_id_matched="RULE-P6-CLEAN-PASS",
        justification="Clean pass",
        detailed_explanation="### Executive Summary\nClaim clean pass.",
        created_at=now,
        updated_at=now,
    )
    assert resp.detailed_explanation == "### Executive Summary\nClaim clean pass."
    assert resp.justification == "Clean pass"


def test_clinical_alignment_explanation_includes_diagnosis_procedure_misalignment():
    """Verifies that diagnosis-procedure clinical misalignment is explicitly surfaced in Section 6 of explanation."""
    context, decision_output = _build_sample_context()

    from medishield.domain.clinical_align import DeterministicClinicalAlignmentEngine
    from medishield.domain.contracts import ClinicalIndicationStatus

    line_ev = DeterministicClinicalAlignmentEngine.evaluate_line_item(
        line_number=1,
        service_code="27447",
        billed_units=1.0,
        billed_amount=15400.0,
        diagnosis_codes=["S52", "M54.5"],
    )
    assert line_ev.indication_status == ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION
    assert line_ev.discrepancy_description is not None
    assert "Diagnosis-procedure clinical misalignment" in line_ev.discrepancy_description
    assert "CPT 27447 (Total Knee Arthroplasty (TKA))" in line_ev.discrepancy_description
    assert "S52, M54.5" in line_ev.discrepancy_description
    assert "Knee Joint" in line_ev.discrepancy_description

    clin_output = DeterministicClinicalAlignmentEngine.align_claim(
        expected_line_numbers=[1],
        line_assessments=[line_ev],
    )
    context.clinical_output = clin_output

    explanation = generate_deterministic_explanation(context, decision_output)

    assert "#### 6. Clinical Alignment & Medical Necessity" in explanation
    assert "Diagnosis-procedure clinical misalignment" in explanation
    assert "CPT 27447 (Total Knee Arthroplasty (TKA))" in explanation
    assert "S52, M54.5" in explanation
    assert "Knee Joint" in explanation


def test_deterministic_explanation_rich_clinical_alignment_details():
    """Verifies that deterministic generator renders granular line-item evidence and bed-day stay reconciliation."""
    from datetime import date

    from medishield.domain.contracts import (
        BedDayBillingRuleContract,
        BedDayReconciliation,
        ClinicalAlignmentOutput,
        ClinicalIndicationStatus,
        CriterionEvaluationOutcome,
        FacilityBillingConvention,
        GuidelineCitation,
        LineClinicalEvidence,
        MandatoryGuidelineCriterion,
        MedicalNecessityStatus,
        PerformanceEvidenceStatus,
        PriorAuthRequirementStatus,
        PriorAuthVerificationStatus,
        RepeatServiceStatus,
        ServiceModality,
    )
    from medishield.domain.provenance import FieldProvenance

    context, decision_output = _build_sample_context()

    guideline = GuidelineCitation(
        guideline_id="LCD-44970-01",
        guideline_version="2026.1",
        guideline_title="Laparoscopic Appendectomy",
        jurisdiction="NATIONAL",
        payer_product="COMMERCIAL",
        effective_date=date(2025, 1, 1),
        criteria_evaluations=[
            MandatoryGuidelineCriterion(
                criterion_id="CRIT-44970-01",
                description="Clinical, laboratory, or imaging evidence of acute or suspected appendicitis",
                is_mandatory=True,
                evaluation_outcome=CriterionEvaluationOutcome.SATISFIED,
                supporting_spans=[
                    FieldProvenance(
                        document_id="DOC-DS-01",
                        page_number=1,
                        text_content="Laparoscopic appendectomy performed",
                        evidence_quality_score=0.95,
                    )
                ],
            )
        ],
    )

    line1 = LineClinicalEvidence(
        line_number=1,
        service_code="44970",
        modality=ServiceModality.SURGICAL_PROCEDURE,
        billed_units=1.0,
        billed_amount=12500.0,
        service_date=date(2026, 3, 11),
        performance_status=PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
        indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
        necessity_status=MedicalNecessityStatus.MET,
        prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
        prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
        repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
        line_verdict=AgentVerdict.PASS,
        primary_icd10_codes=["K35.80"],
        secondary_icd10_codes=["R10.31"],
        guideline_citation=guideline,
        supporting_spans=[
            FieldProvenance(
                document_id="DOC-DS-01",
                page_number=1,
                text_content="Laparoscopic appendectomy performed",
                evidence_quality_score=0.95,
            )
        ],
        clinical_finding_summary="Operative report confirms laparoscopic appendectomy; pathology confirms acute suppurative appendicitis.",
    )

    billing_rule = BedDayBillingRuleContract(
        rule_id="BR-ACUTE-INP-01",
        rule_version="1.0",
        facility_type="INPATIENT_ACUTE",
        claim_type="INPATIENT",
        effective_date=date(2025, 1, 1),
        is_approved=True,
        convention=FacilityBillingConvention.MIDNIGHT_CENSUS,
    )

    bdr = BedDayReconciliation(
        billing_rule=billing_rule,
        claim_facility_type="INPATIENT_ACUTE",
        claim_type="INPATIENT",
        admission_date=date(2026, 3, 10),
        discharge_date=date(2026, 3, 12),
        calendar_days_stayed=2,
        expected_billable_units_min=2.0,
        expected_billable_units_max=2.0,
        billed_bed_days=2.0,
        is_reconciled=True,
        discrepancy_units=0.0,
        uncertainty_flag=False,
        reconciliation_verdict=AgentVerdict.PASS,
        remarks="Admission on 2026-03-10 through discharge on 2026-03-12 satisfies 2 midnights under facility contract.",
    )

    clin_output = ClinicalAlignmentOutput(
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        expected_claim_line_numbers=[1],
        total_lines_assessed=1,
        line_assessments=[line1],
        bed_day_reconciliation=bdr,
        lines_fully_supported_count=1,
        assessment_sufficient=True,
        clinical_summary="All surgical and inpatient services clinically justified.",
        latency_ms=120,
    )
    context.clinical_output = clin_output

    explanation = generate_deterministic_explanation(context, decision_output)

    # Verify granular line item reporting
    assert "Itemized Clinical Indication & Evidence Grounding:" in explanation
    assert "Line 1 (CPT/Code `44970`" in explanation
    assert "Primary ICD: K35.80" in explanation
    assert "Secondary ICD: R10.31" in explanation
    assert "Performance: `DIRECTLY_DOCUMENTED_PERFORMANCE`" in explanation
    assert "Indication: `EXPLICITLY_INDICATED`" in explanation
    assert "Necessity: `MET`" in explanation
    assert "Laparoscopic Appendectomy (LCD-44970-01)" in explanation
    assert "Operative report confirms laparoscopic appendectomy" in explanation

    # Verify bed-day reconciliation reporting
    assert "Inpatient Hospital Stay & Bed-Day Reconciliation:" in explanation
    assert "2.0 billed bed day(s)" in explanation
    assert "Admit: 2026-03-10" in explanation
    assert "Discharge: 2026-03-12" in explanation
    assert "MIDNIGHT_CENSUS" in explanation
    assert "satisfies 2 midnights under facility contract" in explanation


def test_generate_decision_explanation_llm_payload_contains_rich_clinical_details():
    """Verifies that Groq LLM receives comprehensive clinical facts in payload and specific clinical directives."""
    import json
    from datetime import date

    from medishield.domain.contracts import (
        BedDayBillingRuleContract,
        BedDayReconciliation,
        ClinicalAlignmentOutput,
        ClinicalIndicationStatus,
        CriterionEvaluationOutcome,
        FacilityBillingConvention,
        GuidelineCitation,
        LineClinicalEvidence,
        MandatoryGuidelineCriterion,
        MedicalNecessityStatus,
        PerformanceEvidenceStatus,
        PriorAuthRequirementStatus,
        PriorAuthVerificationStatus,
        RepeatServiceStatus,
        ServiceModality,
    )
    from medishield.domain.provenance import FieldProvenance

    context, decision_output = _build_sample_context()

    guideline = GuidelineCitation(
        guideline_id="LCD-44970-01",
        guideline_version="2026.1",
        guideline_title="Laparoscopic Appendectomy",
        jurisdiction="NATIONAL",
        payer_product="COMMERCIAL",
        effective_date=date(2025, 1, 1),
        criteria_evaluations=[
            MandatoryGuidelineCriterion(
                criterion_id="CRIT-44970-01",
                description="Clinical, laboratory, or imaging evidence of acute or suspected appendicitis",
                is_mandatory=True,
                evaluation_outcome=CriterionEvaluationOutcome.SATISFIED,
                supporting_spans=[
                    FieldProvenance(
                        document_id="DOC-DS-01",
                        page_number=1,
                        text_content="Laparoscopic appendectomy performed",
                        evidence_quality_score=0.95,
                    )
                ],
            )
        ],
    )

    line1 = LineClinicalEvidence(
        line_number=1,
        service_code="44970",
        modality=ServiceModality.SURGICAL_PROCEDURE,
        billed_units=1.0,
        billed_amount=12500.0,
        service_date=date(2026, 3, 11),
        performance_status=PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
        indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
        necessity_status=MedicalNecessityStatus.MET,
        prior_auth_requirement=PriorAuthRequirementStatus.NOT_REQUIRED,
        prior_auth_verification=PriorAuthVerificationStatus.NOT_APPLICABLE,
        repeat_service_status=RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
        line_verdict=AgentVerdict.PASS,
        primary_icd10_codes=["K35.80"],
        secondary_icd10_codes=["R10.31"],
        guideline_citation=guideline,
        supporting_spans=[
            FieldProvenance(
                document_id="DOC-DS-01",
                page_number=1,
                text_content="Laparoscopic appendectomy performed",
                evidence_quality_score=0.95,
            )
        ],
        clinical_finding_summary="Operative report confirms laparoscopic appendectomy; pathology confirms acute suppurative appendicitis.",
    )

    billing_rule = BedDayBillingRuleContract(
        rule_id="BR-ACUTE-INP-01",
        rule_version="1.0",
        facility_type="INPATIENT_ACUTE",
        claim_type="INPATIENT",
        effective_date=date(2025, 1, 1),
        is_approved=True,
        convention=FacilityBillingConvention.MIDNIGHT_CENSUS,
    )

    bdr = BedDayReconciliation(
        billing_rule=billing_rule,
        claim_facility_type="INPATIENT_ACUTE",
        claim_type="INPATIENT",
        admission_date=date(2026, 3, 10),
        discharge_date=date(2026, 3, 12),
        calendar_days_stayed=2,
        expected_billable_units_min=2.0,
        expected_billable_units_max=2.0,
        billed_bed_days=2.0,
        is_reconciled=True,
        discrepancy_units=0.0,
        uncertainty_flag=False,
        reconciliation_verdict=AgentVerdict.PASS,
        remarks="Admission on 2026-03-10 through discharge on 2026-03-12 satisfies 2 midnights under facility contract.",
    )

    clin_output = ClinicalAlignmentOutput(
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        expected_claim_line_numbers=[1],
        total_lines_assessed=1,
        line_assessments=[line1],
        bed_day_reconciliation=bdr,
        lines_fully_supported_count=1,
        assessment_sufficient=True,
        clinical_summary="All surgical and inpatient services clinically justified.",
        latency_ms=120,
    )
    context.clinical_output = clin_output

    captured_prompt = {}

    def mock_generate_json(prompt, system_prompt, **kwargs):
        captured_prompt["prompt"] = prompt
        captured_prompt["system_prompt"] = system_prompt
        return {
            "detailed_explanation": (
                "### ✅ Executive Decision Summary: APPROVE\n\n"
                "#### 6. Clinical Alignment & Medical Necessity\n"
                "- **Medical Indication:** CPT 44970 is clinically indicated by acute appendicitis (K35.80).\n"
                "- **Evidence Grounding:** Operative report validates laparoscopic technique.\n"
                "- **Guideline Compliance:** ACS Acute Appendicitis guideline met.\n"
                "- **Inpatient Bed Days:** 2.0 bed days reconciled under midnight census convention.\n"
            )
        }

    mock_client = MagicMock()
    mock_client.is_available.return_value = True
    mock_client.generate_json.side_effect = mock_generate_json

    result = generate_decision_explanation(context, decision_output, llm_client=mock_client)

    # Ensure result adopted
    assert "CPT 44970 is clinically indicated by acute appendicitis" in result

    # Check payload structure
    prompt_str = captured_prompt["prompt"]
    json_start = prompt_str.find("```json\n") + len("```json\n")
    json_end = prompt_str.find("\n```\n\nGenerate")
    payload = json.loads(prompt_str[json_start:json_end])

    clin_payload = payload["clinical_alignment"]
    assert clin_payload["verdict"] == "PASS"
    assert clin_payload["total_lines_assessed"] == 1
    assert len(clin_payload["line_assessments"]) == 1

    la_p = clin_payload["line_assessments"][0]
    assert la_p["service_code"] == "44970"
    assert la_p["primary_icd10_codes"] == ["K35.80"]
    assert la_p["secondary_icd10_codes"] == ["R10.31"]
    assert la_p["guideline_citation"]["guideline_id"] == "LCD-44970-01"
    assert la_p["guideline_citation"]["guideline_title"] == "Laparoscopic Appendectomy"
    assert la_p["clinical_finding_summary"] == "Operative report confirms laparoscopic appendectomy; pathology confirms acute suppurative appendicitis."

    # Check bed-day payload
    bdr_p = clin_payload["bed_day_reconciliation"]
    assert bdr_p["billed_bed_days"] == 2.0
    assert bdr_p["admission_date"] == "2026-03-10"
    assert bdr_p["discharge_date"] == "2026-03-12"
    assert bdr_p["billing_convention"] == "MIDNIGHT_CENSUS"
    assert bdr_p["is_reconciled"] is True

    # Check system prompt directives
    sys_prompt = captured_prompt["system_prompt"]
    assert "Medical Indication Rationale:" in sys_prompt
    assert "Performance & Evidence Grounding:" in sys_prompt
    assert "Clinical Practice Guidelines:" in sys_prompt
    assert "Inpatient Bed-Day & Stay Reconciliation:" in sys_prompt

