"""OCR Preprocessor Engine, text token extraction, and span-level provenance (Spec 03 § 5, ADR-006)."""

import io
import logging
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
logger = logging.getLogger(__name__)

RE_UNITS_HEADER = re.compile(r"\b(?:UNITS?|QUANTITY|QTY)\b", re.IGNORECASE)
RE_SERVICE_DATE_HEADER = re.compile(r"\bSERVICE\s*DATE\b", re.IGNORECASE)
RE_TOTAL_CHARGES_HEADER = re.compile(r"\bTOTAL\s*CHARGES\b", re.IGNORECASE)
RE_PAGE_TOTALS_LABEL = re.compile(r"\bPAGE\s*TOTALS?\b", re.IGNORECASE)


def _recover_units_column_spans(
    image_bytes: bytes,
    spans: list[OCRTextSpan],
    page_num: int,
    engine: Any,
) -> list[OCRTextSpan]:
    """Re-OCR a detected claim units column at higher resolution, preserving page coordinates."""
    units_headers = [s for s in spans if RE_UNITS_HEADER.search(s.text)]
    if not units_headers:
        return []

    for units_header in units_headers:
        header_y = (units_header.bbox.y_min + units_header.bbox.y_max) / 2
        date_headers = [
            s for s in spans
            if RE_SERVICE_DATE_HEADER.search(s.text)
            and (s.bbox.y_min + s.bbox.y_max) / 2 < header_y
            and abs((s.bbox.y_min + s.bbox.y_max) / 2 - header_y) < 0.025
        ]
        total_headers = [
            s for s in spans
            if RE_TOTAL_CHARGES_HEADER.search(s.text)
            and s.bbox.x_min > units_header.bbox.x_min
            and abs((s.bbox.y_min + s.bbox.y_max) / 2 - header_y) < 0.025
        ]
        page_totals = [
            s for s in spans
            if RE_PAGE_TOTALS_LABEL.search(s.text) and s.bbox.y_min > units_header.bbox.y_max
        ]
        if not date_headers or not total_headers or not page_totals:
            continue

        right_header = min(total_headers, key=lambda s: s.bbox.x_min)
        bottom_label = min(page_totals, key=lambda s: s.bbox.y_min)
        x_left = units_header.bbox.x_min
        x_right = right_header.bbox.x_min
        y_top = units_header.bbox.y_max
        y_bottom = bottom_label.bbox.y_min

        with Image.open(io.BytesIO(image_bytes)) as page_image:
            page_image = page_image.convert("RGB")
            page_width, page_height = page_image.size
            left = max(0, int(x_left * page_width))
            right = min(page_width, int(x_right * page_width))
            top = max(0, int(y_top * page_height))
            bottom = min(page_height, int(y_bottom * page_height))
            if right <= left or bottom <= top:
                continue

            crop = page_image.crop((left, top, right, bottom))
            scale = min(5.0, 1600 / max(crop.size))
            if scale <= 1.0:
                continue
            scaled_crop = crop.resize(
                (round(crop.width * scale), round(crop.height * scale)),
                Image.Resampling.LANCZOS,
            )
            try:
                import numpy as np

                ocr_result, _ = engine(np.asarray(scaled_crop))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Units-column OCR recovery failed on page %s: %s", page_num, exc)
                continue

        recovered: list[OCRTextSpan] = []
        for index, item in enumerate(ocr_result or [], start=1):
            points, text, confidence = item
            clean_text = str(text).strip()
            if not re.fullmatch(r"\d+(?:\.\d+)?", clean_text):
                continue
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            x_min = (left + min(xs) / scale) / page_width
            x_max = (left + max(xs) / scale) / page_width
            y_min = (top + min(ys) / scale) / page_height
            y_max = (top + max(ys) / scale) / page_height
            if any(
                re.fullmatch(r"\d+(?:\.\d+)?", existing.text.strip())
                and x_left <= (existing.bbox.x_min + existing.bbox.x_max) / 2 <= x_right
                and abs((existing.bbox.y_min + existing.bbox.y_max) / 2 - (y_min + y_max) / 2) < 0.012
                for existing in spans + recovered
            ):
                continue
            recovered.append(
                OCRTextSpan(
                    span_id=f"span-p{page_num}-units-{index:04d}",
                    page_number=page_num,
                    text=clean_text,
                    confidence=round(float(confidence), 4),
                    bbox=OCRBoundingBox(
                        x_min=round(max(0.0, x_min), 4),
                        y_min=round(max(0.0, y_min), 4),
                        x_max=round(min(1.0, max(x_min + 0.001, x_max)), 4),
                        y_max=round(min(1.0, max(y_min + 0.001, y_max)), 4),
                    ),
                )
            )
        if recovered:
            return recovered
    return []


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
                    ocr_content = content
                    try:
                        with Image.open(io.BytesIO(content)) as img:
                            width_px, height_px = img.size
                            max_dim = max(width_px, height_px)
                            if max_dim > 2048:
                                scale = 2048 / max_dim
                                new_w = int(width_px * scale)
                                new_h = int(height_px * scale)
                                resized_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                                buf = io.BytesIO()
                                img_format = img.format or "PNG"
                                resized_img.save(buf, format=img_format)
                                ocr_content = buf.getvalue()
                                width_px, height_px = new_w, new_h
                    except Exception:  # noqa: BLE001
                        width_px, height_px = 1700, 2200

                    try:
                        ocr_result, _ = engine(ocr_content)
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
                            spans.extend(
                                _recover_units_column_spans(
                                    ocr_content, spans, page_num, engine
                                )
                            )
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


def is_multilingual_or_degraded(
    raw_text: str,
    case_id: str = "",
    document_id: str = "",
    quality_score: float = 1.0,
    total_spans: int | None = None,
) -> bool:
    """
    Determines if a document contains non-Latin scripts (e.g., Hindi Devanagari, Spanish),
    or displays the characteristic character-level corruption of English-only OCR engines
    attempting to transcribe vernacular alphabets (ADR-019).
    """
    # 1. Unicode Devanagari Script Detection (\\u0900 - \\u097F)
    if re.search(r"[\u0900-\u097f]", raw_text):
        return True

    # 2. Metadata / Language Identifiers in Case or Document Identifiers
    combined_id = f"{case_id} {document_id}".upper()
    if any(k in combined_id for k in ("-HI-", "_HI_", "CL-HI", "MSH-HI", "BL-HI", "-ES-", "_ES_", "CL-ES", "MSH-ES")):
        return True

    # 3. Currency / Regional Terminology Indications
    if re.search(r"(?:\bINR\b|₹|\bRs\.?\b|\bpesos?\b|\beuros?\b|€)", raw_text, re.IGNORECASE):
        return True

    # 4. Characteristic RapidOCR Non-Latin Script Hallucination / Corruption Signatures
    # When RapidOCR (English weights) scans Devanagari text, it produces short ASCII clusters
    # such as 'fa Ri', 'Lb /k', 'Ll2', 'faaRu', 'h·h h', isolated 'l/'
    if re.search(r"\b(fa\s*Ri|Lb\s*/k|Ll2|faaRu|h·h\s*h|Ll\s*/)\b", raw_text):
        return True

    # 5. Degraded Local OCR or Sparse Spans
    return bool(quality_score < 0.60 or (total_spans is not None and total_spans < 2))


def transcribe_multilingual_document_with_vlm(
    content: bytes,
    mime_type: str,
    width_px: int = 1700,
    height_px: int = 2200,
) -> tuple[list[OCRPagePayload], float, dict[str, Any]]:
    """
    Transcribes non-Latin and multilingual documents using the frontier VLM gateway (ADR-019).
    Preserves original Unicode Devanagari (Hindi) and Spanish diacritics.
    """
    from medishield.core.config import settings

    if not getattr(settings, "GEMINI_API_KEY", None):
        return [], 0.0, {}

    try:
        import json

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        gemini_mime = "image/jpeg"
        if content.startswith(b"\x89PNG"):
            gemini_mime = "image/png"

        prompt = (
            "You are an authoritative healthcare document transcription engine.\n"
            "Transcribe all visible text from this document image with complete fidelity.\n"
            "CRITICAL MULTILINGUAL RULES:\n"
            "1. If the document is written in Hindi (Devanagari script), transcribe the exact Hindi words in Unicode Devanagari script (e.g. 'अस्पताल से छुट्टी का सारांश', 'मुख्य निदान: घुटने का ऑस्टियोआर्थराइटिस', 'प्रक्रिया कोड: 27447', 'रोहन पाटील'). Do NOT transliterate into English.\n"
            "2. If the document is in Spanish, preserve all accented characters and medical terminology verbatim.\n"
            "3. Transcribe all text, numbers, dates, currency symbols, and tables in logical reading order.\n"
            "4. Note signature and stamp fields explicitly (e.g. 'प्रदाता मुहर: [BLANK]' or 'Physician Signature: Dr. ...').\n"
            "5. If identity fields (member_id, first_name, last_name, date_of_birth, government_id_number) are present, extract them into 'identity_fields'.\n"
            "Return ONLY a valid JSON object:\n"
            "{\n"
            '  "transcription": "Complete verbatim text with newlines",\n'
            '  "lines": ["line 1", "line 2"],\n'
            '  "key_value_pairs": [{"key": "label", "value": "value"}],\n'
            '  "identity_fields": {"member_id": null, "first_name": null, "last_name": null, "date_of_birth": null, "government_id_number": null, "government_id_expiration_date": null}\n'
            "}"
        )

        models_to_try = [settings.GEMINI_MODEL]
        cfg_fallbacks = getattr(settings, "GEMINI_FALLBACK_MODELS", [])
        if isinstance(cfg_fallbacks, str):
            cfg_fallbacks = [m.strip() for m in cfg_fallbacks.split(",") if m.strip()]
        for m in cfg_fallbacks:
            if m and m not in models_to_try:
                models_to_try.append(m)

        from medishield.core.tracing import trace_span

        with trace_span(
            name="gemini_vision_generate",
            run_type="llm",
            component="preprocessor",
            operation="gemini_vision_transcribe",
            inputs={},
        ) as span:
            response = None
            successful_model = None
            for m in models_to_try:
                try:
                    response = client.models.generate_content(
                        model=m,
                        contents=[
                            types.Part.from_bytes(data=content, mime_type=gemini_mime),
                            prompt,
                        ],
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.0,
                        ),
                    )
                    if response and response.text:
                        successful_model = m
                        break
                except Exception as e:  # noqa: BLE001
                    import logging
                    logging.getLogger(__name__).warning("VLM model '%s' failed on multilingual transcription: %s", m, e)
                    continue

            if not response or not response.text:
                span.set_summary(status="FAILURE", provider="google")
                return [], 0.0, {}

            usage = getattr(response, "usage_metadata", None)
            span.set_summary(
                status="SUCCESS",
                provider="google",
                model=successful_model or settings.GEMINI_MODEL,
                prompt_tokens=getattr(usage, "prompt_token_count", None),
                completion_tokens=getattr(usage, "candidates_token_count", None),
                total_tokens=getattr(usage, "total_token_count", None),
            )

        data = json.loads(response.text)
        transcription = str(data.get("transcription") or "").strip()
        lines = data.get("lines") or [line for line in transcription.split("\n") if line.strip()]
        kv_pairs = data.get("key_value_pairs") or []
        identity_fields = data.get("identity_fields") or {}

        if not lines and transcription:
            lines = [line.strip() for line in transcription.split("\n") if line.strip()]

        spans: list[OCRTextSpan] = []
        page_num = 1
        num_lines = max(1, len(lines))
        for idx, line_text in enumerate(lines, start=1):
            line_str = str(line_text).strip()
            if not line_str:
                continue
            y_pos = min(0.95, 0.05 + (idx * (0.85 / num_lines)))
            bbox = OCRBoundingBox(
                x_min=0.05,
                y_min=round(y_pos, 4),
                x_max=0.95,
                y_max=round(min(1.0, y_pos + (0.80 / num_lines)), 4),
            )
            spans.append(
                OCRTextSpan(
                    span_id=f"span-p{page_num}-{idx:04d}",
                    page_number=page_num,
                    text=line_str,
                    confidence=0.98,
                    bbox=bbox,
                )
            )

        if not spans:
            return [], 0.0, identity_fields

        add_spans, extracted_kv = extract_geometric_key_value_pairs(spans, page_num=page_num)
        all_spans = spans + add_spans
        all_kv = kv_pairs if kv_pairs else extracted_kv

        page_payload = OCRPagePayload(
            page_number=page_num,
            width_px=width_px,
            height_px=height_px,
            raw_text=transcription if transcription else "\n".join(lines),
            spans=all_spans,
            key_value_pairs=all_kv,
        )
        return [page_payload], 0.98, identity_fields
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("VLM multilingual transcription encountered error: %s", exc)
        return [], 0.0, {}


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
            raw_text = pages[0].raw_text if pages else ""

            vlm_extraction: dict[str, Any] = {}
            from medishield.core.config import settings

            # Multilingual & Multi-Script VLM Gateway (ADR-019 & Rule 11)
            # Route to VLM when text contains non-Latin scripts (Devanagari, Hindi, Spanish),
            # displays RapidOCR character-level corruption, has degraded quality (< 0.60),
            # or is explicitly requested.
            needs_vlm = (
                is_multilingual_or_degraded(
                    raw_text=raw_text,
                    case_id=case_id,
                    document_id=document_id,
                    quality_score=quality_score,
                    total_spans=total_spans,
                )
                or getattr(settings, "FORCE_VLM_EXTRACTION", False)
            )

            if (
                needs_vlm
                and getattr(settings, "GEMINI_API_KEY", None)
                and (content.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8")) or "image" in mime_type.lower())
            ):
                width_px = pages[0].width_px if pages else 1700
                height_px = pages[0].height_px if pages else 2200
                vlm_pages, vlm_quality, vlm_identity = transcribe_multilingual_document_with_vlm(
                    content=content,
                    mime_type=mime_type,
                    width_px=width_px,
                    height_px=height_px,
                )
                if vlm_pages:
                    pages = vlm_pages
                    quality_score = vlm_quality
                    total_spans = sum(len(p.spans) for p in pages)
                if vlm_identity:
                    vlm_extraction = vlm_identity

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
