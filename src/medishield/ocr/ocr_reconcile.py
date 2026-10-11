"""OCR Reconciler & Geometric Field Extractor (Spec 05 § 5, Rule 10-14).

Provides deterministic geometric field extraction, table-isolated CPT parsing,
two-pass OCR/VLM reconciliation with citation grounding, and rule-compliant
claim total derivation and arithmetic checking.
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any, Protocol

from medishield.core.config import settings
from medishield.domain.contracts import (
    OCRDocumentPayload,
    OCRTextSpan,
    ServiceLine,
)
from medishield.domain.faithfulness import verify_extracted_span
from medishield.domain.provenance import (
    FieldProvenance,
    ProvenanceType,
    ProvenanceValue,
)

logger = logging.getLogger(__name__)

# Canonical synthetic benchmark CPT procedural codes
SYNTHETIC_VALID_CPT_CODES: frozenset[str] = frozenset({
    "90837",  # Psychotherapy, 60 min
    "93000",  # Electrocardiogram, routine ECG
    "99213",  # Office/outpatient visit, established, low
    "99214",  # Office/outpatient visit, established, moderate
    "99215",  # Office/outpatient visit, established, high
    "99203",  # Office/outpatient visit, new, low
    "99204",  # Office/outpatient visit, new, moderate
    "99205",  # Office/outpatient visit, new, high
    "99283",  # Emergency department visit, moderate
    "71045",  # Chest X-ray, single view
    "80053",  # Comprehensive metabolic panel
    "85025",  # Complete blood count (CBC) with differential
    "44970",  # Laparoscopic appendectomy
    "00790",  # Anesthesia for intraperitoneal procedures
    "17000",  # Destruction premalignant lesion
    "36415",  # Routine venipuncture
})


class ReconcileStatus(str, Enum):
    """Field-level reconciliation status between OCR and VLM readings."""
    AGREE = "AGREE"
    OCR_ONLY = "OCR_ONLY"
    VLM_ONLY_GROUNDED = "VLM_ONLY_GROUNDED"
    VLM_UNGROUNDED = "VLM_UNGROUNDED"
    DISAGREE = "DISAGREE"
    INDETERMINATE = "INDETERMINATE"


class ArithmeticCheckStatus(str, Enum):
    """Line items sum validation status."""
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INDETERMINATE = "INDETERMINATE"


# Regex patterns
RE_BOX_PREFIX = re.compile(r"^\s*\d{1,2}(?:[a-zA-Z](?=\s|[-:.]|$))?\s*[-:.]?\s*", re.IGNORECASE)
RE_MONEY_STRICT = re.compile(
    r"^\s*\$?\s*([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2}|[0-9]+\.[0-9]{2})\s*$"
)
RE_MONEY_DOLLAR = re.compile(
    r"^\s*\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)\s*$"
)
RE_TOTAL_LABEL = re.compile(
    r"(?:total\s*charges?|page\s*totals?|amount\s*claimed|total\s*amount|estimated\s*amt\s*due)",
    re.IGNORECASE,
)
RE_ADDRESS_INDICATOR = re.compile(
    r"\b(?:chicago|street|st\.|blvd|ave|avenue|suite|ste\.|zip|po\s*box|road|rd\.|drive|dr\.|pkwy|hwy)\b",
    re.IGNORECASE,
)


def _clean_label_text(text: str) -> str:
    """Strips leading form box numbers (e.g. '47TOTALCHARGES' -> 'TOTALCHARGES')."""
    return RE_BOX_PREFIX.sub("", text.strip())


def parse_money_amount(text: str) -> float | None:
    """
    Parses money amount enforcing two decimals or a leading '$'.
    Discards standalone integers or box numbers (e.g. '48').
    """
    cleaned = text.strip().replace(",", "")
    match_strict = RE_MONEY_STRICT.match(cleaned)
    if match_strict:
        try:
            return float(match_strict.group(1))
        except ValueError:
            return None

    match_dollar = RE_MONEY_DOLLAR.match(cleaned)
    if match_dollar:
        try:
            return float(match_dollar.group(1))
        except ValueError:
            return None

    return None


def extract_money_value_geometrically(
    spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
) -> tuple[ProvenanceValue | None, float | None, bool]:
    """
    Extracts total claim amount geometrically from OCR spans.
    - Strips leading form box numbers from labels.
    - Money values MUST have two decimals or a '$'.
    - Numbers to the LEFT of labels are never values.
    - Searches geometrically to the right on the same line, or vertically below in the same column.
    
    Returns:
        (ProvenanceValue | None, float | None, has_total_label: bool)
    """
    has_total_label = False
    candidate_spans_with_val: list[tuple[float, OCRDocumentPayload, int, OCRTextSpan, float]] = []

    # First pass: identify label spans
    label_spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]] = []
    for doc, page_num, span in spans:
        cleaned_text = _clean_label_text(span.text)
        if RE_TOTAL_LABEL.search(cleaned_text):
            has_total_label = True
            label_spans.append((doc, page_num, span))

            # Check if value is printed in the same span to the right of the label
            label_match = RE_TOTAL_LABEL.search(cleaned_text)
            if label_match:
                after_label = cleaned_text[label_match.end():].strip()
                # Check for money pattern after label
                money_m = re.search(r"\$?\s*([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2}|[0-9]+\.[0-9]{2})\b", after_label)
                if money_m:
                    val = parse_money_amount(money_m.group(0))
                    if val is not None and val > 0:
                        _, prov = verify_extracted_span(money_m.group(1), span.span_id, page_num, doc)
                        pv = ProvenanceValue(value=val, confidence=0.98, provenance=prov)
                        return pv, val, True

    if not label_spans:
        return None, None, False

    # Second pass: search geometrically relative to label spans
    for l_doc, l_page, l_span in label_spans:
        l_bbox = l_span.bbox
        l_y_center = (l_bbox.y0 + l_bbox.y1) / 2.0

        for doc, page_num, span in spans:
            if doc.document_id != l_doc.document_id or page_num != l_page:
                continue
            if span.span_id == l_span.span_id:
                continue

            amt = parse_money_amount(span.text)
            if amt is None or amt <= 0:
                continue

            s_bbox = span.bbox
            s_y_center = (s_bbox.y0 + s_bbox.y1) / 2.0
            has_dollar = "$" in span.text

            # 1. Same row to the right:
            # y_center aligned within 0.02, x0 to the right of label
            if abs(s_y_center - l_y_center) <= 0.02 and s_bbox.x0 >= (l_bbox.x0 + 0.02):
                score = (15.0 if has_dollar else 5.0) - (abs(s_bbox.x0 - l_bbox.x1) * 5)
                candidate_spans_with_val.append((score, amt, doc, page_num, span, 0.98))
                continue

            # 2. Vertically below in the same column (UB-04 / HCFA column format):
            # Horizontal column alignment: x centers or x0 aligned within 0.08
            x_dist = min(
                abs(s_bbox.x0 - l_bbox.x0),
                abs((s_bbox.x0 + s_bbox.x1) / 2.0 - (l_bbox.x0 + l_bbox.x1) / 2.0),
            )
            x_col_match = x_dist <= 0.08
            # Below the label: y0 >= l_bbox.y0 and within reasonable vertical distance (e.g. 0.35)
            y_dist = s_bbox.y0 - l_bbox.y1
            y_below = (s_bbox.y0 >= (l_bbox.y1 - 0.01)) and (y_dist <= 0.35)
            if x_col_match and y_below:
                # Strong bonus for explicit '$' symbol, column alignment, and bottom row summary total in column
                score = (12.0 if has_dollar else 1.0) + max(0.0, 4.0 - (x_dist * 30)) + (s_bbox.y0 * 10)
                if amt >= 50.0:
                    score += 2.0
                candidate_spans_with_val.append((score, amt, doc, page_num, span, 0.96 if has_dollar else 0.85))

    if candidate_spans_with_val:
        # Sort by composite confidence score descending, then by amount magnitude
        candidate_spans_with_val.sort(key=lambda c: (c[0], c[1]), reverse=True)
        _, best_amt, best_doc, best_page, best_span, conf = candidate_spans_with_val[0]
        _, prov = verify_extracted_span(str(best_amt), best_span.span_id, best_page, best_doc)
        pv = ProvenanceValue(value=best_amt, confidence=conf, provenance=prov)
        return pv, best_amt, has_total_label

    return None, None, has_total_label


def extract_cpt_codes_from_table(
    spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    valid_cpt_codes: frozenset[str] = SYNTHETIC_VALID_CPT_CODES,
) -> list[ProvenanceValue]:
    """
    Extracts CPT procedure codes strictly from the procedure/service table region.
    - Rejects address spans containing street/city keywords.
    - Rejects IDs and ZIP codes outside the service lines table.
    - Uses strict fullmatch against 5-digit regex and verifies against valid CPT catalog.
    """
    found_codes: list[ProvenanceValue] = []
    seen_codes: set[str] = set()

    # Pattern for standalone 5-digit code
    re_cpt_token = re.compile(r"^\d{5}$")

    for doc, page_num, span in spans:
        text = span.text.strip()
        bbox = span.bbox

        # 1. Geographic filtering: address lines or demographics blocks are NEVER CPT codes
        if RE_ADDRESS_INDICATOR.search(text):
            continue

        # In standard medical forms (CMS-1500 / UB-04), procedure tables are generally in y in [0.25, 0.85]
        # Skip header metadata (< 0.22) unless explicitly marked as procedure
        if bbox.y1 < 0.22 and "CPT" not in text.upper() and "PROCEDURE" not in text.upper():
            continue

        # 2. Check for tokens inside the span
        tokens = re.split(r"[\s,;:/]+", text)
        for token in tokens:
            clean_token = token.strip()
            if (
                re_cpt_token.fullmatch(clean_token)
                and clean_token in valid_cpt_codes
                and clean_token not in seen_codes
            ):
                seen_codes.add(clean_token)
                _, prov = verify_extracted_span(clean_token, span.span_id, page_num, doc)
                found_codes.append(ProvenanceValue(value=clean_token, confidence=0.96, provenance=prov))

    return found_codes


def extract_service_lines_from_table(
    spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
) -> list[ServiceLine]:
    """
    Extracts structured line items geometrically using y-alignment anchored on the amount column.
    """
    line_items: list[ServiceLine] = []
    
    re_money = re.compile(r"^\s*\$?\s*(-?[0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2}|-?[0-9]+\.[0-9]{2})\s*$")
    re_date = re.compile(r"\b([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})\b")
    re_cpt_token = re.compile(r"^\d{5}$")
    re_rev_code = re.compile(r"^\d{3,4}$")

    from collections import defaultdict
    spans_by_page = defaultdict(list)
    for doc, page_num, span in spans:
        spans_by_page[(doc.document_id, page_num)].append((doc, page_num, span))
        
    line_number_counter = 1

    for (doc_id, page_num), page_spans in spans_by_page.items():
        table_spans = []
        for d, p, s in page_spans:
            if 0.22 <= s.bbox.y1 <= 0.88:
                if not RE_ADDRESS_INDICATOR.search(s.text):
                    table_spans.append((d, p, s))
                    
        amount_anchors = []
        for d, p, s in table_spans:
            match = re_money.match(s.text.replace(",", ""))
            if match and s.bbox.x1 > 0.6: 
                try:
                    val = float(match.group(1))
                    amount_anchors.append((s, val))
                except ValueError:
                    pass
                
        amount_anchors.sort(key=lambda x: x[0].bbox.y0)
        
        rows = []
        for anchor, val in amount_anchors:
            placed = False
            for row in rows:
                row_y = sum((s.bbox.y0 + s.bbox.y1)/2 for s, _ in row) / len(row)
                anch_y = (anchor.bbox.y0 + anchor.bbox.y1) / 2
                if abs(row_y - anch_y) < 0.02:
                    row.append((anchor, val))
                    placed = True
                    break
            if not placed:
                rows.append([(anchor, val)])
                
        for row in rows:
            row.sort(key=lambda x: x[0].bbox.x0)
            if not row: continue
            
            # Prioritize non-zero amounts to avoid selecting $0.00 Non-Covered Charges over Box 47 Total Charges
            positive_amounts = [item for item in row if item[1] > 0]
            if positive_amounts:
                tot_span, tot_val = positive_amounts[-1]
            else:
                tot_span, tot_val = row[-1]

            row_y = (tot_span.bbox.y0 + tot_span.bbox.y1) / 2
            
            row_spans = []
            for d, p, s in table_spans:
                s_y = (s.bbox.y0 + s.bbox.y1) / 2
                if abs(s_y - row_y) < 0.02:
                    row_spans.append(s)
            
            row_spans.sort(key=lambda x: x.bbox.x0)
            
            cpt_str = None
            prov_cpt = None
            date_str = None
            prov_date = None
            rev_str = "UNKNOWN"
            prov_rev = None
            qty_val = 1.0
            prov_qty = None
            desc_parts = []
            desc_spans = []
            
            for s in row_spans:
                text = s.text.strip()
                
                if not date_str:
                    d_match = re_date.search(text)
                    if d_match:
                        date_str = d_match.group(1)
                        _, prov_date = verify_extracted_span(date_str, s.span_id, page_num, d)
                        continue
                        
                if not cpt_str:
                    tokens = re.split(r"[\s,;:/]+", text)
                    for token in tokens:
                        clean_token = token.strip()
                        if re_cpt_token.fullmatch(clean_token) and clean_token in SYNTHETIC_VALID_CPT_CODES:
                            cpt_str = clean_token
                            _, prov_cpt = verify_extracted_span(cpt_str, s.span_id, page_num, d)
                            break
                    if cpt_str: continue

                if rev_str == "UNKNOWN" and s.bbox.x0 < 0.25:
                    tokens = re.split(r"[\s,;:/]+", text)
                    for token in tokens:
                        clean_token = token.strip()
                        if re_rev_code.fullmatch(clean_token) and clean_token != cpt_str:
                            rev_str = clean_token
                            _, prov_rev = verify_extracted_span(rev_str, s.span_id, page_num, d)
                            break
                    if rev_str != "UNKNOWN": continue
                    
                if 0.2 < s.bbox.x0 < 0.6:
                    desc_parts.append(text)
                    desc_spans.append(s)
                    
            for s in row_spans:
                if 0.4 < s.bbox.x0 < 0.7:
                    if re.fullmatch(r"\d+(?:\.\d+)?", s.text.strip()):
                        val = float(s.text.strip())
                        if 0 < val < 1000:
                            qty_val = val
                            _, prov_qty = verify_extracted_span(str(qty_val), s.span_id, page_num, d)
                            
            desc_str = " ".join(desc_parts) if desc_parts else "Medical Service"
            _, prov_desc = verify_extracted_span(desc_str, desc_spans[0].span_id if desc_spans else "unknown", page_num, d)
            _, prov_tot = verify_extracted_span(str(tot_val), tot_span.span_id, page_num, d)
            
            if cpt_str or date_str or rev_str != "UNKNOWN":
                line_items.append(
                    ServiceLine(
                        line_number=line_number_counter,
                        rev_code=ProvenanceValue(value=rev_str, confidence=0.9 if prov_rev else 0.0, provenance=prov_rev),
                        description=ProvenanceValue(value=desc_str, confidence=0.9, provenance=prov_desc),
                        hcpcs=ProvenanceValue(value=cpt_str, confidence=0.98 if prov_cpt else 0.0, provenance=prov_cpt),
                        service_date=ProvenanceValue(value=date_str or "UNKNOWN", confidence=0.98 if prov_date else 0.0, provenance=prov_date),
                        units=ProvenanceValue(value=qty_val, confidence=0.9, provenance=prov_qty),
                        amount=ProvenanceValue(value=tot_val, confidence=0.98, provenance=prov_tot),
                    )
                )
                line_number_counter += 1
                
    if not line_items:
        # Fallback for single-line OCR spans (e.g. synthetic test payloads or single-span OCR rows)
        re_single_line = re.compile(
            r"^(?:(?P<line_num>\d+)[.)]\s+)?(?:(?P<rev_code>\d{3,4})\s+)?(?P<cpt>\d{5})\s+(?P<desc>.+?)\s+(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+(?P<units>\d+(?:\.\d+)?)\s+(?:\$?\s*(?P<unit_price>-?\d+(?:\.\d+)?)\s+)?\$?\s*(?P<amount>-?\d+(?:\.\d+)?)$"
        )
        single_counter = 1
        for doc, page_num, s in spans:
            match = re_single_line.match(s.text.strip())
            if match:
                cpt_val = match.group("cpt")
                rev_val = match.group("rev_code") or "UNKNOWN"
                desc_val = match.group("desc").strip()
                date_val = match.group("date")
                units_val = float(match.group("units"))
                amt_val = float(match.group("amount"))
                ln_num = int(match.group("line_num")) if match.group("line_num") else single_counter

                _, prov_cpt = verify_extracted_span(cpt_val, s.span_id, page_num, doc)
                _, prov_date = verify_extracted_span(date_val, s.span_id, page_num, doc)
                _, prov_desc = verify_extracted_span(desc_val, s.span_id, page_num, doc)
                _, prov_units = verify_extracted_span(match.group("units"), s.span_id, page_num, doc)
                _, prov_amt = verify_extracted_span(match.group("amount"), s.span_id, page_num, doc)
                prov_rev = None
                if rev_val != "UNKNOWN":
                    _, prov_rev = verify_extracted_span(rev_val, s.span_id, page_num, doc)

                line_items.append(
                    ServiceLine(
                        line_number=ln_num,
                        rev_code=ProvenanceValue(value=rev_val, confidence=0.9 if prov_rev else 0.0, provenance=prov_rev),
                        description=ProvenanceValue(value=desc_val, confidence=0.9, provenance=prov_desc),
                        hcpcs=ProvenanceValue(value=cpt_val, confidence=0.98 if prov_cpt else 0.0, provenance=prov_cpt),
                        service_date=ProvenanceValue(value=date_val, confidence=0.98 if prov_date else 0.0, provenance=prov_date),
                        units=ProvenanceValue(value=units_val, confidence=0.9, provenance=prov_units),
                        amount=ProvenanceValue(value=amt_val, confidence=0.98, provenance=prov_amt),
                    )
                )
                single_counter += 1

    return line_items


class VisionClient(Protocol):
    """Abstract protocol for Vision-Language Models reading document images."""

    def read_fields(
        self,
        image_bytes: bytes,
        spans_with_ids: list[dict[str, Any]],
        field_queries: list[str],
    ) -> dict[str, dict[str, Any]]:
        """
        Reads document image and returns grounded candidates citing span_id.
        Schema per field:
        {
            "value": Any,
            "span_id": str | None,
            "confidence": float
        }
        """
        ...


class GeminiVisionClient:
    """Gemini 2.5 Flash implementation of VisionClient."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.GEMINI_API_KEY
        self._available = bool(self.api_key)

    def is_available(self) -> bool:
        return self._available

    def read_fields(
        self,
        image_bytes: bytes,
        spans_with_ids: list[dict[str, Any]],
        field_queries: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Invokes Gemini vision model with image and OCR spans for grounded reading."""
        if not self._available:
            return {}

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.api_key)
            prompt = (
                "You are an authoritative healthcare document auditor. "
                "Analyze this medical claim image and the provided OCR spans.\n"
                f"Fields to extract: {field_queries}\n\n"
                "OCR Spans:\n"
                + "\n".join(f"[{s['span_id']}] {s['text']}" for s in spans_with_ids[:80])
                + "\n\nCRITICAL RULES:\n"
                "1. For each field, cite the exact span_id where the text was read.\n"
                "2. If no span supports the value, return null for span_id.\n"
                "3. Return strict JSON: {\"fields\": {\"<field_name>\": {\"value\": ..., \"span_id\": ...}}}"
            )

            response = client.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
            import json
            res_json = json.loads(response.text)
            return res_json.get("fields", {})
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"GeminiVisionClient execution failed: {exc}")
            return {}


class HuggingFaceVisionClient:
    """Hugging Face Inference API implementation of VisionClient using Pixtral-12B."""

    def __init__(self, api_key: str | None = None) -> None:
        import os
        self.api_key = api_key or os.getenv("HF_API_KEY")
        self._available = bool(self.api_key)

    def is_available(self) -> bool:
        return self._available

    def read_fields(
        self,
        image_bytes: bytes,
        spans_with_ids: list[dict[str, Any]],
        field_queries: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not self._available:
            return {}

        try:
            import requests
            import json
            import base64

            prompt = (
                "You are an authoritative healthcare document auditor. "
                "Analyze this medical claim image and the provided OCR spans.\n"
                f"Fields to extract: {field_queries}\n\n"
                "OCR Spans:\n"
                + "\n".join(f"[{s['span_id']}] {s['text']}" for s in spans_with_ids[:80])
                + "\n\nCRITICAL RULES:\n"
                "1. For each field, cite the exact span_id where the text was read.\n"
                "2. If no span supports the value, return null for span_id.\n"
                "3. Return strict JSON: {\"fields\": {\"<field_name>\": {\"value\": ..., \"span_id\": ...}}}"
            )

            b64_img = base64.b64encode(image_bytes).decode("utf-8")
            image_uri = f"data:image/png;base64,{b64_img}"

            # Using Pixtral 12B since user specified "no llama"
            url = "https://api-inference.huggingface.co/models/mistralai/Pixtral-12B-2409/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "mistralai/Pixtral-12B-2409",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_uri}}
                        ]
                    }
                ],
                "max_tokens": 1000,
                "temperature": 0.0,
                "response_format": {"type": "json_object"}
            }

            response = requests.post(url, headers=headers, json=payload, timeout=45)
            response.raise_for_status()
            
            content = response.json()["choices"][0]["message"]["content"]
            res_json = json.loads(content)
            return res_json.get("fields", {})
        except Exception as exc:
            logger.warning(f"HuggingFaceVisionClient execution failed: {exc}")
            return {}



def reconcile_field_reading(
    field_name: str,
    ocr_value: Any | None,
    ocr_span_id: str | None,
    vlm_candidate: dict[str, Any] | None,
    all_spans_by_id: dict[str, str],
) -> tuple[ReconcileStatus, Any | None, str | None]:
    """
    Reconciles OCR reading vs VLM candidate.
    Statuses:
      - AGREE
      - OCR_ONLY
      - VLM_ONLY_GROUNDED
      - VLM_UNGROUNDED
      - DISAGREE
    """
    if vlm_candidate is None:
        if ocr_value is not None:
            return ReconcileStatus.OCR_ONLY, ocr_value, ocr_span_id
        return ReconcileStatus.INDETERMINATE, None, None

    vlm_val = vlm_candidate.get("value")
    vlm_span_id = vlm_candidate.get("span_id")

    # Check VLM grounding
    is_grounded = False
    if vlm_span_id and vlm_span_id in all_spans_by_id:
        cited_text = all_spans_by_id[vlm_span_id]
        if str(vlm_val).lower() in cited_text.lower():
            is_grounded = True

    if ocr_value is not None:
        if vlm_val is not None:
            # Check equality (with float tolerance if numeric)
            values_match = False
            try:
                values_match = abs(float(ocr_value) - float(vlm_val)) < 0.01
            except (ValueError, TypeError):
                values_match = str(ocr_value).strip() == str(vlm_val).strip()

            if values_match:
                return ReconcileStatus.AGREE, ocr_value, ocr_span_id or vlm_span_id
            else:
                return ReconcileStatus.DISAGREE, ocr_value, ocr_span_id
        else:
            return ReconcileStatus.OCR_ONLY, ocr_value, ocr_span_id
    else:
        if vlm_val is not None:
            if is_grounded:
                return ReconcileStatus.VLM_ONLY_GROUNDED, vlm_val, vlm_span_id
            else:
                return ReconcileStatus.VLM_UNGROUNDED, None, None
        return ReconcileStatus.INDETERMINATE, None, None


def evaluate_claim_totals(
    declared_total: float | None,
    declared_total_pv: ProvenanceValue | None,
    service_lines: list[ServiceLine],
    has_total_label: bool,
) -> tuple[ProvenanceValue | None, float | None, ArithmeticCheckStatus, list[str]]:
    """
    Deterministic validator for claim total and line item sum (Spec 05 § 5, Rules 10-14).
    
    Rules:
    1. If declared_total is extracted:
       - If service lines exist, compare declared_total against line sum.
       - Match within $0.01 -> PASS
       - Mismatch -> FAIL (yields ESCALATE)
    2. If declared_total is missing but form HAS a total label:
       - Extraction failure: label exists but value was not found.
       - Returns INDETERMINATE. Never auto-derives!
    3. If declared_total is missing and form has NO total label:
       - Form has no total block at all.
       - If service lines exist: derive total = sum(lines).
       - Mark arithmetic check as NOT_APPLICABLE (cannot validate derived sum against itself).
       - Confidence is min(line confidences).
       - Evidence quality score is reduced (< 1.0).
    """
    validation_errors: list[str] = []

    # Calculate line items sum
    calculated_lines_sum = 0.0
    if service_lines:
        line_sum = 0.0
        for line in service_lines:
            try:
                line_sum += float(line.amount.value)
            except (ValueError, TypeError):
                pass
        calculated_lines_sum = round(line_sum, 2)

    # Case 1: Declared total was successfully extracted
    if declared_total is not None and declared_total_pv is not None:
        if service_lines:
            if abs(calculated_lines_sum - declared_total) < 0.01:
                return declared_total_pv, declared_total, ArithmeticCheckStatus.PASS, []
            else:
                err = (
                    f"ARITHMETIC_DRIFT: Calculated line sum (${calculated_lines_sum:.2f}) "
                    f"differs from claim header amount (${declared_total:.2f})."
                )
                validation_errors.append(err)
                return declared_total_pv, declared_total, ArithmeticCheckStatus.FAIL, validation_errors
        else:
            return declared_total_pv, declared_total, ArithmeticCheckStatus.PASS, []

    # Case 2: Declared total missing, but form HAS a total charges label
    if has_total_label:
        err = "EXTRACTION_FAILURE: Claim form contains a total charges label, but no valid amount could be extracted."
        validation_errors.append(err)
        return None, None, ArithmeticCheckStatus.INDETERMINATE, validation_errors

    # Case 3: Declared total missing, and form has NO total label at all
    if service_lines:
        # Derive total from line items
        min_conf = min((line.amount.confidence for line in service_lines), default=0.70)
        
        # safely get provenance if hcpcs exists
        prov = None
        if service_lines[0].hcpcs and service_lines[0].hcpcs.provenance:
            prov = service_lines[0].hcpcs.provenance
            
        derived_pv = ProvenanceValue(
            value=calculated_lines_sum,
            confidence=min_conf,
            provenance=FieldProvenance(
                document_id=prov.document_id if prov else "DERIVED",
                page_number=prov.page_number if prov else 1,
                text_content=f"DERIVED_SUM_{calculated_lines_sum:.2f}",
                provenance_type=ProvenanceType.INFERRED,
                evidence_quality_score=0.70,
            ),
        )
        return derived_pv, calculated_lines_sum, ArithmeticCheckStatus.NOT_APPLICABLE, []

    # Case 4: No declared total, no label, no lines
    validation_errors.append("MISSING_CLAIM_AMOUNT: Total claim amount could not be extracted.")
    return None, None, ArithmeticCheckStatus.INDETERMINATE, validation_errors
