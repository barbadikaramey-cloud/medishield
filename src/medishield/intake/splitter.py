"""Multi-page PDF splitter and document page decomposition engine (Spec 03 § 3)."""

import re
from typing import Final

from pydantic import BaseModel, Field

from medishield.intake.storage import generate_page_object_key, generate_raw_object_key
from medishield.intake.validation import MAX_PAGE_COUNT_PER_PDF, FileValidationError

DEFAULT_PAGE_WIDTH_PX: Final[int] = 1700   # 200 DPI 8.5" width
DEFAULT_PAGE_HEIGHT_PX: Final[int] = 2200  # 200 DPI 11.0" height


class PageCountLimitExceededError(FileValidationError):
    """Raised when a PDF exceeds the 50-page maximum limit (Spec 03 § 2.1)."""


class SplitPageRecord(BaseModel):
    """Metadata for an individual decomposed page within a document."""

    page_number: int = Field(..., ge=1, description="1-indexed physical page number")
    object_key: str = Field(..., description="Canonical S3 key for rasterized page image")
    width_px: int = Field(default=DEFAULT_PAGE_WIDTH_PX, ge=1)
    height_px: int = Field(default=DEFAULT_PAGE_HEIGHT_PX, ge=1)


class SplitDocumentResult(BaseModel):
    """Aggregate result of decomposing an intake document into physical page records."""

    case_id: str
    document_id: str
    file_name: str
    mime_type: str
    total_pages: int = Field(..., ge=1)
    raw_object_key: str
    pages: list[SplitPageRecord] = Field(default_factory=list)


def count_pdf_pages_from_bytes(content: bytes) -> int:
    """
    Deterministically determines page count by inspecting PDF dictionary structures.
    Uses regex scanning on standard /Type /Page objects and /Count tags without C-extension dependencies.
    """
    # 1. Look for top-level Pages dictionary count: e.g. /Type /Pages ... /Count 5
    count_matches = re.findall(rb"/Type\s*/Pages.*?/Count\s+(\d+)", content, re.DOTALL)
    if count_matches:
        try:
            counts = [int(m) for m in count_matches]
            return max(counts)
        except ValueError:
            pass

    # 2. Count individual /Type /Page (excluding /Type /Pages)
    page_matches = re.findall(rb"/Type\s*/Page\b", content)
    if page_matches:
        return len(page_matches)

    # 3. Default fallback for valid but non-standard minimal PDFs
    return 1


def split_document_pages(
    case_id: str,
    document_id: str,
    file_name: str,
    mime_type: str,
    content: bytes,
) -> SplitDocumentResult:
    """
    Decomposes an uploaded document into discrete, 1-indexed page records.
    - PDFs are inspected and split into N pages (enforcing the 50-page limit).
    - Image files (JPEG, PNG, TIFF) are registered as single-page documents.
    """
    if mime_type == "application/pdf":
        page_count = count_pdf_pages_from_bytes(content)
        if page_count > MAX_PAGE_COUNT_PER_PDF:
            raise PageCountLimitExceededError(
                f"PDF '{file_name}' contains {page_count} pages, exceeding the "
                f"maximum allowed limit of {MAX_PAGE_COUNT_PER_PDF} pages per document"
            )
    else:
        page_count = 1

    raw_key = generate_raw_object_key(case_id, document_id, file_name)
    pages: list[SplitPageRecord] = []

    for page_num in range(1, page_count + 1):
        page_key = generate_page_object_key(case_id, document_id, page_num)
        pages.append(
            SplitPageRecord(
                page_number=page_num,
                object_key=page_key,
                width_px=DEFAULT_PAGE_WIDTH_PX,
                height_px=DEFAULT_PAGE_HEIGHT_PX,
            )
        )

    return SplitDocumentResult(
        case_id=case_id,
        document_id=document_id,
        file_name=file_name,
        mime_type=mime_type,
        total_pages=page_count,
        raw_object_key=raw_key,
        pages=pages,
    )
