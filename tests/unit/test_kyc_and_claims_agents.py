"""Unit tests for KYC Agent and Claims Extraction Agent (FEAT-008, Spec 05 § 4-5)."""

from datetime import date

from medishield.agents.claims import ClaimsAgent
from medishield.agents.kyc import (
    InMemoryMemberRepository,
    KYCAgent,
    MemberRecord,
)
from medishield.db.catalog import InMemoryCodeCatalogRepository
from medishield.db.enums import AgentVerdict, CodingSystem
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


def test_kyc_agent_fail_exp_colon_format_and_status_badge() -> None:
    """Proves that EXP: MM/DD/YYYY and **EXPIRED** badge (PT_39816 case) are properly parsed and fail KYC."""
    repo = InMemoryMemberRepository([
        MemberRecord(
            member_id="MEM-12345",
            first_name="Alice",
            last_name="Smith",
            date_of_birth=date(1985, 5, 20),
        )
    ])
    agent = KYCAgent(member_repo=repo)

    text = """ILLINOIS DRIVER LICENSE
DL NUMBER: D9817264
MEMBER ID: MEM-12345
NAME: Alice Smith
DOB: 1985-05-20
EXP: 03/28/2025
**EXPIRED**
"""
    doc = make_test_ocr_doc("doc-id-pt39816", text)
    result = agent.execute(
        case_id="case-pt39816",
        document_payloads=[doc],
        service_date=date(2025, 4, 24),
    )

    assert result.verdict == AgentVerdict.FAIL
    assert result.id_expired is True
    assert result.id_expiration_date == date(2025, 3, 28)
    assert "EXPIRED_GOVERNMENT_ID" in result.flags


def test_kyc_agent_dob_not_confused_with_admission_date() -> None:
    """Proves that DOB proximity extraction does not bind to an adjacent admission date."""
    repo = InMemoryMemberRepository([
        MemberRecord(
            member_id="MEM-12345",
            first_name="Alice",
            last_name="Smith",
            date_of_birth=date(1985, 5, 20),
        )
    ])
    agent = KYCAgent(member_repo=repo)

    text = """HOSPITAL INTAKE & ID
MEMBER ID: MEM-12345
NAME: Alice Smith
DOB:
ADMISSION DATE: 2026-03-01
05/20/1985
EXP DATE: 2030-01-01
"""
    doc = make_test_ocr_doc("doc-id-dob-admit", text)
    result = agent.execute(
        case_id="case-dob-admit",
        document_payloads=[doc],
    )

    assert result.dob_matched is True
    assert result.date_of_birth is not None
    assert result.date_of_birth.value == "1985-05-20"


def test_kyc_agent_id_expiring_soon_not_expired_at_service_date() -> None:
    """Proves that ID expiring after service date (e.g. C_024: service 10/22/2025, exp 11/12/2025) is NOT expired."""
    repo = InMemoryMemberRepository([
        MemberRecord(
            member_id="MED-GLD-6218367",
            first_name="Richard",
            last_name="Martinez",
            date_of_birth=date(1989, 5, 25),
        )
    ])
    agent = KYCAgent(member_repo=repo)

    id_text = """GOLD PLAN MEMBER ID CARD
MEMBER ID: MED-GLD-6218367
NAME: Richard Martinez
DOB: 05/25/1989
EFFECTIVE DATE: 10/22/2025
EXPIRATION DATE: 11/12/2025
"""
    claim_text = """CMS-1500 CLAIM FORM
MEMBER ID: MED-GLD-6218367
PATIENT: Richard Martinez
DATE OF SERVICE: 10/22/2025
"""
    doc_id = make_test_ocr_doc("doc-id-c024", id_text)
    doc_claim = make_test_ocr_doc("doc-claim-c024", claim_text)

    # When service date is discovered automatically from claim form spans or passed explicitly
    result = agent.execute(
        case_id="case-c024",
        document_payloads=[doc_id, doc_claim],
    )

    assert result.id_expired is False
    assert result.verdict == AgentVerdict.PASS
    assert "EXPIRED_GOVERNMENT_ID" not in result.flags
    assert "EXPIRING_SOON_GOVERNMENT_ID" in result.flags
    assert result.id_expiration_date == date(2025, 11, 12)



def test_kyc_agent_prefers_grounded_birthdate_key_value_pair() -> None:
    repo = InMemoryMemberRepository([
        MemberRecord(
            member_id="MEM-12345",
            first_name="Elizabeth",
            last_name="Taylor",
            date_of_birth=date(1976, 2, 25),
        )
    ])
    agent = KYCAgent(member_repo=repo)
    text = """STATE CLAIM FORM
MEMBER ID: MEM-12345
NAME: Elizabeth Taylor
10 BIRTHDATE
12 ADMISSION DATE
09/09/2025
02/25/1976
EXP DATE: 2030-01-01
"""
    doc = make_test_ocr_doc("doc-birthdate-kvp", text)
    date_span = next(span for span in doc.pages[0].spans if span.text.strip() == "02/25/1976")
    doc.pages[0].key_value_pairs = [
        {
            "key": "10 BIRTHDATE",
            "value": "02/25/1976",
            "val_span_id": date_span.span_id,
        }
    ]

    result = agent.execute(case_id="case-birthdate-kvp", document_payloads=[doc])

    assert result.dob_matched is True
    assert result.date_of_birth is not None
    assert result.date_of_birth.value == "1976-02-25"
    assert "DEMOGRAPHIC_DOB_MISMATCH" not in result.flags


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
    code_catalog = InMemoryCodeCatalogRepository()
    code_catalog.upsert_code(
        "J02.9",
        CodingSystem.ICD10_CM,
        short_description="Acute pharyngitis, unspecified",
    )
    agent = ClaimsAgent(code_catalog=code_catalog)
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


def test_claims_agent_indeterminate_unsupported_cpt_code() -> None:
    """Proves that a claim with CPT codes outside the supported synthetic catalog fails closed as INDETERMINATE."""
    agent = ClaimsAgent()
    text = """HEALTH INSURANCE CLAIM FORM
NPI: 9876543210
DATE OF SERVICE: 2026-04-01
1. 99999 Experimental Procedure 2026-04-01 1.0 $450.00 $450.00
TOTAL CHARGE: $450.00
"""
    doc = make_test_ocr_doc("doc-claim-unsupported", text)
    result = agent.execute(case_id="case-unsupported-cpt", document_payloads=[doc])

    assert result.verdict == AgentVerdict.INDETERMINATE
    assert any("UNSUPPORTED_CPT_CODES" in err for err in result.validation_errors)
    # The code was NOT silently omitted:
    assert any(c.value == "99999" for c in result.cpt_codes) or any(line.hcpcs.value == "99999" for line in result.service_lines if line.hcpcs)

