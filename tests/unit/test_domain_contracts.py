from datetime import date
from decimal import Decimal

from medishield.db.enums import AgentVerdict, BusinessDecision, DocumentType
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    ClassifierAgentOutput,
    FraudAgentOutput,
    OCRBoundingBox,
    OCRDocumentPayload,
    OCRPagePayload,
    OCRTextSpan,
    OrchestratorOutput,
    RiskLevel,
    ServiceLineItem,
)
from medishield.domain.provenance import (
    FieldProvenance,
    ProvenanceType,
    ProvenanceValue,
)


def test_field_provenance_and_value():
    prov = FieldProvenance(
        document_id="doc-123",
        page_number=1,
        span_id="span-p1-001",
        text_content="Jane Doe",
        bounding_box=[0.1, 0.2, 0.4, 0.3],
        provenance_type=ProvenanceType.SPAN,
        evidence_quality_score=1.0,
    )
    assert prov.document_id == "doc-123"
    assert prov.evidence_quality_score == 1.0

    val = ProvenanceValue(value="Jane Doe", confidence=0.99, provenance=prov)
    assert val.value == "Jane Doe"
    assert val.confidence == 0.99
    assert val.provenance.span_id == "span-p1-001"


def test_ocr_payload_structure():
    span = OCRTextSpan(
        span_id="span-p1-001",
        page_number=1,
        text="Total $500.00",
        confidence=0.95,
        bbox=OCRBoundingBox(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.2),
    )
    page = OCRPagePayload(page_number=1, raw_text="Total $500.00", spans=[span])
    doc = OCRDocumentPayload(document_id="doc-123", total_pages=1, pages=[page])

    assert doc.document_id == "doc-123"
    assert len(doc.pages[0].spans) == 1


def test_classifier_output():
    out = ClassifierAgentOutput(
        document_id="doc-123",
        doc_type=DocumentType.CLAIM_FORM,
        confidence=0.98,
        verdict=AgentVerdict.PASS,
        latency_ms=120,
    )
    assert out.doc_type == DocumentType.CLAIM_FORM
    assert out.verdict == AgentVerdict.PASS


def test_claims_agent_output():
    prov_amount = ProvenanceValue(value=Decimal("150.00"), confidence=0.98)
    prov_date = ProvenanceValue(value=date(2025, 6, 15), confidence=0.99)
    prov_npi = ProvenanceValue(value="1234567890", confidence=0.95)

    line = ServiceLineItem(
        line_number=1,
        cpt_code=ProvenanceValue(value="99213", confidence=0.98),
        service_date=prov_date,
        quantity=ProvenanceValue(value=1.0, confidence=1.0),
        unit_charge=ProvenanceValue(value=150.00, confidence=0.98),
        total_charge=ProvenanceValue(value=150.00, confidence=0.98),
    )

    claims_out = ClaimsAgentOutput(
        verdict=AgentVerdict.PASS,
        confidence=0.97,
        claim_amount=prov_amount,
        service_date=prov_date,
        provider_npi=prov_npi,
        service_lines=[line],
        schema_valid=True,
        line_items_sum_valid=True,
        calculated_lines_sum=150.00,
        latency_ms=250,
    )
    assert claims_out.schema_valid is True
    assert len(claims_out.service_lines) == 1


def test_fraud_agent_output():
    out = FraudAgentOutput(
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        fraud_score=0.12,
        risk_level=RiskLevel.LOW,
        latency_ms=45,
    )
    assert out.risk_level == RiskLevel.LOW
    assert out.fraud_score == 0.12


def test_orchestrator_output():
    out = OrchestratorOutput(
        case_id="CASE-20261002-001",
        execution_id="exec-001",
        decision=BusinessDecision.APPROVE,
        decision_confidence=0.945,
        rule_id_matched="RULE-P6-CLEAN-PASS",
        justification="All verification checks passed with clean fraud profile.",
        agent_summaries={"kyc": "PASS", "claims": "PASS", "policy": "PASS"},
    )
    assert out.decision == BusinessDecision.APPROVE
    assert out.is_auto_decided is True


def test_bbox_clamping_and_normalization():
    """Verifies that BBox clamps small boundary overshoots, normalizes inverted dimensions, and converts to list."""
    from medishield.domain.contracts import BBox
    from medishield.domain.provenance import FieldProvenance, ProvenanceType

    # 1. Clamping minor out-of-range floats
    b = BBox(x0=-0.02, y0=-0.01, x1=1.05, y1=1.01)
    assert b.x0 == 0.0
    assert b.y0 == 0.0
    assert b.x1 == 1.0
    assert b.y1 == 1.0

    # 2. Inverted coordinates normalization
    b_inv = BBox(x0=0.8, y0=0.9, x1=0.2, y1=0.1)
    assert b_inv.x0 == 0.2
    assert b_inv.x1 == 0.8
    assert b_inv.y0 == 0.1
    assert b_inv.y1 == 0.9

    # 3. Construct from 4-element list/tuple
    b_list = BBox.model_validate([0.1, 0.2, 0.4, 0.5])
    assert b_list.x0 == 0.1
    assert b_list.y0 == 0.2
    assert b_list.x1 == 0.4
    assert b_list.y1 == 0.5
    assert b_list.to_list() == [0.1, 0.2, 0.4, 0.5]

    # 4. FieldProvenance automatic bbox synchronization
    prov = FieldProvenance(
        document_id="DOC-01",
        page_number=1,
        text_content="Sample",
        bounding_box=[0.15, 0.25, 0.35, 0.45],
        provenance_type=ProvenanceType.SPAN,
        evidence_quality_score=1.0,
    )
    assert prov.bbox is not None
    assert prov.bbox.x0 == 0.15
    assert prov.bbox.y0 == 0.25
    assert prov.bbox.x1 == 0.35
    assert prov.bbox.y1 == 0.45
    assert prov.bounding_box == [0.15, 0.25, 0.35, 0.45]
