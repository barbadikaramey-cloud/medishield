"""
Unit and Integration Tests for JPEG Tamper-Detection using Error Level Analysis (ELA)
and KYC Image Retrieval from Storage.
Specification Reference: ADR-017 § Feature 3.
"""

import io
import unittest.mock
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw

from medishield.agents.kyc import KYCAgent, MemberRecord
from medishield.db.enums import AgentVerdict, DocumentType
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    OCRDocumentPayload,
    OCRPagePayload,
    OCRTextSpan,
    PolicyCoverageOutput,
    ProvenanceValue,
)
from medishield.domain.decision import (
    BusinessDecision,
    DecisionInputContext,
    DeterministicDecisionEngine,
    EscalationSubReason,
)
from medishield.domain.provenance import BBox
from medishield.intake.storage import (
    InMemoryStorageBackend,
    LocalFilesystemStorageBackend,
)
from medishield.ocr.forensics import (
    ELAVerdict,
    default_ela_engine,
)


def _create_synthetic_jpeg(quality: int = 90, size: tuple[int, int] = (256, 256)) -> bytes:
    """Generates a clean synthetic JPEG image."""
    img = Image.new("RGB", size, color=(240, 240, 240))
    draw = ImageDraw.Draw(img)
    for y in range(0, size[1], 16):
        draw.line([(0, y), (size[0], y)], fill=(200, 210, 220), width=1)
    draw.text((30, 50), "Standard Clean Hospital Receipt", fill=(30, 30, 30))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _create_tampered_synthetic_jpeg() -> bytes:
    """
    Generates a synthetic spliced/tampered JPEG.
    Base image is saved at low JPEG quality (quality=60), then a foreign patch
    is spliced into the image at high quality (quality=85).
    This creates the classic non-uniform ELA compression boundary signature.
    """
    base = Image.new("RGB", (320, 320), color=(240, 240, 240))
    d = ImageDraw.Draw(base)
    for y in range(0, 320, 8):
        d.line([(0, y), (320, y)], fill=(225, 230, 235))
    d.text((30, 30), "Original Invoice #1002", fill=(40, 40, 40))
    d.text((30, 60), "Amount: $500.00", fill=(40, 40, 40))

    b1 = io.BytesIO()
    base.save(b1, format="JPEG", quality=60)
    b1.seek(0)
    re_base = Image.open(b1).convert("RGB")

    patch = Image.new("RGB", (110, 40), color=(255, 255, 200))
    dp = ImageDraw.Draw(patch)
    dp.text((10, 10), "$95,000.00", fill=(200, 0, 0))
    re_base.paste(patch, (100, 140))

    patch2 = Image.new("RGB", (100, 35), color=(255, 250, 210))
    dp2 = ImageDraw.Draw(patch2)
    dp2.text((10, 10), "2026-10-18", fill=(180, 0, 0))
    re_base.paste(patch2, (100, 80))

    b2 = io.BytesIO()
    re_base.save(b2, format="JPEG", quality=85)
    return b2.getvalue()


def _make_dummy_ocr_doc(doc_id: str, member_id: str = "MEM-12345") -> OCRDocumentPayload:
    """Helper creating minimal grounded OCR payload for KYC identity match."""
    span = OCRTextSpan(
        span_id="span-1",
        page_number=1,
        text=f"Hospital Receipt Member ID: {member_id} DOB: 1985-05-20",
        confidence=0.98,
        bbox=BBox(x0=0.1, y0=0.1, x1=0.8, y1=0.2),
    )
    return OCRDocumentPayload(
        document_id=doc_id,
        total_pages=1,
        pages=[
            OCRPagePayload(
                page_number=1,
                raw_text=f"Hospital Receipt Member ID: {member_id} DOB: 1985-05-20",
                spans=[span],
            )
        ],
    )


class DummyMemberRepo:
    """Mock member repository providing valid test member records."""

    def find_member(self, member_id: str) -> MemberRecord | None:
        if member_id == "MEM-12345":
            return MemberRecord(
                member_id="MEM-12345",
                first_name="Alice",
                last_name="Smith",
                date_of_birth=date(1985, 5, 20),
            )
        return None


# ── Core Forensic Engine Tests ───────────────────────────────────────────────


def test_ela_non_jpeg_format() -> None:
    """Verifies non-JPEG files safely return NOT_APPLICABLE."""
    img = Image.new("RGB", (100, 100), color=(255, 0, 0))
    png_buf = io.BytesIO()
    img.save(png_buf, format="PNG")
    png_bytes = png_buf.getvalue()

    result = default_ela_engine.analyze_jpeg(png_bytes)
    assert result.status == ELAVerdict.NOT_APPLICABLE
    assert "not JPEG" in result.flagged_reasons[0]


def test_ela_corrupted_or_empty_bytes() -> None:
    """Verifies corrupted or empty bytes safely return ANALYSIS_FAILED without crashing."""
    empty_result = default_ela_engine.analyze_jpeg(b"")
    assert empty_result.status == ELAVerdict.ANALYSIS_FAILED

    corrupted_result = default_ela_engine.analyze_jpeg(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    assert corrupted_result.status in (ELAVerdict.ANALYSIS_FAILED, ELAVerdict.NOT_APPLICABLE)


def test_ela_clean_untampered_jpeg() -> None:
    """Verifies clean, authentically compressed JPEG passes without tamper anomalies."""
    clean_bytes = _create_synthetic_jpeg(quality=90)
    result = default_ela_engine.analyze_jpeg(clean_bytes)

    assert result.status == ELAVerdict.ANALYZED_NO_ANOMALY_DETECTED
    assert result.anomaly_score < 0.50
    assert result.suspicious_region_count <= 1
    assert result.ela_image_bytes is not None
    assert len(result.ela_image_bytes) > 0


def test_ela_tampered_jpeg_detection() -> None:
    """Verifies ELA detects non-uniform compression discrepancy in spliced JPEG."""
    tampered_bytes = _create_tampered_synthetic_jpeg()
    result = default_ela_engine.analyze_jpeg(tampered_bytes)

    assert result.status == ELAVerdict.ANOMALY_DETECTED
    assert result.anomaly_score >= 0.70
    assert result.suspicious_region_count >= 1
    assert len(result.flagged_reasons) > 0
    assert result.max_error_level > 20.0
    assert result.ela_image_bytes is not None


# ── KYC Storage Key Resolution and Byte Retrieval Tests ──────────────────────


def test_document_with_valid_object_key_retrieves_bytes() -> None:
    """Proves that a document with canonical object_key retrieves expected bytes and completes forensics."""
    storage = InMemoryStorageBackend(share_store=False)
    clean_bytes = _create_synthetic_jpeg(quality=90)
    canonical_key = "cases/CASE-VALID-KEY/raw/DOC-001_card.jpg"
    storage.put_bytes(canonical_key, clean_bytes)

    doc_payload = _make_dummy_ocr_doc("DOC-001")
    kyc = KYCAgent(member_repo=DummyMemberRepo())

    documents = [
        {
            "document_id": "DOC-001",
            "file_name": "card.jpg",
            "mime_type": "image/jpeg",
            "object_key": canonical_key,
        }
    ]

    output = kyc.execute(
        case_id="CASE-VALID-KEY",
        document_payloads=[doc_payload],
        storage_backend=storage,
        documents=documents,
    )

    assert output.forensic_status == "FORENSICS_COMPLETED"
    assert "FLAG_FORENSICS_RETRIEVAL_FAILED" not in output.flags
    assert output.tamper_detected is False
    assert output.verdict == AgentVerdict.PASS


def test_legacy_storage_key_backward_compatibility() -> None:
    """Proves legacy storage_key metadata remains supported for backward compatibility."""
    storage = InMemoryStorageBackend(share_store=False)
    clean_bytes = _create_synthetic_jpeg(quality=90)
    legacy_key = "legacy_storage/cases/CASE-LEGACY/DOC-LEGACY-01.jpg"
    storage.put_bytes(legacy_key, clean_bytes)

    doc_payload = _make_dummy_ocr_doc("DOC-LEGACY-01")
    kyc = KYCAgent(member_repo=DummyMemberRepo())

    documents = [
        {
            "document_id": "DOC-LEGACY-01",
            "file_name": "DOC-LEGACY-01.jpg",
            "mime_type": "image/jpeg",
            "storage_key": legacy_key,
        }
    ]

    output = kyc.execute(
        case_id="CASE-LEGACY",
        document_payloads=[doc_payload],
        storage_backend=storage,
        documents=documents,
    )

    assert output.forensic_status == "FORENSICS_COMPLETED"
    assert "FLAG_FORENSICS_RETRIEVAL_FAILED" not in output.flags


def test_constructed_canonical_fallback_path() -> None:
    """Proves canonical path cases/{case_id}/raw/{doc_id}_{filename} is resolved when object_key is omitted."""
    storage = InMemoryStorageBackend(share_store=False)
    clean_bytes = _create_synthetic_jpeg(quality=90)
    constructed_key = "cases/CASE-FALLBACK/raw/DOC-FB-01_member_card.jpg"
    storage.put_bytes(constructed_key, clean_bytes)

    doc_payload = _make_dummy_ocr_doc("DOC-FB-01")
    kyc = KYCAgent(member_repo=DummyMemberRepo())

    documents = [
        {
            "document_id": "DOC-FB-01",
            "file_name": "member_card.jpg",
            "mime_type": "image/jpeg",
        }
    ]

    output = kyc.execute(
        case_id="CASE-FALLBACK",
        document_payloads=[doc_payload],
        storage_backend=storage,
        documents=documents,
    )

    assert output.forensic_status == "FORENSICS_COMPLETED"
    assert "FLAG_FORENSICS_RETRIEVAL_FAILED" not in output.flags


def test_missing_object_produces_explicit_retrieval_failure() -> None:
    """Proves a missing storage object produces IMAGE_RETRIEVAL_FAILED without silent pass."""
    storage = InMemoryStorageBackend(share_store=False)
    missing_key = "cases/CASE-MISSING/raw/DOC-MISSING-01_card.jpg"

    doc_payload = _make_dummy_ocr_doc("DOC-MISSING-01")
    kyc = KYCAgent(member_repo=DummyMemberRepo())

    documents = [
        {
            "document_id": "DOC-MISSING-01",
            "file_name": "card.jpg",
            "mime_type": "image/jpeg",
            "object_key": missing_key,
        }
    ]

    output = kyc.execute(
        case_id="CASE-MISSING",
        document_payloads=[doc_payload],
        storage_backend=storage,
        documents=documents,
    )

    assert output.forensic_status == "IMAGE_RETRIEVAL_FAILED"
    assert "FLAG_FORENSICS_RETRIEVAL_FAILED" in output.flags
    assert any(s.signal_type == "FORENSIC_RETRIEVAL_FAILED" for s in output.tamper_signals)
    assert output.tamper_detected is False


def test_storage_exception_handled_and_observable() -> None:
    """Proves storage exceptions during get_bytes are caught, logged, and surfaced in flags/signals."""
    class FailingStorage:
        def exists(self, key: str) -> bool:
            return True

        def get_bytes(self, key: str) -> bytes:
            raise OSError("S3 connection reset by peer during chunk streaming")

    doc_payload = _make_dummy_ocr_doc("DOC-ERR-01")
    kyc = KYCAgent(member_repo=DummyMemberRepo())

    documents = [
        {
            "document_id": "DOC-ERR-01",
            "file_name": "card.jpg",
            "mime_type": "image/jpeg",
            "object_key": "cases/CASE-ERR/raw/DOC-ERR-01_card.jpg",
        }
    ]

    output = kyc.execute(
        case_id="CASE-ERR",
        document_payloads=[doc_payload],
        storage_backend=FailingStorage(),
        documents=documents,
    )

    assert output.forensic_status == "IMAGE_RETRIEVAL_FAILED"
    assert "FLAG_FORENSICS_RETRIEVAL_FAILED" in output.flags
    assert any(s.signal_type == "FORENSIC_RETRIEVAL_FAILED" for s in output.tamper_signals)
    assert output.tamper_detected is False


def test_retrieved_bytes_actually_passed_into_ela_engine() -> None:
    """Proves that retrieved bytes from storage are forwarded to the ELA engine unmodified."""
    storage = InMemoryStorageBackend(share_store=False)
    clean_bytes = _create_synthetic_jpeg(quality=90)
    key = "cases/CASE-BYTES/raw/DOC-BYTES-01_card.jpg"
    storage.put_bytes(key, clean_bytes)

    doc_payload = _make_dummy_ocr_doc("DOC-BYTES-01")
    kyc = KYCAgent(member_repo=DummyMemberRepo())

    documents = [
        {
            "document_id": "DOC-BYTES-01",
            "file_name": "card.jpg",
            "mime_type": "image/jpeg",
            "object_key": key,
        }
    ]

    with unittest.mock.patch.object(
        default_ela_engine, "analyze_jpeg", wraps=default_ela_engine.analyze_jpeg
    ) as mock_ela:
        output = kyc.execute(
            case_id="CASE-BYTES",
            document_payloads=[doc_payload],
            storage_backend=storage,
            documents=documents,
        )

        mock_ela.assert_called_once()
        called_bytes = mock_ela.call_args[0][0]
        assert called_bytes == clean_bytes
        assert output.forensic_status == "FORENSICS_COMPLETED"


def test_retrieved_tampered_image_produces_ela_anomaly_and_conflict_verdict() -> None:
    """Proves successfully retrieved tampered image triggers ELA anomaly and CONFLICT verdict (not auto-reject)."""
    storage = InMemoryStorageBackend(share_store=False)
    tampered_bytes = _create_tampered_synthetic_jpeg()
    key = "cases/CASE-TAMPER-E2E/raw/DOC-TAMPER-01_bill.jpg"
    storage.put_bytes(key, tampered_bytes)

    doc_payload = _make_dummy_ocr_doc("DOC-TAMPER-01")
    kyc = KYCAgent(member_repo=DummyMemberRepo())

    documents = [
        {
            "document_id": "DOC-TAMPER-01",
            "file_name": "bill.jpg",
            "mime_type": "image/jpeg",
            "object_key": key,
        }
    ]

    output = kyc.execute(
        case_id="CASE-TAMPER-E2E",
        document_payloads=[doc_payload],
        storage_backend=storage,
        documents=documents,
    )

    assert output.forensic_status == "ANOMALY_DETECTED"
    assert output.tamper_detected is True
    assert output.tamper_score >= 0.70
    assert "FLAG_IMAGE_TAMPER_SUSPECTED" in output.flags
    assert any(s.signal_type == "ELA_ANOMALY" for s in output.tamper_signals)
    flagged = [s for s in output.tamper_signals if s.signal_type == "ELA_ANOMALY"]
    assert all(s.document_id == "DOC-TAMPER-01" for s in flagged)
    assert all(s.document_name == "bill.jpg" for s in flagged)
    # Requirement 7: ELA anomaly alone must route to CONFLICT (review), NOT automatic FAIL
    assert output.verdict == AgentVerdict.CONFLICT


def test_clean_image_control_does_not_become_tamper_alert() -> None:
    """Proves authentic control image does not trigger tamper flags or conflict verdict."""
    storage = InMemoryStorageBackend(share_store=False)
    clean_bytes = _create_synthetic_jpeg(quality=90)
    key = "cases/CASE-CLEAN/raw/DOC-CLEAN-01_card.jpg"
    storage.put_bytes(key, clean_bytes)

    doc_payload = _make_dummy_ocr_doc("DOC-CLEAN-01")
    kyc = KYCAgent(member_repo=DummyMemberRepo())

    documents = [
        {
            "document_id": "DOC-CLEAN-01",
            "file_name": "card.jpg",
            "mime_type": "image/jpeg",
            "object_key": key,
        }
    ]

    output = kyc.execute(
        case_id="CASE-CLEAN",
        document_payloads=[doc_payload],
        storage_backend=storage,
        documents=documents,
    )

    assert output.forensic_status == "FORENSICS_COMPLETED"
    assert output.tamper_detected is False
    assert output.tamper_score < 0.30
    assert "FLAG_IMAGE_TAMPER_SUSPECTED" not in output.flags
    assert output.verdict == AgentVerdict.PASS


def test_no_supported_image_available_forensic_status() -> None:
    """Proves cases with only non-image documents explicitly record NO_SUPPORTED_IMAGE_AVAILABLE."""
    storage = InMemoryStorageBackend(share_store=False)
    doc_payload = _make_dummy_ocr_doc("DOC-PDF-01")
    kyc = KYCAgent(member_repo=DummyMemberRepo())

    documents = [
        {
            "document_id": "DOC-PDF-01",
            "file_name": "claim.pdf",
            "mime_type": "application/pdf",
            "object_key": "cases/CASE-PDF/raw/DOC-PDF-01_claim.pdf",
        }
    ]

    output = kyc.execute(
        case_id="CASE-PDF",
        document_payloads=[doc_payload],
        storage_backend=storage,
        documents=documents,
    )

    assert output.forensic_status == "NO_SUPPORTED_IMAGE_AVAILABLE"
    assert "FLAG_FORENSICS_RETRIEVAL_FAILED" not in output.flags
    assert output.tamper_detected is False
    assert output.verdict == AgentVerdict.PASS


def test_integration_with_actual_local_filesystem_storage(tmp_path: Path) -> None:
    """Proves ELA image retrieval against the actual LocalFilesystemStorageBackend used by MediShield."""
    real_storage = LocalFilesystemStorageBackend(base_directory=tmp_path)
    tampered_bytes = _create_tampered_synthetic_jpeg()

    case_id = "CASE-REAL-FS"
    doc_id = "DOC-FS-01"
    file_name = "patient_id.jpg"
    object_key = f"cases/{case_id}/raw/{doc_id}_{file_name}"

    real_storage.put_bytes(object_key, tampered_bytes)
    assert real_storage.exists(object_key)

    doc_payload = _make_dummy_ocr_doc(doc_id)
    kyc = KYCAgent(member_repo=DummyMemberRepo())

    documents = [
        {
            "document_id": doc_id,
            "file_name": file_name,
            "mime_type": "image/jpeg",
            "object_key": object_key,
        }
    ]

    output = kyc.execute(
        case_id=case_id,
        document_payloads=[doc_payload],
        storage_backend=real_storage,
        documents=documents,
    )

    assert output.forensic_status == "ANOMALY_DETECTED"
    assert output.tamper_detected is True
    assert output.tamper_score >= 0.70
    assert output.verdict == AgentVerdict.CONFLICT
    assert any(s.signal_type == "ELA_ANOMALY" for s in output.tamper_signals)


def test_decision_engine_routes_ela_anomaly_to_escalation_review() -> None:
    """Verifies Decision Engine Rule P3 routes KYC CONFLICT (from ELA anomaly) to ESCALATE (human review)."""
    storage = InMemoryStorageBackend(share_store=False)
    tampered_bytes = _create_tampered_synthetic_jpeg()
    key = "cases/CASE-DEC-TEST/raw/DOC-01_id.jpg"
    storage.put_bytes(key, tampered_bytes)

    doc_payload = _make_dummy_ocr_doc("DOC-01")
    kyc = KYCAgent(member_repo=DummyMemberRepo())

    documents = [
        {
            "document_id": "DOC-01",
            "file_name": "id.jpg",
            "mime_type": "image/jpeg",
            "object_key": key,
        }
    ]

    kyc_output = kyc.execute(
        case_id="CASE-DEC-TEST",
        document_payloads=[doc_payload],
        storage_backend=storage,
        documents=documents,
    )

    assert kyc_output.verdict == AgentVerdict.CONFLICT

    context = DecisionInputContext(
        case_id="CASE-DEC-TEST",
        execution_id="exec-dec-test",
        kyc_output=kyc_output,
        claims_output=ClaimsAgentOutput(
            agent_name="claims_agent",
            verdict=AgentVerdict.PASS,
            confidence=0.95,
            claim_amount=ProvenanceValue(value=100.0, confidence=0.99),
            service_date=ProvenanceValue(value="2026-08-01", confidence=0.99),
            provider_npi=ProvenanceValue(value="1234567890", confidence=0.95),
            schema_valid=True,
            line_items_sum_valid=True,
            calculated_lines_sum=100.0,
            latency_ms=10,
        ),
        policy_output=PolicyCoverageOutput(
            agent_name="policy_coverage_rag_agent",
            verdict=AgentVerdict.PASS,
            confidence=0.90,
            covered=True,
            coverage_percentage=80.0,
            reasoning_summary="Eligible covered service.",
            latency_ms=10,
        ),
        present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
    )

    decision_output = DeterministicDecisionEngine.adjudicate(context)

    # Must ESCALATE for human review / security review, NEVER automatically REJECT
    assert decision_output.decision == BusinessDecision.ESCALATE
    assert decision_output.decision != BusinessDecision.REJECT
    assert decision_output.escalation_sub_reason in (
        EscalationSubReason.SECURITY_REVIEW,
        EscalationSubReason.HUMAN_REVIEW,
    )
