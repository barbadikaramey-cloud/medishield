from datetime import date
from decimal import Decimal

from medishield.db.enums import AgentVerdict
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    KYCAgentOutput,
    OCRBoundingBox,
    OCRDocumentPayload,
    OCRPagePayload,
    OCRTextSpan,
    PolicyCoverageOutput,
)
from medishield.domain.faithfulness import (
    calculate_composite_decision_confidence,
    evaluate_case_consistency,
    fuzzy_token_match,
    normalize_text,
    verify_clinical_evidence_reference,
    verify_extracted_span,
)
from medishield.domain.provenance import (
    EvidenceSource,
    EvidenceVerificationStatus,
    FieldProvenance,
    ProvenanceType,
    ProvenanceValue,
)


def test_normalize_text():
    raw = "  Hospital Total:   $1,250.00!! \n\t "
    assert normalize_text(raw) == "hospital total 125000"


def test_fuzzy_token_match():
    assert fuzzy_token_match("John Doe", "Dr. John Doe MD") is True
    assert fuzzy_token_match("Jane Smith", "John Doe") is False


def build_mock_ocr_payload() -> OCRDocumentPayload:
    span1 = OCRTextSpan(
        span_id="span-p1-001",
        page_number=1,
        text="James T. Kirk",
        confidence=0.99,
        bbox=OCRBoundingBox(x_min=0.1, y_min=0.1, x_max=0.3, y_max=0.15),
    )
    span2 = OCRTextSpan(
        span_id="span-p1-002",
        page_number=1,
        text="Total Billed: $450.00",
        confidence=0.95,
        bbox=OCRBoundingBox(x_min=0.4, y_min=0.8, x_max=0.6, y_max=0.85),
    )
    page1 = OCRPagePayload(
        page_number=1,
        raw_text="Patient: James T. Kirk. Procedure Office Visit. Total Billed: $450.00",
        spans=[span1, span2],
    )
    return OCRDocumentPayload(document_id="doc-001", total_pages=1, pages=[page1])


def test_verify_extracted_span_direct_match():
    payload = build_mock_ocr_payload()
    verified, prov = verify_extracted_span(
        extracted_value="James T. Kirk",
        cited_span_id="span-p1-001",
        cited_page_number=1,
        ocr_payload=payload,
    )
    assert verified is True
    assert prov.provenance_type == ProvenanceType.SPAN
    assert prov.evidence_quality_score == 1.0
    assert prov.span_id == "span-p1-001"
    assert prov.bounding_box == [0.1, 0.1, 0.3, 0.15]


def test_verify_extracted_span_page_fallback():
    payload = build_mock_ocr_payload()
    # Fact is on page 1 ('Office Visit') but no span ID cited
    verified, prov = verify_extracted_span(
        extracted_value="Office Visit",
        cited_span_id=None,
        cited_page_number=1,
        ocr_payload=payload,
    )
    assert verified is True
    assert prov.provenance_type == ProvenanceType.PAGE
    assert prov.evidence_quality_score == 0.5


def test_verify_extracted_span_hallucination():
    payload = build_mock_ocr_payload()
    # Extracted value not anywhere on page
    verified, prov = verify_extracted_span(
        extracted_value="Appendectomy",
        cited_span_id="span-p1-999",
        cited_page_number=1,
        ocr_payload=payload,
    )
    assert verified is False
    assert prov.provenance_type == ProvenanceType.INFERRED
    assert prov.evidence_quality_score == 0.0


def test_clinical_evidence_rejects_wrong_document_or_fabricated_span() -> None:
    payload = build_mock_ocr_payload()
    for reference in (
        FieldProvenance(
            document_id="DOC-OTHER",
            page_number=1,
            span_id="span-p1-001",
            text_content="James T. Kirk",
            evidence_quality_score=0.99,
        ),
        FieldProvenance(
            document_id="doc-001",
            page_number=1,
            span_id="span-p1-fabricated",
            text_content="James T. Kirk",
            evidence_quality_score=0.99,
        ),
    ):
        verified, result = verify_clinical_evidence_reference(reference, payload)
        assert verified is False
        assert result.verification_status == EvidenceVerificationStatus.REJECTED
        assert result.evidence_source == EvidenceSource.OCR
        assert result.evidence_quality_score == 0.0


def test_clinical_evidence_verifies_exact_span_and_bbox() -> None:
    payload = build_mock_ocr_payload()
    source_span = payload.pages[0].spans[0]
    reference = FieldProvenance(
        document_id=payload.document_id,
        page_number=source_span.page_number,
        span_id=source_span.span_id,
        text_content=source_span.text,
        bbox=source_span.bbox,
        evidence_quality_score=0.99,
    )

    verified, result = verify_clinical_evidence_reference(reference, payload)

    assert verified is True
    assert result.verification_status == EvidenceVerificationStatus.VERIFIED
    assert result.provenance_type == ProvenanceType.SPAN
    assert result.span_id == source_span.span_id
    assert result.bounding_box == source_span.bbox.to_list()


def test_clinical_evidence_page_match_is_not_criterion_relevance() -> None:
    payload = build_mock_ocr_payload()
    reference = FieldProvenance(
        document_id=payload.document_id,
        page_number=1,
        text_content="Office Visit",
        evidence_quality_score=0.8,
    )

    verified, result = verify_clinical_evidence_reference(reference, payload)

    assert verified is True
    assert result.provenance_type == ProvenanceType.PAGE
    assert result.verification_status == EvidenceVerificationStatus.VERIFIED
    assert result.evidence_relevance_verified is False


def test_evaluate_case_consistency_clean():
    kyc = KYCAgentOutput(verdict=AgentVerdict.PASS, confidence=0.98, latency_ms=100)
    claims = ClaimsAgentOutput(
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        claim_amount=ProvenanceValue(value=Decimal("100.00"), confidence=0.99),
        service_date=ProvenanceValue(value=date(2025, 4, 1), confidence=0.99),
        provider_npi=ProvenanceValue(value="1234567890", confidence=0.99),
        schema_valid=True,
        line_items_sum_valid=True,
        calculated_lines_sum=100.00,
        latency_ms=100,
    )
    policy = PolicyCoverageOutput(
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        covered=True,
        coverage_percentage=100.0,
        effective_on_service_date=True,
        reasoning_summary="Covered in full",
        latency_ms=100,
    )

    report = evaluate_case_consistency(kyc, claims, policy)
    assert report.is_consistent is True
    assert report.verdict_override is None
    assert len(report.conflicts_detected) == 0


def test_evaluate_case_consistency_arithmetic_drift():
    kyc = KYCAgentOutput(verdict=AgentVerdict.PASS, confidence=0.98, latency_ms=100)
    claims = ClaimsAgentOutput(
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        claim_amount=ProvenanceValue(value=Decimal("200.00"), confidence=0.99),
        service_date=ProvenanceValue(value=date(2025, 4, 1), confidence=0.99),
        provider_npi=ProvenanceValue(value="1234567890", confidence=0.99),
        schema_valid=True,
        line_items_sum_valid=False,  # Total 200 vs sum 150
        calculated_lines_sum=150.00,
        latency_ms=100,
    )
    policy = PolicyCoverageOutput(
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        covered=True,
        coverage_percentage=100.0,
        effective_on_service_date=True,
        reasoning_summary="Covered",
        latency_ms=100,
    )

    report = evaluate_case_consistency(kyc, claims, policy)
    assert report.is_consistent is False
    assert report.verdict_override == AgentVerdict.CONFLICT
    assert any("Arithmetic drift" in c for c in report.conflicts_detected)


def test_calculate_composite_decision_confidence():
    # Provenance 1.0, validation clean 1.0, min model confidence 0.90
    # Expected: 0.40(1.0) + 0.35(1.0) + 0.25(0.90) = 0.40 + 0.35 + 0.225 = 0.975
    score = calculate_composite_decision_confidence(
        provenance_scores=[1.0, 1.0],
        validation_valid=True,
        agent_confidences=[0.95, 0.90, 0.98],
    )
    assert score == 0.975

    # Poor provenance 0.0 (hallucinated), validation fail
    # Expected: 0.40(0.0) + 0.35(0.0) + 0.25(0.99) = 0.248
    score_penalized = calculate_composite_decision_confidence(
        provenance_scores=[0.0, 0.0],
        validation_valid=False,
        agent_confidences=[0.99],
    )
    assert score_penalized == 0.247
