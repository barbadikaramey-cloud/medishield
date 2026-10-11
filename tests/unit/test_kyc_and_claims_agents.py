"""Unit tests for KYC Agent and Claims Extraction Agent (FEAT-008, Spec 05 § 4-5)."""

from datetime import date

from medishield.agents.claims import ClaimsAgent
from medishield.agents.kyc import (
    InMemoryMemberRepository,
    KYCAgent,
    MemberRecord,
)
from medishield.db.enums import AgentVerdict
from medishield.domain.contracts import OCRDocumentPayload
from medishield.domain.provenance import ProvenanceType
from medishield.ocr.preprocessor import DefaultOCREngine


def make_test_ocr_doc(doc_id: str, text: str) -> OCRDocumentPayload:
    """Helper creating OCRDocumentPayload with synthesized token spans."""
    pages = DefaultOCREngine().extract_pages(text.encode("utf-8"), "text/plain")
    return OCRDocumentPayload(
        document_id=doc_id,
        total_pages=len(pages),
        pages=pages,
    )


# ── KYC Agent Tests ──────────────────────────────────────────────────────────


def test_kyc_agent_pass_matching_member() -> None:
    repo = InMemoryMemberRepository([
        MemberRecord(
            member_id="MEM-12345",
            first_name="Alice",
            last_name="Smith",
            date_of_birth=date(1985, 5, 20),
            ssn_last4="4321",
        )
    ])
    agent = KYCAgent(member_repo=repo)

    text = """STATE DRIVER LICENSE
DL NUMBER: D9817264
MEMBER ID: MEM-12345
NAME: Alice Smith
DOB: 1985-05-20
EXP DATE: 2030-01-01
"""
    doc = make_test_ocr_doc("doc-id-1", text)
    result = agent.execute(
        case_id="case-1",
        document_payloads=[doc],
        reference_date=date(2026, 1, 1),
    )

    assert result.verdict == AgentVerdict.PASS
    assert result.member_db_matched is True
    assert result.member_id is not None
    assert result.member_id.value == "MEM-12345"
    assert result.confidence >= 0.85
    assert result.dob_matched is True
    assert result.id_expired is False


def test_kyc_agent_fail_unknown_member() -> None:
    repo = InMemoryMemberRepository()
    agent = KYCAgent(member_repo=repo)

    text = """STATE DRIVER LICENSE
MEMBER ID: MEM-99999
NAME: Bob Jones
DOB: 1990-01-01
EXP DATE: 2028-01-01
"""
    doc = make_test_ocr_doc("doc-id-2", text)
    result = agent.execute(case_id="case-2", document_payloads=[doc])

    assert result.verdict == AgentVerdict.FAIL
    assert result.member_db_matched is False
    assert "MEMBER_NOT_FOUND_IN_DB" in result.flags


def test_kyc_agent_fail_dob_mismatch() -> None:
    repo = InMemoryMemberRepository([
        MemberRecord(
            member_id="MEM-12345",
            first_name="Alice",
            last_name="Smith",
            date_of_birth=date(1985, 5, 20),
        )
    ])
    agent = KYCAgent(member_repo=repo)

    text = """STATE DRIVER LICENSE
MEMBER ID: MEM-12345
NAME: Alice Smith
DOB: 1970-12-12
EXP DATE: 2028-01-01
"""
    doc = make_test_ocr_doc("doc-id-3", text)
    result = agent.execute(case_id="case-3", document_payloads=[doc])

    assert result.verdict == AgentVerdict.FAIL
    assert result.dob_matched is False
    assert "DEMOGRAPHIC_DOB_MISMATCH" in result.flags


def test_kyc_agent_fail_expired_id() -> None:
    repo = InMemoryMemberRepository([
        MemberRecord(
            member_id="MEM-12345",
            first_name="Alice",
            last_name="Smith",
            date_of_birth=date(1985, 5, 20),
        )
    ])
    agent = KYCAgent(member_repo=repo)

    text = """STATE DRIVER LICENSE
MEMBER ID: MEM-12345
NAME: Alice Smith
DOB: 1985-05-20
EXP DATE: 2020-01-01
"""
    doc = make_test_ocr_doc("doc-id-4", text)
    result = agent.execute(
        case_id="case-4",
        document_payloads=[doc],
        reference_date=date(2026, 1, 1),
    )

    assert result.verdict == AgentVerdict.FAIL
    assert result.id_expired is True
    assert "EXPIRED_GOVERNMENT_ID" in result.flags


def test_kyc_agent_tamper_detected_clone_stamp() -> None:
    repo = InMemoryMemberRepository([
        MemberRecord(
            member_id="MEM-12345",
            first_name="Alice",
            last_name="Smith",
            date_of_birth=date(1985, 5, 20),
        )
    ])
    agent = KYCAgent(member_repo=repo)

    text = """STATE DRIVER LICENSE
MEMBER ID: MEM-12345
NAME: Alice Smith
DOB: 1985-05-20
EXP DATE: 2030-01-01
Security Check: clone_stamp artifact detected
"""
    doc = make_test_ocr_doc("doc-id-5", text)
    result = agent.execute(
        case_id="case-5",
        document_payloads=[doc],
        reference_date=date(2026, 1, 1),
    )

    assert result.verdict == AgentVerdict.FAIL
    assert result.tamper_detected is True
    assert result.tamper_score >= 0.80
    assert len(result.tamper_signals) >= 1


def test_kyc_agent_missing_member_indeterminate() -> None:
    repo = InMemoryMemberRepository()
    agent = KYCAgent(member_repo=repo)

    text = """STATE DRIVER LICENSE
NAME: Unknown Person
DOB: 1985-05-20
EXP DATE: 2030-01-01
"""
    doc = make_test_ocr_doc("doc-id-6", text)
    result = agent.execute(case_id="case-6", document_payloads=[doc])

    assert result.verdict == AgentVerdict.INDETERMINATE
    assert "MISSING_MEMBER_ID" in result.flags


# ── Claims Agent Tests ────────────────────────────────────────────────────────


def test_claims_agent_pass_clean_claim() -> None:
    agent = ClaimsAgent()
    text = """HEALTH INSURANCE CLAIM FORM
PROVIDER: Dr. Sarah Jenkins Hospital
NPI: 1234567890
DATE OF SERVICE: 2026-03-15
DIAGNOSIS: J02.9
PROCEDURE: 99213
1. 99213 Office Visit 2026-03-15 1.0 $200.00 $200.00
2. 93000 Routine ECG 2026-03-15 1.0 $150.00 $150.00
TOTAL CHARGE: $350.00
"""
    doc = make_test_ocr_doc("doc-claim-1", text)
    result = agent.execute(case_id="case-10", document_payloads=[doc])

    assert result.verdict == AgentVerdict.PASS
    assert result.claim_amount.value == 350.00
    assert result.line_items_sum_valid is True
    assert result.schema_valid is True
    assert any(c.value == "99213" for c in result.cpt_codes)
    assert any(c.value == "J02.9" for c in result.icd10_codes)
    assert len(result.service_lines) == 2


def test_claims_agent_conflict_arithmetic_drift() -> None:
    agent = ClaimsAgent()
    text = """HEALTH INSURANCE CLAIM FORM
NPI: 1234567890
DATE OF SERVICE: 2026-03-15
1. 99213 Office Visit 2026-03-15 1.0 $150.00 $150.00
2. 93000 Routine ECG 2026-03-15 1.0 $100.00 $100.00
TOTAL CHARGE: $500.00
"""
    # Sum of lines is 250.00, header claims 500.00 -> drift
    doc = make_test_ocr_doc("doc-claim-2", text)
    result = agent.execute(case_id="case-11", document_payloads=[doc])

    assert result.verdict == AgentVerdict.CONFLICT
    assert result.line_items_sum_valid is False
    assert any("ARITHMETIC_DRIFT" in err for err in result.validation_errors)


def test_claims_agent_fail_negative_charge() -> None:
    agent = ClaimsAgent()
    text = """HEALTH INSURANCE CLAIM FORM
NPI: 1234567890
DATE OF SERVICE: 2026-03-15
1. 99213 Office Visit 2026-03-15 1.0 -50.00 -50.00
TOTAL CHARGE: $50.00
"""
    doc = make_test_ocr_doc("doc-claim-3", text)
    result = agent.execute(case_id="case-12", document_payloads=[doc])

    assert result.verdict == AgentVerdict.FAIL
    assert any("NEGATIVE" in err for err in result.validation_errors)


def test_claims_agent_indeterminate_invalid_npi() -> None:
    agent = ClaimsAgent()
    text = """HEALTH INSURANCE CLAIM FORM
NPI: 12345
DATE OF SERVICE: 2026-03-15
1. 99213 Office Visit 2026-03-15 1.0 $100.00 $100.00
TOTAL CHARGE: $100.00
"""
    # 5 digit NPI is invalid (requires 10)
    doc = make_test_ocr_doc("doc-claim-4", text)
    result = agent.execute(case_id="case-13", document_payloads=[doc])

    assert result.verdict == AgentVerdict.INDETERMINATE
    assert result.schema_valid is False
    assert any("INVALID_PROVIDER_NPI" in err for err in result.validation_errors)


def test_claims_agent_faithfulness_provenance() -> None:
    agent = ClaimsAgent()
    text = """HEALTH INSURANCE CLAIM FORM
NPI: 9876543210
DATE OF SERVICE: 2026-04-01
1. 99213 Office Visit 2026-04-01 1.0 $250.00 $250.00
TOTAL CHARGE: $250.00
"""
    doc = make_test_ocr_doc("doc-claim-5", text)
    result = agent.execute(case_id="case-14", document_payloads=[doc])

    assert result.claim_amount.provenance is not None
    assert result.claim_amount.provenance.provenance_type == ProvenanceType.SPAN
    assert result.claim_amount.provenance.evidence_quality_score == 1.0
    assert result.claim_amount.provenance.document_id == "doc-claim-5"
