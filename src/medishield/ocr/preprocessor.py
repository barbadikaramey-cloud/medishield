"""OCR Preprocessor Engine, text token extraction, and span-level provenance (Spec 03 § 5, ADR-006)."""

import io
import re
import time
import zlib
from datetime import UTC, datetime
from typing import Any

from PIL import Image

try:
    from rapidocr_onnxruntime import RapidOCR
    _RAPID_OCR_AVAILABLE = True
except ImportError:
    RapidOCR = None  # type: ignore[assignment,misc]
    _RAPID_OCR_AVAILABLE = False

from medishield.core.tracing import trace_span
from medishield.db.enums import AgentVerdict
from medishield.domain.contracts import (
    OCRBoundingBox,
    OCRDocumentPayload,
    OCRPagePayload,
    OCRPreprocessorOutput,
    OCRTextSpan,
)
from medishield.intake.storage import StorageBackend

_RAPID_OCR_ENGINE: Any = None


def get_rapid_ocr_engine() -> Any:
    """Lazy-initializes and caches a singleton RapidOCR instance."""
    global _RAPID_OCR_ENGINE
    if _RAPID_OCR_ENGINE is None and _RAPID_OCR_AVAILABLE:
        try:
            _RAPID_OCR_ENGINE = RapidOCR()
        except Exception:  # noqa: BLE001
            _RAPID_OCR_ENGINE = False
    return _RAPID_OCR_ENGINE if _RAPID_OCR_ENGINE is not False else None


RE_FORM_BOX_HEADER = re.compile(
    r"^(?:\d{1,2}[a-zA-Z]?\s*[-:.]?\s*)?"
    r"(?:patient(?:'s)?\s*name|patient(?:'s)?\s*address|insured(?:'s)?\s*(?:name|unique\s*id|i\.?d\.?)|member\s*id|"
    r"birthdate|dob|date\s*of\s*birth|sex|statement\s*covers\s*period|admit\s*type|admission\s*date|discharge\s*date|"
    r"total\s*charges?|page\s*totals?|estimated\s*amt\s*due|amount\s*claimed|total\s*amount|"
    r"principal\s*diagnosis|admitting\s*diagnosis|principal\s*procedure|cpt|hcpcs|"
    r"billing\s*provider|billing\s*npi|attending\s*physician|physician\s*signature|"
    r"medical\s*record\s*no|group\s*number|npi|dln|driver(?:'s)?\s*license|name|address|dob|exp(?:iration)?)",
    re.IGNORECASE,
)


def extract_geometric_key_value_pairs(
    spans: list[OCRTextSpan],
    page_num: int = 1,
) -> tuple[list[OCRTextSpan], list[dict[str, Any]]]:
    """
    Extracts key-value pairs from OCR text spans using spatial and geometric layout.
    Generates -k and -v spans and structured dictionary records.
    """
    additional_spans: list[OCRTextSpan] = []
    key_value_pairs: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for i, s in enumerate(spans):
        if s.span_id.endswith("-k") or s.span_id.endswith("-v"):
            continue

        text = s.text.strip()
        if not text:
            continue

        # 1. Inline Colon Separation
        if ":" in text:
            parts = text.split(":", 1)
            k_text = parts[0].strip()
            v_text = parts[1].strip()
            if k_text and v_text:
                clean_k = re.sub(r"^\d{1,2}(?:[a-z](?=\s|[-:.]|$))?\s*[-:.]?\s*", "", k_text).strip()
                k_span = OCRTextSpan(
                    span_id=f"{s.span_id}-k",
                    page_number=page_num,
                    text=k_text,
                    confidence=s.confidence,
                    bbox=OCRBoundingBox(
                        x_min=s.bbox.x_min,
                        y_min=s.bbox.y_min,
                        x_max=round((s.bbox.x_min + s.bbox.x_max) / 2, 4),
                        y_max=s.bbox.y_max,
                    ),
                )
                v_span = OCRTextSpan(
                    span_id=f"{s.span_id}-v",
                    page_number=page_num,
                    text=v_text,
                    confidence=s.confidence,
                    bbox=OCRBoundingBox(
                        x_min=round((s.bbox.x_min + s.bbox.x_max) / 2, 4),
                        y_min=s.bbox.y_min,
                        x_max=s.bbox.x_max,
                        y_max=s.bbox.y_max,
                    ),
                )
                additional_spans.extend([k_span, v_span])
                key_value_pairs.append({
                    "key": clean_k,
                    "value": v_text,
                    "key_span_id": k_span.span_id,
                    "val_span_id": v_span.span_id,
                    "bbox": {
                        "x0": v_span.bbox.x_min,
                        "y0": v_span.bbox.y_min,
                        "x1": v_span.bbox.x_max,
                        "y1": v_span.bbox.y_max,
                    },
                    "confidence": s.confidence,
                })
                seen_keys.add(k_text.upper())
                continue

        # 2. Form Box Header Geometric Association (e.g. CMS-1500 / UB-04)
        if RE_FORM_BOX_HEADER.search(text):
            clean_k = re.sub(r"^\d{1,2}(?:[a-z](?=\s|[-:.]|$))?\s*[-:.]?\s*", "", text).strip()
            if clean_k.upper() in seen_keys:
                continue

            best_val_span: OCRTextSpan | None = None
            best_dist = float("inf")

            for j, s2 in enumerate(spans):
                if i == j or s2.span_id.endswith("-k") or s2.span_id.endswith("-v"):
                    continue
                t2 = s2.text.strip()
                if not t2 or RE_FORM_BOX_HEADER.search(t2):
                    continue

                # Vertical neighbor: directly below within the box
                x_diff_v = abs(s2.bbox.x_min - s.bbox.x_min)
                y_dist_v = s2.bbox.y_min - s.bbox.y_max
                if x_diff_v <= 0.05 and -0.005 <= y_dist_v <= 0.04:
                    dist = x_diff_v + max(0.0, y_dist_v)
                    if dist < best_dist:
                        best_dist = dist
                        best_val_span = s2
                    continue

                # Horizontal neighbor: same row to the right
                y_diff_h = abs((s2.bbox.y_min + s2.bbox.y_max) / 2 - (s.bbox.y_min + s.bbox.y_max) / 2)
                x_dist_h = s2.bbox.x_min - s.bbox.x_max
                if y_diff_h <= 0.02 and 0.0 <= x_dist_h <= 0.35:
                    dist = y_diff_h + (x_dist_h * 0.5)
                    if dist < best_dist:
                        best_dist = dist
                        best_val_span = s2

            if best_val_span is not None:
                k_span = OCRTextSpan(
                    span_id=f"{s.span_id}-k",
                    page_number=page_num,
                    text=text,
                    confidence=s.confidence,
                    bbox=s.bbox,
                )
                v_span = OCRTextSpan(
                    span_id=f"{best_val_span.span_id}-v",
                    page_number=page_num,
                    text=best_val_span.text,
                    confidence=best_val_span.confidence,
                    bbox=best_val_span.bbox,
                )
                additional_spans.extend([k_span, v_span])
                key_value_pairs.append({
                    "key": clean_k,
                    "value": best_val_span.text.strip(),
                    "key_span_id": k_span.span_id,
                    "val_span_id": v_span.span_id,
                    "bbox": {
                        "x0": best_val_span.bbox.x_min,
                        "y0": best_val_span.bbox.y_min,
                        "x1": best_val_span.bbox.x_max,
                        "y1": best_val_span.bbox.y_max,
                    },
                    "confidence": round(min(s.confidence, best_val_span.confidence), 4),
                })
                seen_keys.add(clean_k.upper())

    return additional_spans, key_value_pairs


class DefaultOCREngine:
    """Deterministic OCR extraction engine for searchable PDFs, text documents, and raster images (ADR-006)."""

    def extract_pages(self, content: bytes, mime_type: str) -> list[OCRPagePayload]:
        """Extracts and returns only the page payloads."""
        pages, _, _ = self.extract(content, mime_type)
        return pages

    def extract(self, content: bytes, mime_type: str) -> tuple[list[OCRPagePayload], float, bool]:
        """
        Extracts pages and discrete token spans from raw bytes.
        Returns (pages, image_quality_score, is_searchable_pdf).
        """
        if not content or not content.strip():
            return [], 0.0, False

        # Prioritize magic bytes over MIME type to prevent images (e.g. ID cards, PNG claims) from being misparsed as PDFs
        is_raster_image = content.startswith(
            (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"II*\x00", b"MM\x00*", b"BM")
        )
        is_pdf = not is_raster_image and (content.startswith(b"%PDF-") or "pdf" in mime_type.lower())
        is_searchable_pdf = False

        if is_pdf:
            # Extract plain text stream markers between BT and ET
            matches = re.findall(rb"\((.*?)\)\s*Tj", content)
            if matches:
                is_searchable_pdf = True
                text_str = "\n".join(m.decode("ascii", errors="ignore") for m in matches)
            else:
                # Decompress FlateDecode streams using built-in zlib
                streams = re.findall(rb"stream[\r\n]+(.*?)[\r\n]+endstream", content, re.DOTALL)
                extracted_lines: list[str] = []
                for s in streams:
                    try:
                        dec = zlib.decompress(s)
                        str_matches = re.findall(rb"\((.*?)\)", dec)
                        if str_matches:
                            for sm in str_matches:
                                line = sm.decode("latin1", errors="ignore").strip()
                                if line and len(line) > 1:
                                    extracted_lines.append(line)
                    except (zlib.error, UnicodeDecodeError):
                        continue
                if extracted_lines:
                    is_searchable_pdf = True
                    text_str = "\n".join(extracted_lines)
                else:
                    text_str = ""
        else:
            # Check if content is a raster image (PNG, JPEG, GIF, TIFF, BMP)
            is_image = (
                content.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"II*\x00", b"MM\x00*", b"BM"))
                or "image" in mime_type.lower()
            )
            if is_image:
                engine = get_rapid_ocr_engine()
                if engine is not None:
                    try:
                        with Image.open(io.BytesIO(content)) as img:
                            width_px, height_px = img.size
                    except Exception:  # noqa: BLE001
                        width_px, height_px = 1700, 2200

                    try:
                        ocr_result, _ = engine(content)
                    except Exception:  # noqa: BLE001
                        ocr_result = None

                    if ocr_result:
                        spans: list[OCRTextSpan] = []
                        page_num = 1
                        lines_text: list[str] = []

                        for idx, item in enumerate(ocr_result, start=1):
                            dt_box, line_text, conf = item
                            line_str = str(line_text).strip()
                            if not line_str:
                                continue
                            lines_text.append(line_str)
                            confidence = float(conf) if conf is not None else 0.95

                            try:
                                xs = [pt[0] for pt in dt_box]
                                ys = [pt[1] for pt in dt_box]
                                x_min = max(0.0, min(1.0, float(min(xs)) / max(1, width_px)))
                                x_max = max(0.0, min(1.0, float(max(xs)) / max(1, width_px)))
                                y_min = max(0.0, min(1.0, float(min(ys)) / max(1, height_px)))
                                y_max = max(0.0, min(1.0, float(max(ys)) / max(1, height_px)))
                                bbox = OCRBoundingBox(
                                    x_min=round(x_min, 4),
                                    y_min=round(y_min, 4),
                                    x_max=round(max(x_min + 0.001, x_max), 4),
                                    y_max=round(max(y_min + 0.001, y_max), 4),
                                )
                            except Exception:  # noqa: BLE001
                                y_pos = min(0.95, 0.05 + (idx * 0.04))
                                bbox = OCRBoundingBox(x_min=0.05, y_min=y_pos, x_max=0.90, y_max=min(1.0, y_pos + 0.03))

                            span_id = f"span-p{page_num}-{idx:04d}"
                            spans.append(
                                OCRTextSpan(
                                    span_id=span_id,
                                    page_number=page_num,
                                    text=line_str,
                                    confidence=round(confidence, 4),
                                    bbox=bbox,
                                )
                            )

                        if spans:
                            add_spans, kv_pairs = extract_geometric_key_value_pairs(spans, page_num=page_num)
                            all_spans = spans + add_spans
                            raw_page_text = "\n".join(lines_text)
                            page_payload = OCRPagePayload(
                                page_number=page_num,
                                width_px=width_px,
                                height_px=height_px,
                                raw_text=raw_page_text,
                                spans=all_spans,
                                key_value_pairs=kv_pairs,
                            )
                            avg_conf = sum(s.confidence for s in all_spans) / len(all_spans)
                            quality_score = max(0.50, min(0.99, avg_conf))
                            return [page_payload], round(quality_score, 4), False

                return [], 0.20, False

            # Non-image, non-PDF text/markdown
            is_binary = b"\x00" in content[:1024]
            if is_binary:
                text_str = ""
            else:
                text_str = content.decode("utf-8", errors="ignore")

        lines = [line.strip() for line in text_str.split("\n") if line.strip()]
        if not lines:
            return [], 0.20, is_pdf

        spans: list[OCRTextSpan] = []
        page_num = 1

        for idx, line in enumerate(lines, start=1):
            y_pos = min(0.95, 0.05 + (idx * 0.04))
            span_id = f"span-p{page_num}-{idx:04d}"

            spans.append(
                OCRTextSpan(
                    span_id=span_id,
                    page_number=page_num,
                    text=line,
                    confidence=0.98,
                    bbox=OCRBoundingBox(x_min=0.05, y_min=y_pos, x_max=0.90, y_max=min(1.0, y_pos + 0.03)),
                )
            )

        if spans:
            add_spans, kv_pairs = extract_geometric_key_value_pairs(spans, page_num=page_num)
            all_spans = spans + add_spans
            raw_page_text = "\n".join(lines)
            page_payload = OCRPagePayload(
                page_number=page_num,
                width_px=1700,
                height_px=2200,
                raw_text=raw_page_text,
                spans=all_spans,
                key_value_pairs=kv_pairs,
            )

            quality_score = 0.95 if not is_pdf else (0.85 if is_searchable_pdf else 0.50)
            return [page_payload], quality_score, is_searchable_pdf


class OCRPreprocessorEngine:
    """
    OCR Preprocessor Node engine (Spec 03 § 5).
    Tokenizes document text into spatial spans and saves heavy payloads to storage.
    """

    def __init__(self, engine: Any = None) -> None:
        self.engine = engine or DefaultOCREngine()

    def process_document(
        self,
        content: bytes,
        document_id: str,
        case_id: str,
        mime_type: str,
        storage_backend: StorageBackend,
    ) -> OCRPreprocessorOutput:
        with trace_span(
            name="ocr_process_document",
            run_type="tool",
            component="ocr_preprocessor_engine",
            operation="process_document",
            inputs={},
        ) as span:
            span.set_summary(is_complete=False)
            start_time = time.perf_counter()

            if not content:
                span.set_summary(
                    status="FAILED",
                    ocr_span_count=0,
                    is_complete=False,
                )
                return OCRPreprocessorOutput(
                    document_id=document_id,
                    total_pages=0,
                    image_quality_score=0.0,
                    verdict=AgentVerdict.UNAVAILABLE,
                    is_searchable_pdf=False,
                    payload_storage_uri="",
                    latency_ms=0,
                )

            pages, quality_score, is_searchable_pdf = self.engine.extract(content, mime_type)
            total_spans = sum(len(p.spans) for p in pages) if pages else 0

            if not pages or quality_score < 0.40 or not pages[0].raw_text.strip():
                latency = int((time.perf_counter() - start_time) * 1000)
                span.set_summary(
                    status="FAILED",
                    ocr_span_count=total_spans,
                    is_complete=False,
                )
                return OCRPreprocessorOutput(
                    document_id=document_id,
                    total_pages=len(pages) if pages else 1,
                    image_quality_score=quality_score,
                    verdict=AgentVerdict.INDETERMINATE,
                    is_searchable_pdf=is_searchable_pdf,
                    payload_storage_uri="",
                    latency_ms=latency,
                )

            now_str = datetime.now(UTC).isoformat()
            
            vlm_extraction = {}
            # Optional Gemini VLM Enhancement (ADR-009)
            try:
                from medishield.core.config import settings
                if settings.GEMINI_API_KEY and (content.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8")) or "image" in mime_type.lower()):
                    import json

                    from google import genai
                    from google.genai import types
                    
                    client = genai.Client(api_key=settings.GEMINI_API_KEY)
                    prompt = (
                        "Extract the following fields from this identity document and return ONLY a valid JSON object. "
                        "Fields: member_id, first_name, last_name, date_of_birth, government_id_number, government_id_expiration_date. "
                        "If a field is missing, use null."
                    )
                    
                    # Determine mime type for Gemini
                    gemini_mime = "image/jpeg"
                    if content.startswith(b"\x89PNG"):
                        gemini_mime = "image/png"
                        
                    with trace_span(
                        name="gemini_vision_generate",
                        run_type="llm",
                        component="gemini_vision_client",
                        operation="id_extraction",
                        inputs={},
                    ) as vlm_span:
                        vlm_span.set_summary(provider="google")
                        response = client.models.generate_content(
                            model=settings.GEMINI_MODEL,
                            contents=[
                                types.Part.from_bytes(data=content, mime_type=gemini_mime),
                                prompt,
                            ],
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                temperature=0.0
                            ),
                        )
                        prompt_toks = 0
                        comp_toks = 0
                        if hasattr(response, "usage_metadata") and response.usage_metadata:
                            prompt_toks = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
                            comp_toks = getattr(response.usage_metadata, "candidates_token_count", 0) or 0
                        vlm_span.set_summary(
                            status="SUCCESS",
                            prompt_tokens=prompt_toks,
                            completion_tokens=comp_toks,
                            total_tokens=prompt_toks + comp_toks,
                        )
                        if response.text:
                            vlm_extraction = json.loads(response.text)
            except Exception as e:  # noqa: BLE001
                import logging
                logging.getLogger(__name__).warning("VLM extraction failed: %s", e)
                
            all_kv_pairs: list[dict[str, Any]] = []
            for p in pages:
                if hasattr(p, "key_value_pairs") and p.key_value_pairs:
                    all_kv_pairs.extend(p.key_value_pairs)

            doc_payload = OCRDocumentPayload(
                document_id=document_id,
                case_id=case_id,
                total_pages=len(pages),
                pages=pages,
                created_at=now_str,
                vlm_extraction=vlm_extraction,
                key_value_pairs=all_kv_pairs,
            )

            object_key = f"ocr/{case_id}/{document_id}.json"
            storage_backend.put_bytes(object_key, doc_payload.model_dump_json().encode("utf-8"), "application/json")

            latency = int((time.perf_counter() - start_time) * 1000)
            span.set_summary(
                status="SUCCESS",
                ocr_span_count=total_spans,
                is_complete=True,
            )
            return OCRPreprocessorOutput(
                document_id=document_id,
                total_pages=len(pages),
                image_quality_score=quality_score,
                verdict=AgentVerdict.PASS,
                is_searchable_pdf=is_searchable_pdf,
                payload_storage_uri=object_key,
                latency_ms=latency,
            )


def retrieve_ocr_payload(
    case_id: str,
    document_id: str,
    storage_backend: StorageBackend,
) -> OCRDocumentPayload:
    """Retrieves full OCR representation from object storage (Spec 03 § 5.2)."""
    object_key = f"ocr/{case_id}/{document_id}.json"
    raw_bytes = storage_backend.get_bytes(object_key)
    return OCRDocumentPayload.model_validate_json(raw_bytes.decode("utf-8"))
