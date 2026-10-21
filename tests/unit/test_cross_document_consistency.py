"""Unit tests for Cross-Document Consistency Engine, Date Reconciliation, and Specific Escalation Reasoning."""

from datetime import date

from medishield.db.enums import AgentVerdict, BusinessDecision, DocumentType, EscalationSubReason
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    ClinicalAlignmentOutput,
    FraudAgentOutput,
    KYCAgentOutput,
    PolicyCoverageOutput,
    ProvenanceValue,
    build_clinical_alignment_output,
)
from medishield.domain.decision import (
    RULE_P3_AMBIGUITY,
    RULE_P6_CLEAN_PASS,
    DecisionInputContext,
    DeterministicDecisionEngine,
)
from medishield.domain.faithfulness import (
    evaluate_case_consistency,
    parse_medical_date,
)
from medishield.domain.fraud import (
    calculate_service_date_inconsistency_feature,
)
from medishield.domain.provenance import FieldProvenance, ProvenanceType
from tests.unit.test_clinical_alignment import make_clean_line_evidence


def _dummy_provenance(doc_id: str = "DOC-TEST") -> FieldProvenance:
    return FieldProvenance(
        document_id=doc_id,
        page_number=1,
        text_content="test",
        provenance_type=ProvenanceType.SPAN,
        evidence_quality_score=0.95,
        model_confidence=0.95,
    )


def _base_kyc() -> KYCAgentOutput:
    prov = _dummy_provenance("DOC-ID")
    return KYCAgentOutput(
        agent_name="kyc_agent",
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        member_id=ProvenanceValue(value="MEM-12345", confidence=0.95, provenance=prov),
        first_name=ProvenanceValue(value="Alice", confidence=0.95, provenance=prov),
        last_name=ProvenanceValue(value="Smith", confidence=0.95, provenance=prov),
        dob=ProvenanceValue(value="1985-05-20", confidence=0.95, provenance=prov),
        member_db_matched=True,
        tamper_detected=False,
        tamper_score=0.0,
        tamper_signals=[],
        flags=[],
        latency_ms=10,
    )


def _base_claims(
    service_date: str = "2025-09-15",
    claim_amount: float = 350.0,
    npi: str = "1234567890",
) -> ClaimsAgentOutput:
    prov = _dummy_provenance("DOC-CLAIM")
    from medishield.domain.contracts import ServiceLine
    lines = [
        ServiceLine(
            line_number=1,
            hcpcs=ProvenanceValue(value="99213", confidence=0.95, provenance=prov),
            amount=ProvenanceValue(value=claim_amount, confidence=0.95, provenance=prov),
            units=ProvenanceValue(value=1.0, confidence=0.95, provenance=prov),
            service_date=ProvenanceValue(value=service_date, confidence=0.95, provenance=prov),
        )
    ]
    return ClaimsAgentOutput(
        agent_name="claims_agent",
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        claim_amount=ProvenanceValue(value=claim_amount, confidence=0.95, provenance=prov),
        service_date=ProvenanceValue(value=service_date, confidence=0.95, provenance=prov),
        provider_npi=ProvenanceValue(value=npi, confidence=0.95, provenance=prov),
        service_lines=lines,
        cpt_codes=[ProvenanceValue(value="99213", confidence=0.95, provenance=prov)],
        icd10_codes=[ProvenanceValue(value="J02.9", confidence=0.95, provenance=prov)],
        schema_valid=True,
        validation_errors=[],
        line_items_sum_valid=True,
        calculated_lines_sum=claim_amount,
        latency_ms=10,
    )


def _base_policy() -> PolicyCoverageOutput:
    return PolicyCoverageOutput(
        agent_name="policy_agent",
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        covered=True,
        coverage_percentage=100.0,
        policy_id="POL-STD-001",
        applicable_service_codes=["99213"],
        identified_exclusions=[],
        exclusion_clause_verified=False,
        effective_on_service_date=True,
        reasoning_summary="Service covered under standard outpatient benefit.",
        latency_ms=10,
    )


def _base_clinical() -> ClinicalAlignmentOutput:
    clin_evidence = make_clean_line_evidence(
        line_number=1,
        service_code="99213",
        billed_units=1.0,
        billed_amount=350.00,
    )
    return build_clinical_alignment_output(
        expected_line_numbers=[1],
        line_assessments=[clin_evidence],
        clinical_documents_present=True,
    )


def _base_fraud() -> FraudAgentOutput:
    from medishield.domain.contracts import RiskLevel
    return FraudAgentOutput(
        agent_name="fraud_agent",
        verdict=AgentVerdict.PASS,
        confidence=1.0,
        fraud_score=0.0,
        risk_level=RiskLevel.LOW,
        anomalies=[],
        duplicate_claim_detected=False,
        latency_ms=5,
    )


# ── 1. Multi-Format Date Parsing Tests ────────────────────────────────────────

def test_parse_medical_date_formats() -> None:
    assert parse_medical_date("2025-09-15") == date(2025, 9, 15)
    assert parse_medical_date("09/15/2025") == date(2025, 9, 15)
    assert parse_medical_date("15/09/2025") == date(2025, 9, 15)
    assert parse_medical_date("September 15, 2025") == date(2025, 9, 15)
    assert parse_medical_date("15 September 2025") == date(2025, 9, 15)
    assert parse_medical_date("Sep 15, 2025") == date(2025, 9, 15)
    assert parse_medical_date("15-Sep-2025") == date(2025, 9, 15)
    assert parse_medical_date("invalid-date") is None
    assert parse_medical_date(None) is None


# ── 2. Multi-Format Date Reconciliation Tests ─────────────────────────────────

def test_multi_format_date_reconciliation_slash_vs_iso() -> None:
    """Claim date in MM/DD/YYYY reconciles with Discharge Summary in YYYY-MM-DD."""
    kyc = _base_kyc()
    claims = _base_claims(service_date="09/15/2025")
    policy = _base_policy()
    clinical = _base_clinical()

    doc_texts = {
        "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDATE OF SERVICE: 09/15/2025\nNPI: 1234567890\nPATIENT SIGNATURE: Signed on file\nPHYSICIAN SIGNATURE: Dr. Sarah Jenkins, MD",
        "DISCHARGE_SUMMARY": "HOSPITAL DISCHARGE SUMMARY\nPATIENT: Alice Smith\nADMISSION DATE: 2025-09-14\nDISCHARGE DATE: 2025-09-18\nATTENDING PHYSICIAN SIGNATURE: Dr. Sarah Jenkins, MD",
    }

    report = evaluate_case_consistency(
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        document_texts=doc_texts,
    )

    assert report.dates_consistent is True
    assert report.date_reconciliation_summary is not None
    assert "2025-09-15" in report.date_reconciliation_summary
    assert report.is_consistent is True


def test_date_conflict_incompatible() -> None:
    """Service date far outside hospital stay generates specific date conflict."""
    kyc = _base_kyc()
    claims = _base_claims(service_date="2025-01-10")
    policy = _base_policy()
    clinical = _base_clinical()

    doc_texts = {
        "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDATE OF SERVICE: 2025-01-10\nNPI: 1234567890\nPATIENT SIGNATURE: Yes\nPHYSICIAN SIGNATURE: Dr. Sarah Jenkins, MD",
        "DISCHARGE_SUMMARY": "DISCHARGE SUMMARY\nPATIENT: Alice Smith\nADMISSION DATE: 2025-09-14\nDISCHARGE DATE: 2025-09-18\nPHYSICIAN: Dr. Sarah Jenkins, MD",
    }

    report = evaluate_case_consistency(
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        document_texts=doc_texts,
    )

    assert report.dates_consistent is False
    assert any("Date conflict detected" in c for c in report.conflicts_detected)
    assert any("Date conflict" in r for r in report.specific_escalation_reasons)


# ── 3. Fraud Engine Stale Date Prevention ─────────────────────────────────────

def test_fraud_engine_no_stale_penalty_when_dates_reconciled() -> None:
    """When claim service date aligns with discharge summary, F-05 feature is not flagged as stale."""
    feat = calculate_service_date_inconsistency_feature(
        service_date=date(2025, 9, 15),
        submission_date=None,  # running today in 2026
        discharge_date=date(2025, 9, 18),
        dates_reconciled=True,
    )
    assert feat.detected is False
    assert "reconciles" in feat.description.lower()


# ── 4. Doctor Attestation Signature Verification ──────────────────────────────

def test_doctor_signature_missing_escalates() -> None:
    """Missing doctor signature on clinical records flags specific reason and blocks clean pass."""
    kyc = _base_kyc()
    claims = _base_claims()
    policy = _base_policy()
    clinical = _base_clinical()

    doc_texts = {
        "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nNPI: 1234567890\nPATIENT SIGNATURE: Signed on file",
        "DISCHARGE_SUMMARY": "HOSPITAL DISCHARGE SUMMARY\nPATIENT: Alice Smith\nATTENDING PHYSICIAN: [UNSIGNED / PENDING ATTESTATION]",
    }

    report = evaluate_case_consistency(
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        document_texts=doc_texts,
    )

    assert report.doctor_signature_verified is False
    assert any("physician signature" in r.lower() for r in report.specific_escalation_reasons)

    # Decision Engine Evaluation: must escalate under Rule P3
    ctx = DecisionInputContext(
        case_id="CASE-DOC-SIG",
        execution_id="EXEC-DOC-SIG",
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        fraud_output=_base_fraud(),
        consistency_report=report,
        present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
    )
    decision = DeterministicDecisionEngine.adjudicate(ctx)
    assert decision.decision == BusinessDecision.ESCALATE
    assert decision.rule_id_matched == RULE_P3_AMBIGUITY
    assert decision.escalation_sub_reason in (EscalationSubReason.AMBIGUITY, EscalationSubReason.HUMAN_REVIEW)
    assert "physician signature" in decision.justification.lower()


# ── 5. Patient Signature / Assignment Verification ────────────────────────────

def test_patient_signature_missing_escalates() -> None:
    """Missing patient signature on claim form flags specific reason and escalates."""
    kyc = _base_kyc()
    claims = _base_claims()
    policy = _base_policy()
    clinical = _base_clinical()

    doc_texts = {
        "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nNPI: 1234567890\nPATIENT SIGNATURE: None\nASSIGNMENT OF BENEFITS: No",
        "DISCHARGE_SUMMARY": "DISCHARGE SUMMARY\nPATIENT: Alice Smith\nATTENDING PHYSICIAN SIGNATURE: Dr. Sarah Jenkins, MD",
    }

    report = evaluate_case_consistency(
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        document_texts=doc_texts,
    )

    assert report.patient_signature_verified is False
    assert any("patient signature" in r.lower() for r in report.specific_escalation_reasons)

    ctx = DecisionInputContext(
        case_id="CASE-PAT-SIG",
        execution_id="EXEC-PAT-SIG",
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        fraud_output=_base_fraud(),
        consistency_report=report,
        present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
    )
    decision = DeterministicDecisionEngine.adjudicate(ctx)
    assert decision.decision == BusinessDecision.ESCALATE
    assert decision.rule_id_matched == RULE_P3_AMBIGUITY
    assert decision.escalation_sub_reason in (EscalationSubReason.AMBIGUITY, EscalationSubReason.HUMAN_REVIEW)
    assert "patient signature" in decision.justification.lower()


# ── 6. Provider NPI Verification ──────────────────────────────────────────────

def test_missing_or_invalid_npi_escalates() -> None:
    """Missing or dummy NPI (0000000000) flags specific reason and escalates."""
    kyc = _base_kyc()
    claims = _base_claims(npi="0000000000")
    policy = _base_policy()
    clinical = _base_clinical()

    doc_texts = {
        "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nNPI: 0000000000\nPATIENT SIGNATURE: Signed\nPHYSICIAN SIGNATURE: Dr. Sarah Jenkins, MD",
        "DISCHARGE_SUMMARY": "DISCHARGE SUMMARY\nPATIENT: Alice Smith\nATTENDING PHYSICIAN: Dr. Sarah Jenkins, MD",
    }

    report = evaluate_case_consistency(
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        document_texts=doc_texts,
    )

    assert report.npi_verified is False
    assert any("npi" in r.lower() for r in report.specific_escalation_reasons)


# ── 7. Mandatory Prior Authorization for Major Procedures ─────────────────────

def test_missing_prior_authorization_major_procedure() -> None:
    """Major procedure (CPT 33533 CABG) requiring prior authorization under policy terms."""
    kyc = _base_kyc()
    claims = _base_claims()
    # Change billed code to 33533
    prov = _dummy_provenance()
    from medishield.domain.contracts import ServiceLine
    claims.cpt_codes = [ProvenanceValue(value="33533", confidence=0.95, provenance=prov)]
    claims.service_lines = [
        ServiceLine(
            line_number=1,
            hcpcs=ProvenanceValue(value="33533", confidence=0.95, provenance=prov),
            amount=ProvenanceValue(value=350.0, confidence=0.95, provenance=prov),
            units=ProvenanceValue(value=1.0, confidence=0.95, provenance=prov),
            service_date=ProvenanceValue(value="2025-09-15", confidence=0.95, provenance=prov),
        )
    ]
    policy = _base_policy()
    policy.applicable_service_codes = ["33533"]
    clinical = _base_clinical()

    doc_texts = {
        "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nNPI: 1234567890\nCPT: 33533\nPATIENT SIGNATURE: Signed\nPHYSICIAN SIGNATURE: Dr. Sarah Jenkins, MD",
        "DISCHARGE_SUMMARY": "DISCHARGE SUMMARY\nPATIENT: Alice Smith\nCABG performed\nATTENDING PHYSICIAN: Dr. Sarah Jenkins, MD",
    }

    report = evaluate_case_consistency(
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        document_texts=doc_texts,
    )

    assert report.prior_authorization_verified is False
    assert any("prior authorization" in r.lower() for r in report.specific_escalation_reasons)


# ── 8. Common Fields / Patient Name Consistency ───────────────────────────────

def test_patient_name_mismatch_across_documents() -> None:
    """Patient name mismatch (Alice Smith vs John Doe) triggers specific identity conflict."""
    kyc = _base_kyc()
    claims = _base_claims()
    policy = _base_policy()
    clinical = _base_clinical()

    doc_texts = {
        "ID_DOCUMENT": "DRIVER LICENSE\nNAME: Alice Smith\nDOB: 1985-05-20",
        "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT NAME: John Doe\nNPI: 1234567890\nPATIENT SIGNATURE: Signed\nPHYSICIAN SIGNATURE: Dr. Jenkins",
    }

    report = evaluate_case_consistency(
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        document_texts=doc_texts,
    )

    assert report.identity_consistent is False
    assert any("name discrepancy" in r.lower() for r in report.specific_escalation_reasons)


# ── 9. Clean Pass Verification ────────────────────────────────────────────────

def test_all_consistent_passes_rule_p6() -> None:
    """When signatures, NPI, prior auth, dates, and amounts are all verified, clean straight-through passes P6."""
    kyc = _base_kyc()
    claims = _base_claims()
    policy = _base_policy()
    clinical = _base_clinical()

    doc_texts = {
        "ID_DOCUMENT": "DRIVER LICENSE\nNAME: Alice Smith\nDOB: 1985-05-20",
        "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT NAME: Alice Smith\nDATE OF SERVICE: 2025-09-15\nNPI: 1234567890\nPATIENT SIGNATURE: Signed\nPHYSICIAN SIGNATURE: Dr. Sarah Jenkins, MD",
        "DISCHARGE_SUMMARY": "DISCHARGE SUMMARY\nPATIENT NAME: Alice Smith\nADMISSION DATE: 2025-09-14\nDISCHARGE DATE: 2025-09-18\nATTENDING PHYSICIAN: Dr. Sarah Jenkins, MD",
    }

    report = evaluate_case_consistency(
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        document_texts=doc_texts,
    )

    assert report.is_consistent is True
    assert report.doctor_signature_verified is True
    assert report.patient_signature_verified is True
    assert report.npi_verified is True
    assert report.prior_authorization_verified is True
    assert report.dates_consistent is True
    assert len(report.conflicts_detected) == 0

    ctx = DecisionInputContext(
        case_id="CASE-CLEAN-PASS",
        execution_id="EXEC-CLEAN-PASS",
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        fraud_output=_base_fraud(),
        consistency_report=report,
        present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
    )
    decision = DeterministicDecisionEngine.adjudicate(ctx)
    assert decision.decision == BusinessDecision.APPROVE
    assert decision.rule_id_matched == RULE_P6_CLEAN_PASS


# ── 10. Date of Birth Filtering Tests ─────────────────────────────────────────

def test_dob_not_extracted_as_admission_or_discharge_date() -> None:
    """Discharge summary containing patient DOB (e.g. 1958-11-08) does NOT pick DOB as encounter date."""
    kyc = _base_kyc()
    prov_dob = ProvenanceValue(value="1958-11-08", confidence=0.95, provenance=_dummy_provenance("DOC-KYC"))
    kyc.dob = prov_dob
    kyc.date_of_birth = prov_dob
    claims = _base_claims(service_date="2025-09-15")
    policy = _base_policy()
    clinical = _base_clinical()

    doc_texts = {
        "ID_DOCUMENT": "DRIVER LICENSE\nNAME: Alice Smith\nDOB: 11/08/1958",
        "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDATE OF SERVICE: 2025-09-15\nNPI: 1234567890\nPATIENT SIGNATURE: Signed\nPHYSICIAN SIGNATURE: Dr. Sarah Jenkins, MD",
        "DISCHARGE_SUMMARY": (
            "HOSPITAL DISCHARGE SUMMARY\n"
            "PATIENT: Alice Smith\n"
            "DATE OF BIRTH: 11/08/1958\n"
            "ADMISSION DATE: 09/14/2025\n"
            "DISCHARGE DATE: 09/18/2025\n"
            "ATTENDING PHYSICIAN: Dr. Sarah Jenkins, MD\n"
        ),
    }

    report = evaluate_case_consistency(
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        document_texts=doc_texts,
    )

    # Must NOT fail with a 24,000-day date conflict
    assert report.dates_consistent is True
    assert "2025-09-15" in (report.date_reconciliation_summary or "")
    assert not any("1958" in c for c in report.conflicts_detected)


# ── 11. Policy Coverage Window Date Tests ─────────────────────────────────────

def test_claim_service_date_within_allowable_policy_window() -> None:
    """Claim service date up to 30 days post-discharge reconciles cleanly under policy window."""
    kyc = _base_kyc()
    claims = _base_claims(service_date="2025-09-28")  # 10 days post-discharge (09/18)
    policy = _base_policy()
    clinical = _base_clinical()

    doc_texts = {
        "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDATE OF SERVICE: 2025-09-28\nNPI: 1234567890\nPATIENT SIGNATURE: Signed\nPHYSICIAN SIGNATURE: Dr. Sarah Jenkins, MD",
        "DISCHARGE_SUMMARY": "DISCHARGE SUMMARY\nPATIENT: Alice Smith\nADMISSION DATE: 2025-09-14\nDISCHARGE DATE: 2025-09-18\nATTENDING PHYSICIAN: Dr. Sarah Jenkins, MD",
    }

    report = evaluate_case_consistency(
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        document_texts=doc_texts,
    )

    assert report.dates_consistent is True
    assert "allowable policy coverage window" in (report.date_reconciliation_summary or "")
    assert not any("Date conflict" in c for c in report.conflicts_detected)


# ── 12. Uncoded Service Line & Room & Board Escalation Tests ──────────────────

def test_uncoded_service_line_missing_cpt_escalates() -> None:
    """Service line with missing CPT code / non-procedure rate (e.g. 1250.0) blocks P6 and escalates."""
    kyc = _base_kyc()
    claims = _base_claims(claim_amount=5000.0)

    prov = _dummy_provenance("DOC-CLAIM")
    from medishield.domain.contracts import ServiceLine

    # Line 1: valid CPT
    # Line 2: Room and Board with 1250.0 rate instead of CPT code
    claims.service_lines = [
        ServiceLine(
            line_number=1,
            hcpcs=ProvenanceValue(value="99213", confidence=0.95, provenance=prov),
            amount=ProvenanceValue(value=1250.0, confidence=0.95, provenance=prov),
            units=ProvenanceValue(value=1.0, confidence=0.95, provenance=prov),
            service_date=ProvenanceValue(value="2025-09-15", confidence=0.95, provenance=prov),
        ),
        ServiceLine(
            line_number=2,
            hcpcs=ProvenanceValue(value="1250.0", confidence=0.95, provenance=prov),  # rate token in CPT slot
            description=ProvenanceValue(value="Room and Board - Semi-Private", confidence=0.95, provenance=prov),
            amount=ProvenanceValue(value=3750.0, confidence=0.95, provenance=prov),
            units=ProvenanceValue(value=3.0, confidence=0.95, provenance=prov),
            service_date=ProvenanceValue(value="2025-09-15", confidence=0.95, provenance=prov),
        ),
    ]

    policy = _base_policy()
    clinical = _base_clinical()

    doc_texts = {
        "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDATE OF SERVICE: 2025-09-15\nNPI: 1234567890\nPATIENT SIGNATURE: Signed\nPHYSICIAN SIGNATURE: Dr. Sarah Jenkins, MD",
        "DISCHARGE_SUMMARY": "DISCHARGE SUMMARY\nPATIENT: Alice Smith\nADMISSION DATE: 2025-09-14\nDISCHARGE DATE: 2025-09-18\nATTENDING PHYSICIAN: Dr. Sarah Jenkins, MD",
    }

    report = evaluate_case_consistency(
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        document_texts=doc_texts,
    )

    assert report.procedure_codes_verified is False
    assert report.unvalidated_charges_total == 3750.0
    assert report.is_consistent is False
    assert any("Unvalidated billed charge" in r for r in report.specific_escalation_reasons)
    assert any("1250.0" in r for r in report.specific_escalation_reasons)

    # Decision Engine Evaluation: must escalate and block clean pass P6
    ctx = DecisionInputContext(
        case_id="CASE-UNCODED-CHARGES",
        execution_id="EXEC-UNCODED",
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        fraud_output=_base_fraud(),
        consistency_report=report,
        present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
    )
    decision = DeterministicDecisionEngine.adjudicate(ctx)
    assert decision.decision == BusinessDecision.ESCALATE
    assert decision.rule_id_matched == RULE_P3_AMBIGUITY
    assert "unvalidated billed charge" in decision.justification.lower()
    assert "$3,750.00" in decision.justification


# ── 13. Diagnosis-to-Procedure Clinical Alignment Tests ───────────────────────

def test_clinical_misalignment_depression_with_appendectomy_escalates() -> None:
    """Patient diagnosed with Major Depressive Episode (F32.1) billed for Appendectomy (44970) escalates."""
    kyc = _base_kyc()
    claims = _base_claims()

    prov = _dummy_provenance("DOC-CLAIM")
    from medishield.domain.clinical_align import DeterministicClinicalAlignmentEngine
    from medishield.domain.contracts import ServiceLine

    # Patient has diagnosis F32.1 (Major Depressive Episode)
    claims.icd10_codes = [ProvenanceValue(value="F32.1", confidence=0.95, provenance=prov)]
    # Billed procedure is 44970 (Appendectomy)
    claims.cpt_codes = [ProvenanceValue(value="44970", confidence=0.95, provenance=prov)]
    claims.service_lines = [
        ServiceLine(
            line_number=1,
            hcpcs=ProvenanceValue(value="44970", confidence=0.95, provenance=prov),
            amount=ProvenanceValue(value=6500.0, confidence=0.95, provenance=prov),
            units=ProvenanceValue(value=1.0, confidence=0.95, provenance=prov),
            service_date=ProvenanceValue(value="2025-09-15", confidence=0.95, provenance=prov),
        )
    ]

    policy = _base_policy()
    policy.applicable_service_codes = ["44970"]

    # Clinical notes only describe psychiatric evaluation for depression; no appendicitis
    clinical_notes = "Psychiatric consultation note: Patient presents with major depressive episode, depressed mood, insomnia."
    line_ev = DeterministicClinicalAlignmentEngine.evaluate_line_item(
        line_number=1,
        service_code="44970",
        billed_units=1.0,
        billed_amount=6500.0,
        service_date=date(2025, 9, 15),
        clinical_notes_text=clinical_notes,
        diagnosis_codes=["F32.1"],
    )

    clinical = build_clinical_alignment_output(
        expected_line_numbers=[1],
        line_assessments=[line_ev],
        clinical_documents_present=["DOC-PSYCH"],
    )

    doc_texts = {
        "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDIAGNOSIS: F32.1 Major Depressive Episode\nCPT: 44970 Appendectomy\nNPI: 1234567890\nPATIENT SIGNATURE: Signed\nPHYSICIAN SIGNATURE: Dr. Jenkins",
        "CLINICAL_NOTE": clinical_notes,
    }

    report = evaluate_case_consistency(
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        document_texts=doc_texts,
    )

    assert report.is_consistent is False
    assert any("misalignment" in r.lower() for r in report.specific_escalation_reasons)
    assert any("44970" in r for r in report.specific_escalation_reasons)
    assert any("f32.1" in r.lower() for r in report.specific_escalation_reasons)

    ctx = DecisionInputContext(
        case_id="CASE-CLIN-MISALIGN-APP",
        execution_id="EXEC-APP",
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        fraud_output=_base_fraud(),
        consistency_report=report,
        present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
    )
    decision = DeterministicDecisionEngine.adjudicate(ctx)
    assert decision.decision == BusinessDecision.ESCALATE
    assert decision.rule_id_matched == RULE_P3_AMBIGUITY
    assert "misalignment" in decision.justification.lower()


def test_clinical_misalignment_wrist_fracture_with_knee_replacement_escalates() -> None:
    """Patient diagnosed with Wrist Fracture (S52) billed for Total Knee Replacement (27447) escalates."""
    kyc = _base_kyc()
    claims = _base_claims()

    prov = _dummy_provenance("DOC-CLAIM")
    from medishield.domain.clinical_align import DeterministicClinicalAlignmentEngine
    from medishield.domain.contracts import ServiceLine

    # Patient has diagnosis S52 (Fracture of forearm / wrist)
    claims.icd10_codes = [ProvenanceValue(value="S52.501A", confidence=0.95, provenance=prov)]
    # Billed procedure is 27447 (Total Knee Arthroplasty)
    claims.cpt_codes = [ProvenanceValue(value="27447", confidence=0.95, provenance=prov)]
    claims.service_lines = [
        ServiceLine(
            line_number=1,
            hcpcs=ProvenanceValue(value="27447", confidence=0.95, provenance=prov),
            amount=ProvenanceValue(value=15400.0, confidence=0.95, provenance=prov),
            units=ProvenanceValue(value=1.0, confidence=0.95, provenance=prov),
            service_date=ProvenanceValue(value="2025-09-15", confidence=0.95, provenance=prov),
        )
    ]

    policy = _base_policy()
    policy.applicable_service_codes = ["27447"]

    # Clinical records describe wrist injury care only
    clinical_notes = "Orthopedic progress note: Patient sustained right wrist fracture after fall. Splint applied."
    line_ev = DeterministicClinicalAlignmentEngine.evaluate_line_item(
        line_number=1,
        service_code="27447",
        billed_units=1.0,
        billed_amount=15400.0,
        service_date=date(2025, 9, 15),
        clinical_notes_text=clinical_notes,
        diagnosis_codes=["S52.501A"],
    )

    clinical = build_clinical_alignment_output(
        expected_line_numbers=[1],
        line_assessments=[line_ev],
        clinical_documents_present=["DOC-ORTHO"],
    )

    doc_texts = {
        "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDIAGNOSIS: S52.501A Fracture of right wrist\nCPT: 27447 Total Knee Replacement\nNPI: 1234567890\nPATIENT SIGNATURE: Signed\nPHYSICIAN SIGNATURE: Dr. Jenkins",
        "CLINICAL_NOTE": clinical_notes,
    }

    report = evaluate_case_consistency(
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        document_texts=doc_texts,
    )

    assert report.is_consistent is False
    assert any("misalignment" in r.lower() for r in report.specific_escalation_reasons)
    assert any("27447" in r for r in report.specific_escalation_reasons)
    assert any("s52" in r.lower() for r in report.specific_escalation_reasons)

    ctx = DecisionInputContext(
        case_id="CASE-CLIN-MISALIGN-KNEE",
        execution_id="EXEC-KNEE",
        kyc_output=kyc,
        claims_output=claims,
        policy_output=policy,
        clinical_output=clinical,
        fraud_output=_base_fraud(),
        consistency_report=report,
        present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
    )
    decision = DeterministicDecisionEngine.adjudicate(ctx)
    assert decision.decision == BusinessDecision.ESCALATE
    assert decision.rule_id_matched == RULE_P3_AMBIGUITY
    assert "misalignment" in decision.justification.lower()


