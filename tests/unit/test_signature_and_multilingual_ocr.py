"""Unit tests for Positive Evidence Signature Attestation and Multilingual OCR (ADR-019)."""

from datetime import date
from decimal import Decimal

from medishield.db.enums import AgentVerdict, BusinessDecision, DocumentType
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    ClinicalAlignmentOutput,
    KYCAgentOutput,
    PolicyCoverageOutput,
    ProvenanceValue,
    build_clinical_alignment_output,
)
from medishield.domain.decision import DecisionInputContext, DeterministicDecisionEngine
from medishield.domain.explanation import generate_deterministic_explanation
from medishield.domain.faithfulness import evaluate_case_consistency
from medishield.ocr.preprocessor import is_multilingual_or_degraded
from tests.unit.test_clinical_alignment import make_clean_line_evidence


def _base_kyc() -> KYCAgentOutput:
    return KYCAgentOutput(
        verdict=AgentVerdict.PASS,
        confidence=0.98,
        member_id=ProvenanceValue(value="MEM-001", confidence=0.99),
        first_name=ProvenanceValue(value="Alice", confidence=0.99),
        last_name=ProvenanceValue(value="Smith", confidence=0.99),
        dob=ProvenanceValue(value=date(1985, 5, 20), confidence=0.99),
        member_db_matched=True,
        dob_matched=True,
        policy_active_on_service_date=True,
        latency_ms=10,
    )


def _base_claims() -> ClaimsAgentOutput:
    return ClaimsAgentOutput(
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        claim_amount=ProvenanceValue(value=Decimal("5000.00"), confidence=0.99),
        service_date=ProvenanceValue(value=date(2025, 9, 15), confidence=0.99),
        provider_npi=ProvenanceValue(value="1234567890", confidence=0.99),
        schema_valid=True,
        line_items_sum_valid=True,
        calculated_lines_sum=5000.00,
        cpt_codes=[ProvenanceValue(value="27447", confidence=0.99)],
        icd10_codes=[ProvenanceValue(value="M17.11", confidence=0.99)],
        latency_ms=10,
    )


def _base_policy() -> PolicyCoverageOutput:
    return PolicyCoverageOutput(
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        covered=True,
        coverage_percentage=100.0,
        effective_on_service_date=True,
        reasoning_summary="Fully covered under inpatient benefit",
        prior_authorization_present=True,
        prior_authorization_number="PA-2025-9988",
        latency_ms=10,
    )




def _base_clinical() -> ClinicalAlignmentOutput:
    clin_evidence = make_clean_line_evidence(
        line_number=1,
        service_code="27447",
        billed_units=1.0,
        billed_amount=5000.00,
    )
    return build_clinical_alignment_output(
        expected_line_numbers=[1],
        line_assessments=[clin_evidence],
        clinical_documents_present=True,
    )


class TestPositiveSignatureVerification:
    """Tests verifying the Positive Evidence Attestation Model for signatures (ADR-019)."""

    def test_unsigned_claim_and_discharge_summary_evaluate_false(self) -> None:
        """Documents with explicit missing signature markers must evaluate to False (Rule 12 & 13)."""
        doc_texts = {
            "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDATE OF SERVICE: 2025-09-15\nNPI: 1234567890\nPATIENT SIGNATURE: [missing]\nTOTAL: $5000.00",
            "DISCHARGE_SUMMARY": "HOSPITAL DISCHARGE SUMMARY\nPATIENT: Alice Smith\nADMISSION DATE: 2025-09-14\nDISCHARGE DATE: 2025-09-18\nHOSPITAL: General Hospital",
        }

        report = evaluate_case_consistency(
            kyc_output=_base_kyc(),
            claims_output=_base_claims(),
            policy_output=_base_policy(),
            clinical_output=_base_clinical(),
            document_texts=doc_texts,
        )

        assert report.doctor_signature_verified is False
        assert report.patient_signature_verified is False
        assert report.is_consistent is False
        assert any("physician signature" in r.lower() for r in report.conflicts_detected)
        assert any("patient signature" in r.lower() for r in report.conflicts_detected)

    def test_patient_signature_not_mandatory_when_field_absent(self) -> None:
        """When the claim form lacks a patient signature field, patient signature is NOT mandatory."""
        doc_texts = {
            "CLAIM_FORM": (
                "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDATE OF SERVICE: 2025-09-15\n"
                "NPI: 1234567890\nPHYSICIAN SIGNATURE: Dr. Sarah Jenkins, MD"
            ),
            "DISCHARGE_SUMMARY": (
                "HOSPITAL DISCHARGE SUMMARY\nPATIENT: Alice Smith\n"
                "ADMISSION DATE: 2025-09-14\nDISCHARGE DATE: 2025-09-18\n"
                "ATTENDING PHYSICIAN: Dr. Sarah Jenkins, MD"
            ),
        }

        report = evaluate_case_consistency(
            kyc_output=_base_kyc(),
            claims_output=_base_claims(),
            policy_output=_base_policy(),
            clinical_output=_base_clinical(),
            document_texts=doc_texts,
        )

        assert report.doctor_signature_verified is True
        assert report.patient_signature_verified is True
        assert report.officer_signature_verified is True
        assert report.is_consistent is True

    def test_blank_underline_signature_evaluates_false(self) -> None:
        """Documents with blank underline lines (e.g. 'मुहर: ________') must evaluate to False."""
        doc_texts = {
            "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nPATIENT SIGNATURE: ____________________\nNPI: 1234567890",
            "DISCHARGE_SUMMARY": "HOSPITAL DISCHARGE SUMMARY\nPATIENT: Alice Smith\nप्रदाता मुहर: __________________",
        }

        report = evaluate_case_consistency(
            kyc_output=_base_kyc(),
            claims_output=_base_claims(),
            policy_output=_base_policy(),
            clinical_output=_base_clinical(),
            document_texts=doc_texts,
        )

        assert report.doctor_signature_verified is False
        assert report.patient_signature_verified is False

    def test_positive_doctor_and_patient_signatures_evaluate_true(self) -> None:
        """Valid positive signature affirmations evaluate to True."""
        doc_texts = {
            "CLAIM_FORM": (
                "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDATE OF SERVICE: 2025-09-15\n"
                "NPI: 1234567890\nPATIENT SIGNATURE: Signed on file\n"
                "PHYSICIAN SIGNATURE: Dr. Sarah Jenkins, MD"
            ),
            "DISCHARGE_SUMMARY": (
                "HOSPITAL DISCHARGE SUMMARY\nPATIENT: Alice Smith\n"
                "ADMISSION DATE: 2025-09-14\nDISCHARGE DATE: 2025-09-18\n"
                "ATTENDING PHYSICIAN: Dr. Sarah Jenkins, MD"
            ),
        }

        report = evaluate_case_consistency(
            kyc_output=_base_kyc(),
            claims_output=_base_claims(),
            policy_output=_base_policy(),
            clinical_output=_base_clinical(),
            document_texts=doc_texts,
        )

        assert report.doctor_signature_verified is True
        assert report.patient_signature_verified is True
        assert report.officer_signature_verified is True
        assert report.is_consistent is True

    def test_policy_amendment_officer_signature_not_flagged_when_field_absent(self) -> None:
        """When policy amendment document has no officer signature field, it is not flagged."""
        doc_texts = {
            "POLICY_AMENDMENT": "POLICY ENDORSEMENT #4: Coverage effective for total knee replacement M17.11.",
        }

        report = evaluate_case_consistency(
            kyc_output=_base_kyc(),
            claims_output=_base_claims(),
            policy_output=_base_policy(),
            clinical_output=_base_clinical(),
            document_texts=doc_texts,
        )

        assert report.officer_signature_verified is True

    def test_policy_amendment_officer_signature_flagged_when_field_present_but_unsigned(self) -> None:
        """When policy amendment document contains an officer signature field that is unsigned, it must be flagged."""
        doc_texts = {
            "CLAIM_FORM": (
                "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDATE OF SERVICE: 2025-09-15\n"
                "NPI: 1234567890\nPHYSICIAN SIGNATURE: Dr. Sarah Jenkins, MD"
            ),
            "DISCHARGE_SUMMARY": (
                "HOSPITAL DISCHARGE SUMMARY\nPATIENT: Alice Smith\n"
                "ADMISSION DATE: 2025-09-14\nDISCHARGE DATE: 2025-09-18\n"
                "ATTENDING PHYSICIAN: Dr. Sarah Jenkins, MD"
            ),
            "POLICY_AMENDMENT": (
                "POLICY AMENDMENT #2\nEFFECTIVE DATE: 2025-01-01\n"
                "AUTHORIZED OFFICER SIGNATURE: ____________________"
            ),
        }

        report = evaluate_case_consistency(
            kyc_output=_base_kyc(),
            claims_output=_base_claims(),
            policy_output=_base_policy(),
            clinical_output=_base_clinical(),
            document_texts=doc_texts,
        )

        assert report.officer_signature_verified is False
        assert report.is_consistent is False
        assert any("authorized officer signature" in r.lower() for r in report.conflicts_detected)

    def test_policy_amendment_officer_signature_verified_when_signed(self) -> None:
        """When policy amendment document contains a signed authorized officer block, it evaluates to True."""
        doc_texts = {
            "CLAIM_FORM": (
                "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDATE OF SERVICE: 2025-09-15\n"
                "NPI: 1234567890\nPHYSICIAN SIGNATURE: Dr. Sarah Jenkins, MD"
            ),
            "DISCHARGE_SUMMARY": (
                "HOSPITAL DISCHARGE SUMMARY\nPATIENT: Alice Smith\n"
                "ADMISSION DATE: 2025-09-14\nDISCHARGE DATE: 2025-09-18\n"
                "ATTENDING PHYSICIAN: Dr. Sarah Jenkins, MD"
            ),
            "POLICY_AMENDMENT": (
                "POLICY AMENDMENT #2\nEFFECTIVE DATE: 2025-01-01\n"
                "AUTHORIZED OFFICER SIGNATURE: Jane Doe, VP Underwriting"
            ),
        }

        report = evaluate_case_consistency(
            kyc_output=_base_kyc(),
            claims_output=_base_claims(),
            policy_output=_base_policy(),
            clinical_output=_base_clinical(),
            document_texts=doc_texts,
        )

        assert report.officer_signature_verified is True
        assert report.doctor_signature_verified is True
        assert report.patient_signature_verified is True
        assert report.is_consistent is True

    def test_unsigned_claim_triggers_rule_p3_escalation(self) -> None:
        """Missing signatures prevent automated approval and enforce Rule P3 Human Review."""
        doc_texts = {
            "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDATE OF SERVICE: 2025-09-15\nNPI: 1234567890",
            "DISCHARGE_SUMMARY": "DISCHARGE SUMMARY\nPATIENT: Alice Smith\nADMISSION DATE: 2025-09-14\nDISCHARGE DATE: 2025-09-18",
        }

        report = evaluate_case_consistency(
            kyc_output=_base_kyc(),
            claims_output=_base_claims(),
            policy_output=_base_policy(),
            clinical_output=_base_clinical(),
            document_texts=doc_texts,
        )

        ctx = DecisionInputContext(
            case_id="CASE-SIG-TEST",
            execution_id="EXEC-SIG-TEST",
            kyc_output=_base_kyc(),
            claims_output=_base_claims(),
            policy_output=_base_policy(),
            clinical_output=_base_clinical(),
            consistency_report=report,
            present_document_types=[
                DocumentType.CLAIM_FORM,
                DocumentType.DISCHARGE_SUMMARY,
                DocumentType.ID_DOCUMENT,
            ],
        )

        decision = DeterministicDecisionEngine.adjudicate(ctx)
        assert decision.decision == BusinessDecision.ESCALATE
        assert "P3" in decision.rule_id_matched


class TestMultilingualScriptDetection:
    """Tests script detection and RapidOCR corruption detection heuristics."""

    def test_detects_devanagari_unicode(self) -> None:
        hindi_text = "मुख्य निदान: घुटने का ऑस्टियोआर्थराइटिस"
        assert is_multilingual_or_degraded(hindi_text) is True

    def test_detects_hindi_case_and_doc_ids(self) -> None:
        assert is_multilingual_or_degraded("clean text", case_id="CASE-HI-001", document_id="DOC-01") is True
        assert is_multilingual_or_degraded("clean text", case_id="CASE-01", document_id="DOC-CL-HI-01") is True

    def test_detects_currency_symbols(self) -> None:
        assert is_multilingual_or_degraded("Total amount: ₹79,400") is True
        assert is_multilingual_or_degraded("Billing system: SYNTHETIC Currency: INR") is True

    def test_detects_rapidocr_devanagari_corruption_tokens(self) -> None:
        garbled_text = "SH\nCASEDOCUMENT·DISCHARGESUMMARY\nfa Ri\nMSH-HI-002\nl/\n2026-05-10\nLb /k\n27447"
        assert is_multilingual_or_degraded(garbled_text) is True

    def test_clean_english_claim_form_not_flagged(self) -> None:
        clean_text = "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDATE OF SERVICE: 2025-09-15\nNPI: 1234567890"
        assert is_multilingual_or_degraded(clean_text, case_id="CASE-EN-001", document_id="DOC-01") is False


class TestExplanationSignatureFidelity:
    """Verifies that decision explanations faithfully disclose missing signatures."""

    def test_explanation_flags_unverified_signatures(self) -> None:
        doc_texts = {
            "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDATE OF SERVICE: 2025-09-15\nNPI: 1234567890\nPATIENT SIGNATURE: [missing]",
            "DISCHARGE_SUMMARY": "DISCHARGE SUMMARY\nPATIENT: Alice Smith\nADMISSION DATE: 2025-09-14\nDISCHARGE DATE: 2025-09-18",
            "POLICY_AMENDMENT": "POLICY AMENDMENT #1\nAUTHORIZED OFFICER: ____________________",
        }

        report = evaluate_case_consistency(
            kyc_output=_base_kyc(),
            claims_output=_base_claims(),
            policy_output=_base_policy(),
            clinical_output=_base_clinical(),
            document_texts=doc_texts,
        )

        ctx = DecisionInputContext(
            case_id="CASE-SIG-EXPLAIN",
            execution_id="EXEC-SIG-EXPLAIN",
            kyc_output=_base_kyc(),
            claims_output=_base_claims(),
            policy_output=_base_policy(),
            clinical_output=_base_clinical(),
            consistency_report=report,
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.DISCHARGE_SUMMARY],
        )

        decision = DeterministicDecisionEngine.adjudicate(ctx)
        explanation = generate_deterministic_explanation(ctx, decision)

        # Must report signatures as missing / unverified
        assert "Doctor Signature / Attestation:** ❌ Missing or unverified" in explanation
        assert "Patient Signature / Assignment:** ❌ Missing patient signature" in explanation
        assert "Authorized Officer Signature:** ❌ Missing authorized officer signature" in explanation
        assert "Verified on clinical record" not in explanation

    def test_explanation_omits_unverified_warning_when_patient_signature_field_absent(self) -> None:
        doc_texts = {
            "CLAIM_FORM": "CMS-1500 CLAIM FORM\nPATIENT: Alice Smith\nDATE OF SERVICE: 2025-09-15\nNPI: 1234567890\nPHYSICIAN SIGNATURE: Dr. Sarah Jenkins, MD",
            "DISCHARGE_SUMMARY": "DISCHARGE SUMMARY\nPATIENT: Alice Smith\nADMISSION DATE: 2025-09-14\nDISCHARGE DATE: 2025-09-18\nATTENDING PHYSICIAN: Dr. Sarah Jenkins, MD",
        }

        report = evaluate_case_consistency(
            kyc_output=_base_kyc(),
            claims_output=_base_claims(),
            policy_output=_base_policy(),
            clinical_output=_base_clinical(),
            document_texts=doc_texts,
        )

        ctx = DecisionInputContext(
            case_id="CASE-SIG-EXPLAIN-PASS",
            execution_id="EXEC-SIG-EXPLAIN-PASS",
            kyc_output=_base_kyc(),
            claims_output=_base_claims(),
            policy_output=_base_policy(),
            clinical_output=_base_clinical(),
            consistency_report=report,
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.DISCHARGE_SUMMARY],
        )

        decision = DeterministicDecisionEngine.adjudicate(ctx)
        explanation = generate_deterministic_explanation(ctx, decision)

        assert "Doctor Signature / Attestation:** ✅ Verified on clinical record" in explanation
        assert "Patient Signature / Assignment:** ✅ Verified or not required on claim documentation" in explanation
        assert "Authorized Officer Signature:** ❌" not in explanation
