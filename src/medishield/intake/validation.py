"""Intake file validation, magic byte inspection, and security checks (Spec 03 § 2)."""

import hashlib
import re
from typing import Final

from pydantic import BaseModel, Field

# ── Validation Limits (Canonical Spec 03 § 2.1) ───────────────────────────────
MAX_FILE_SIZE_BYTES: Final[int] = 25 * 1024 * 1024           # 25 MB per document
MAX_CASE_AGGREGATE_SIZE_BYTES: Final[int] = 100 * 1024 * 1024  # 100 MB per submission
MAX_CASE_AGGREGATE_BYTES: Final[int] = MAX_CASE_AGGREGATE_SIZE_BYTES
MAX_PAGE_COUNT_PER_PDF: Final[int] = 50                      # 50 pages per PDF

# Allowed MIME types
ALLOWED_MIME_TYPES: Final[set[str]] = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/tiff",
}

# Dangerous PDF tokens that indicate potential security exploits or macros
DANGEROUS_PDF_TOKENS: Final[list[bytes]] = [
    b"/JavaScript",
    b"/JS",
    b"/Launch",
    b"/SubmitForm",
    b"/ImportData",
]


class FileValidationError(ValueError):
    """Base error for document upload validation failures."""


class FileSizeLimitExceededError(FileValidationError):
    """Raised when an uploaded file exceeds the 25 MB limit."""


class AggregateSizeLimitExceededError(FileValidationError):
    """Raised when the total submission bundle exceeds the 100 MB limit."""


class UnsupportedMimeTypeError(FileValidationError):
    """Raised when file magic bytes do not match approved medical document types."""


class SecurityVulnerabilityError(FileValidationError):
    """Raised when static inspection detects executable payloads, scripts, or macros."""


class ValidatedUpload(BaseModel):
    """Immutable verified upload descriptor with cryptographic checksum."""

    file_name: str
    mime_type: str
    size_bytes: int = Field(..., ge=0)
    checksum_sha256: str = Field(..., description="In-stream SHA-256 hash")
    is_valid: bool = True


def detect_mime_type_from_magic_bytes(header_bytes: bytes) -> str | None:
    """
    Deterministically identifies file MIME type by inspecting magic header bytes.
    Never trusts the user-supplied file extension or HTTP Content-Type.
    """
    if len(header_bytes) < 4:
        return None

    # 1. PDF Signature: %PDF-
    if header_bytes.startswith(b"%PDF-"):
        return "application/pdf"

    # 2. JPEG Signature: \xFF\xD8\xFF
    if header_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    # 3. PNG Signature: \x89PNG\r\n\x1a\n
    if header_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    # 4. TIFF Signature: II*\x00 (Little-endian) or MM\x00* (Big-endian)
    if header_bytes.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"

    return None


def inspect_for_malicious_content(content: bytes, mime_type: str | None = None) -> None:
    """
    Inspects file bytes for executable signatures, macros, and embedded scripts.
    Raises SecurityVulnerabilityError if suspicious constructs are found.
    """
    # 1. Check for executable headers (Windows MZ or Linux ELF)
    if content.startswith((b"MZ", b"\x7fELF")):
        raise SecurityVulnerabilityError(
            "Security Violation: Executable binary header detected in uploaded file"
        )

    # 2. Check for PDF-embedded active content / scripts
    if mime_type == "application/pdf":
        for token in DANGEROUS_PDF_TOKENS:
            if re.search(re.escape(token), content, re.IGNORECASE):
                raise SecurityVulnerabilityError(
                    f"Security Violation: Prohibited active construct '{token.decode('ascii', errors='ignore')}' "
                    "detected in PDF document"
                )


def validate_file_upload(
    file_name: str,
    content: bytes,
    current_aggregate_bytes: int = 0,
) -> ValidatedUpload:
    """
    Validates a file upload according to Canonical Spec 03 § 2:
    - Enforces 25 MB individual and 100 MB aggregate size limits.
    - Inspects magic bytes to identify true MIME type.
    - Runs static security inspection against macros and executable streams.
    - Computes cryptographic SHA-256 checksum.
    """
    file_size = len(content)

    # 1. Check individual file size limit (25 MB)
    if file_size > MAX_FILE_SIZE_BYTES:
        max_mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
        actual_mb = file_size / (1024 * 1024)
        raise FileSizeLimitExceededError(
            f"File '{file_name}' exceeds max allowed size ({actual_mb:.2f} MB > {max_mb:.0f} MB)"
        )

    # 2. Check aggregate case size limit (100 MB)
    total_size = current_aggregate_bytes + file_size
    if total_size > MAX_CASE_AGGREGATE_SIZE_BYTES:
        max_agg_mb = MAX_CASE_AGGREGATE_SIZE_BYTES / (1024 * 1024)
        actual_agg_mb = total_size / (1024 * 1024)
        raise AggregateSizeLimitExceededError(
            f"Submission bundle exceeds max aggregate size ({actual_agg_mb:.2f} MB > {max_agg_mb:.0f} MB)"
        )

    # 3. Static security inspection for executable headers before MIME check
    if content.startswith((b"MZ", b"\x7fELF")):
        raise SecurityVulnerabilityError(
            "Security Violation: Executable binary header detected in uploaded file"
        )

    # 4. Magic byte inspection
    mime_type = detect_mime_type_from_magic_bytes(content[:16])
    if mime_type is None or mime_type not in ALLOWED_MIME_TYPES:
        raise UnsupportedMimeTypeError(
            f"File '{file_name}' has invalid or unauthorized MIME type. "
            f"Allowed types: {', '.join(sorted(ALLOWED_MIME_TYPES))}"
        )

    # 5. Security & active script inspection for document types
    inspect_for_malicious_content(content, mime_type)

    # 6. Cryptographic SHA-256 calculation
    sha256_hash = hashlib.sha256(content).hexdigest()

    return ValidatedUpload(
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=file_size,
        checksum_sha256=sha256_hash,
        is_valid=True,
    )
