"""Unit tests for the Multi-Document Intake, S3 PDF Splitting & Case Completeness Engine (FEAT-006, Spec 03)."""

import tempfile
from pathlib import Path

import pytest

from medishield.db.enums import (
    AgentVerdict,
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
    WorkflowState,
)
from medishield.intake.completeness import (
    ClassifiedDocumentItem,
    evaluate_case_completeness,
)
from medishield.intake.splitter import (
    PageCountLimitExceededError,
    count_pdf_pages_from_bytes,
    split_document_pages,
)
from medishield.intake.storage import (
    InMemoryStorageBackend,
    LocalFilesystemStorageBackend,
    build_s3_uri,
    generate_ocr_object_key,
    generate_page_object_key,
    generate_raw_object_key,
    sanitize_filename,
)
from medishield.intake.validation import (
    MAX_CASE_AGGREGATE_SIZE_BYTES,
    MAX_FILE_SIZE_BYTES,
    AggregateSizeLimitExceededError,
    FileSizeLimitExceededError,
    SecurityVulnerabilityError,
    UnsupportedMimeTypeError,
    detect_mime_type_from_magic_bytes,
    validate_file_upload,
)

# ── Sample Binary Headers ─────────────────────────────────────────────────────
PDF_SAMPLE = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page >>\nendobj\n%%EOF"
JPEG_SAMPLE = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb"
PNG_SAMPLE = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
TIFF_LE_SAMPLE = b"II*\x00\x08\x00\x00\x00"
TIFF_BE_SAMPLE = b"MM\x00*\x00\x00\x00\x08"


# ── 1. Magic Byte & Upload Validation Tests ───────────────────────────────────

def test_detect_mime_type_valid_signatures():
    """Identifies PDF, JPEG, PNG, TIFF from raw magic bytes (Spec 03 § 2.1)."""
    assert detect_mime_type_from_magic_bytes(PDF_SAMPLE) == "application/pdf"
    assert detect_mime_type_from_magic_bytes(JPEG_SAMPLE) == "image/jpeg"
    assert detect_mime_type_from_magic_bytes(PNG_SAMPLE) == "image/png"
    assert detect_mime_type_from_magic_bytes(TIFF_LE_SAMPLE) == "image/tiff"
    assert detect_mime_type_from_magic_bytes(TIFF_BE_SAMPLE) == "image/tiff"


def test_detect_mime_type_unauthorized_formats():
    """Rejects unauthorized formats like plain text or zip files."""
    assert detect_mime_type_from_magic_bytes(b'{"json": "payload"}') is None
    assert detect_mime_type_from_magic_bytes(b"PK\x03\x04zipfile") is None
    assert detect_mime_type_from_magic_bytes(b"short") is None


def test_validate_file_upload_clean():
    """Valid upload passes and returns checksum and verified MIME type."""
    upload = validate_file_upload(
        file_name="invoice.pdf",
        content=PDF_SAMPLE,
    )
    assert upload.is_valid is True
    assert upload.mime_type == "application/pdf"
    assert upload.size_bytes == len(PDF_SAMPLE)
    assert len(upload.checksum_sha256) == 64  # SHA-256 hex digest length


def test_validate_file_upload_rejects_spoofed_extension():
    """Renaming an invalid file to .pdf still fails via magic byte inspection."""
    fake_pdf = b"Plain text purporting to be a PDF"
    with pytest.raises(UnsupportedMimeTypeError):
        validate_file_upload("spoofed.pdf", fake_pdf)


def test_individual_file_size_limit():
    """Rejects files exceeding 25 MB limit."""
    oversized_bytes = b"%PDF-" + b"0" * (MAX_FILE_SIZE_BYTES + 10)
    with pytest.raises(FileSizeLimitExceededError):
        validate_file_upload("giant.pdf", oversized_bytes)


def test_aggregate_case_size_limit():
    """Rejects submissions when cumulative bundle size exceeds 100 MB."""
    incoming_size = 5 * 1024 * 1024
    already_uploaded = MAX_CASE_AGGREGATE_SIZE_BYTES - (1 * 1024 * 1024)

    with pytest.raises(AggregateSizeLimitExceededError):
        validate_file_upload(
            file_name="burst.pdf",
            content=PDF_SAMPLE + (b"0" * incoming_size),
            current_aggregate_bytes=already_uploaded,
        )


def test_reject_executable_binary_payloads():
    """Rejects Windows PE (MZ) or Linux ELF executable signatures."""
    windows_exe = b"MZ\x90\x00\x03\x00\x00\x00"
    with pytest.raises(SecurityVulnerabilityError):
        validate_file_upload("installer.exe", windows_exe)

    linux_elf = b"\x7fELF\x02\x01\x01\x00"
    with pytest.raises(SecurityVulnerabilityError):
        validate_file_upload("script.bin", linux_elf)


def test_reject_pdf_embedded_active_scripts():
    """Rejects PDFs containing /JavaScript or /Launch active exploit streams."""
    malicious_js_pdf = b"%PDF-1.4\n<< /Type /Action /S /JavaScript /JS (app.alert('xss')) >>"
    with pytest.raises(SecurityVulnerabilityError):
        validate_file_upload("exploit.pdf", malicious_js_pdf)

    malicious_launch_pdf = b"%PDF-1.4\n<< /Type /Action /S /Launch /F (calc.exe) >>"
    with pytest.raises(SecurityVulnerabilityError):
        validate_file_upload("launch.pdf", malicious_launch_pdf)


# ── 2. Object Storage Layout Tests ────────────────────────────────────────────

def test_canonical_s3_key_generation():
    """Verifies canonical S3 key formatting (Spec 03 § 5.2)."""
    raw_key = generate_raw_object_key("CASE-101", "DOC-001", "claim form.pdf")
    assert raw_key == "cases/CASE-101/raw/DOC-001_claim_form.pdf"

    page_key = generate_page_object_key("CASE-101", "DOC-001", 3)
    assert page_key == "cases/CASE-101/pages/DOC-001_p3.png"

    ocr_key = generate_ocr_object_key("CASE-101", "DOC-001")
    assert ocr_key == "ocr/CASE-101/DOC-001.json"

    s3_uri = build_s3_uri("medishield-storage", ocr_key)
    assert s3_uri == "s3://medishield-storage/ocr/CASE-101/DOC-001.json"


def test_sanitize_filename_traversal():
    """Strips path traversal attempts from uploaded filenames."""
    assert sanitize_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert sanitize_filename("..\\..\\windows\\win.ini") == "win.ini"


def test_in_memory_storage_backend():
    """Verifies put, get, exists, delete on in-memory storage."""
    storage = InMemoryStorageBackend(bucket_name="test-bucket")
    key = "cases/C1/raw/D1_test.pdf"
    payload = b"test-payload-bytes"

    uri = storage.put_bytes(key, payload, "application/pdf")
    assert uri == "s3://test-bucket/cases/C1/raw/D1_test.pdf"
    assert storage.exists(key) is True
    assert storage.get_bytes(key) == payload

    storage.delete(key)
    assert storage.exists(key) is False


def test_local_filesystem_storage_backend():
    """Verifies filesystem storage driver simulation."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage = LocalFilesystemStorageBackend(base_directory=Path(tmp_dir), bucket_name="local-bucket")
        key = "cases/C2/pages/D2_p1.png"
        payload = b"png-raster-bytes"

        uri = storage.put_bytes(key, payload, "image/png")
        assert uri == "s3://local-bucket/cases/C2/pages/D2_p1.png"
        assert storage.exists(key) is True
        assert storage.get_bytes(key) == payload


# ── 3. Document Splitting Tests ───────────────────────────────────────────────

def test_single_page_image_split():
    """Image uploads decompose into a single page record."""
    result = split_document_pages(
        case_id="CASE-1",
        document_id="DOC-1",
        file_name="id_card.jpg",
        mime_type="image/jpeg",
        content=JPEG_SAMPLE,
    )
    assert result.total_pages == 1
    assert len(result.pages) == 1
    assert result.pages[0].page_number == 1
    assert result.pages[0].object_key == "cases/CASE-1/pages/DOC-1_p1.png"


def test_multi_page_pdf_page_counting():
    """Counts PDF pages accurately from byte structures."""
    # Multi-page PDF mock with /Count 3
    multi_page_pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Pages /Kids [2 0 R 3 0 R 4 0 R] /Count 3 >> endobj\n"
        b"2 0 obj << /Type /Page >> endobj\n"
        b"3 0 obj << /Type /Page >> endobj\n"
        b"4 0 obj << /Type /Page >> endobj\n"
        b"%%EOF"
    )
    pages = count_pdf_pages_from_bytes(multi_page_pdf)
    assert pages == 3

    split_res = split_document_pages(
        case_id="CASE-9",
        document_id="DOC-9",
        file_name="bundle.pdf",
        mime_type="application/pdf",
        content=multi_page_pdf,
    )
    assert split_res.total_pages == 3
    assert len(split_res.pages) == 3
    assert [p.page_number for p in split_res.pages] == [1, 2, 3]


def test_pdf_page_count_limit_exceeded():
    """PDFs exceeding 50 pages raise PageCountLimitExceededError."""
    # Simulate a 52-page PDF
    large_pdf = b"%PDF-1.4\n<< /Type /Pages /Count 52 >>"
    with pytest.raises(PageCountLimitExceededError):
        split_document_pages(
            case_id="CASE-X",
            document_id="DOC-X",
            file_name="huge_book.pdf",
            mime_type="application/pdf",
            content=large_pdf,
        )


# ── 4. Case Completeness Engine Tests ─────────────────────────────────────────

def test_case_completeness_clean_reimbursement():
    """Standard claim with CLAIM_FORM and ID_DOCUMENT at >= 0.70 passes completeness."""
    docs = [
        ClassifiedDocumentItem(document_id="D1", doc_type=DocumentType.CLAIM_FORM, classification_confidence=0.95),
        ClassifiedDocumentItem(document_id="D2", doc_type=DocumentType.ID_DOCUMENT, classification_confidence=0.91),
    ]

    res = evaluate_case_completeness(docs, claim_type="STANDARD_REIMBURSEMENT_CLAIM")
    assert res.is_complete is True
    assert res.verdict == AgentVerdict.PASS
    assert res.recommended_state == WorkflowState.ANALYZING
    assert res.recommended_decision == BusinessDecision.APPROVE
    assert len(res.missing_types) == 0


def test_case_completeness_missing_mandatory_id():
    """Standard claim missing ID_DOCUMENT routes to AWAITING_INFO with MISSING_INFORMATION."""
    docs = [
        ClassifiedDocumentItem(document_id="D1", doc_type=DocumentType.CLAIM_FORM, classification_confidence=0.95),
        ClassifiedDocumentItem(document_id="D2", doc_type=DocumentType.DISCHARGE_SUMMARY, classification_confidence=0.88),
    ]

    res = evaluate_case_completeness(docs, claim_type="STANDARD_REIMBURSEMENT_CLAIM")
    assert res.is_complete is False
    assert res.verdict == AgentVerdict.INDETERMINATE
    assert res.recommended_state == WorkflowState.AWAITING_INFO
    assert res.recommended_decision == BusinessDecision.ESCALATE
    assert res.recommended_sub_reason == EscalationSubReason.MISSING_INFORMATION
    assert DocumentType.ID_DOCUMENT in res.missing_types


def test_case_completeness_low_confidence_excluded():
    """Documents classified with confidence < 0.70 are excluded from satisfying completeness."""
    docs = [
        ClassifiedDocumentItem(document_id="D1", doc_type=DocumentType.CLAIM_FORM, classification_confidence=0.95),
        # ID present but low classification confidence (0.62 < 0.70)
        ClassifiedDocumentItem(document_id="D2", doc_type=DocumentType.ID_DOCUMENT, classification_confidence=0.62),
    ]

    res = evaluate_case_completeness(docs, claim_type="STANDARD_REIMBURSEMENT_CLAIM")
    assert res.is_complete is False
    assert DocumentType.ID_DOCUMENT in res.missing_types


def test_case_completeness_cashless_preauth():
    """Hospital cashless preauth requires CLAIM_FORM, ID_DOCUMENT, and DISCHARGE_SUMMARY."""
    docs = [
        ClassifiedDocumentItem(document_id="D1", doc_type=DocumentType.CLAIM_FORM, classification_confidence=0.92),
        ClassifiedDocumentItem(document_id="D2", doc_type=DocumentType.ID_DOCUMENT, classification_confidence=0.90),
    ]

    # Lacks DISCHARGE_SUMMARY
    res = evaluate_case_completeness(docs, claim_type="HOSPITAL_CASHLESS_PREAUTH")
    assert res.is_complete is False
    assert DocumentType.DISCHARGE_SUMMARY in res.missing_types
