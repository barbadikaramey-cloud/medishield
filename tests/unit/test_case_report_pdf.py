"""
Unit Tests for Audit-Ready PDF Case Report Generation.
Specification Reference: ADR-017 § Feature 5.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from medishield.api.main import create_app
from medishield.db.enums import BusinessDecision, WorkflowState
from medishield.db.models import Case
from medishield.db.session import SyncSessionLocal
from medishield.reports.generator import _esc, generate_case_report_pdf


def test_esc_helper_defensive_xml_escaping() -> None:
    """Verifies XML character escaping prevents ReportLab flowable injection."""
    assert _esc(None) == "—"
    assert _esc("") == "—"
    assert _esc("<script>alert('xss')</script>") == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"
    assert _esc("AT&T Healthcare & Partners") == "AT&amp;T Healthcare &amp; Partners"
    assert _esc('Billed amount > $500 & < $1000') == "Billed amount &gt; $500 &amp; &lt; $1000"


def test_generate_case_report_pdf_valid_bytes() -> None:
    """Verifies PDF generator produces valid PDF bytes with all sections populated."""
    case = SimpleNamespace(
        case_id="CASE-TEST-PDF-001",
        workflow_state="COMPLETED",
        current_decision="APPROVE",
        decision_confidence=0.98,
        rule_id_matched="RULE-P1-CLEAN-PASS",
        justification="Clean claim verified against active policy benefits. Grounded in service line arithmetic.",
        created_at=datetime.now(UTC),
        documents=[
            SimpleNamespace(
                document_id="DOC-001",
                file_name="ub04_itemized_bill.pdf",
                doc_type="CLAIM_FORM",
                checksum="a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
            ),
            SimpleNamespace(
                document_id="DOC-002",
                file_name="drivers_license.pdf",
                doc_type="ID_DOCUMENT",
                checksum="b2c3d4e5f6a10718293a4b5c6d7e8f90123456789abcdef0123456789abcdef1",
            ),
        ],
        agent_findings={
            "kyc": {
                "verdict": "PASS",
                "confidence": 0.99,
                "member_id": {"value": "MEM-12345"},
                "first_name": {"value": "Alice"},
                "last_name": {"value": "Smith"},
                "member_db_matched": True,
                "tamper_score": 0.0,
                "flags": [],
            },
            "claims": {
                "verdict": "PASS",
                "confidence": 0.98,
                "claim_amount": {"value": 1250.00},
                "calculated_lines_sum": 1250.00,
                "line_items_sum_valid": True,
                "service_date": {"value": "2026-08-15"},
                "provider_npi": {"value": "1982736450"},
            },
            "policy": {
                "verdict": "PASS",
                "policy_id": "POL-CORP-2026",
                "coverage_percentage": 100,
                "policy_effective_date_valid": True,
                "prior_authorization_required": False,
                "cited_clauses": ["Section 4.1 Inpatient Care"],
            },
            "fraud": {
                "verdict": "PASS",
                "fraud_score": 0.05,
                "risk_level": "LOW",
                "duplicate_detected": False,
                "anomalies": [],
            },
            "clinical_alignment": {
                "verdict": "PASS",
                "confidence": 0.95,
                "total_lines_assessed": 3,
                "lines_fully_supported_count": 3,
                "assessment_sufficient": True,
            },
        },
    )

    pdf_bytes = generate_case_report_pdf(case)
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 1000
    # Standard PDF magic header
    assert pdf_bytes.startswith(b"%PDF-")


def test_generate_case_report_pdf_with_adversarial_xml_strings() -> None:
    """Verifies PDF generation succeeds even with adversarial XML strings in case fields."""
    case = SimpleNamespace(
        case_id="CASE-<XML>-INJECTION-01",
        workflow_state="ESCALATE",
        current_decision="ESCALATE",
        decision_confidence=0.45,
        rule_id_matched="<RULE-P2-INCOMPLETE>",
        justification="<bold>Missing document</bold> & <italic>incomplete demographics</italic> & 'quoted' text.",
        created_at=datetime.now(UTC),
        documents=[
            SimpleNamespace(
                document_id="DOC-<TAG>",
                file_name="bad_filename_<v1>&.pdf",
                doc_type="UNKNOWN",
                checksum="0000000000000000000000000000000000000000000000000000000000000000",
            )
        ],
        agent_findings={},
    )

    # Must complete without xml.parsers.expat or ReportLab XML parse errors
    pdf_bytes = generate_case_report_pdf(case)
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 500


def test_case_report_api_endpoint() -> None:
    """Verifies GET /api/v1/cases/{case_id}/report endpoint returns valid PDF stream."""
    app = create_app()
    client = TestClient(app)

    case_id = f"CASE-{uuid.uuid4()}"
    session = SyncSessionLocal()
    try:
        c = Case(
            case_id=case_id,
            workflow_state=WorkflowState.COMPLETED,
            current_decision=BusinessDecision.APPROVE,
            decision_confidence=0.99,
            is_complete=True,
            version=1,
        )
        session.add(c)
        session.commit()
    finally:
        session.close()

    res = client.get(
        f"/api/v1/cases/{case_id}/report",
        headers={"X-User-Role": "OPERATIONS_REVIEWER"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert "attachment" in res.headers["content-disposition"]
    assert f"MediShield_Case_{case_id}_Report.pdf" in res.headers["content-disposition"]
    assert res.content.startswith(b"%PDF-")


def test_generate_case_report_pdf_with_clause_dicts_and_long_narrative() -> None:
    """Verifies PDF generation succeeds when cited_clauses is a list of dicts and narrative spans pages."""
    case = SimpleNamespace(
        case_id="CASE-COMPLEX-001",
        workflow_state="ESCALATE",
        current_decision="ESCALATE",
        decision_confidence=0.80,
        rule_id_matched="RULE-P3-AMBIGUITY",
        justification="Paragraph 1: Clinical discrepancy discovered.\n\n" + ("Long explanation narrative detailing line items and policy guidelines. " * 50) + "\n\nParagraph 3: Routing to human reviewer.",
        created_at=datetime.now(UTC),
        documents=[
            SimpleNamespace(
                document_id="DOC-999",
                file_name="hospital_bill.pdf",
                doc_type="CLAIM_FORM",
                checksum="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            )
        ],
        agent_findings={
            "kyc": {
                "verdict": "PASS",
                "confidence": 0.95,
                "flags": ["FLAG_A", "FLAG_B"],
            },
            "policy": {
                "verdict": "INDETERMINATE",
                "coverage_percentage": 80,
                "cited_clauses": [
                    {
                        "section_number": "SEC-5.4",
                        "clause_title": "Inpatient Surgical and Major Procedures",
                        "page_number": 25,
                    },
                    {
                        "section_number": "SEC-3.1",
                        "clause_title": "Outpatient Evaluation and Management",
                        "page_number": 12,
                    },
                ],
            },
            "fraud": {
                "verdict": "PASS",
                "risk_level": "LOW",
                "anomalies": ["Outlier billing interval flagged"],
            },
        },
    )

    pdf_bytes = generate_case_report_pdf(case)
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 2000
