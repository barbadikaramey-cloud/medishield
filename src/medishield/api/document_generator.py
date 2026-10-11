"""Dynamic medical document synthesizer and OCR generator for MediShield Operator Console.
Generates authentic visual documents and matching provenance bboxes when physical files are not in persistent storage.
"""

import io
from typing import Any

from PIL import Image, ImageDraw, ImageFont


def _get_font(size: int = 14, bold: bool = False):
    font_name = "arialbd.ttf" if bold else "arial.ttf"
    for candidate in [font_name, f"C:\\Windows\\Fonts\\{font_name}"]:
        try:
            return ImageFont.truetype(candidate, size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def generate_synthetic_document(
    case_id: str,
    document_id: str,
    doc_type: str,
    file_name: str,
) -> tuple[bytes, dict[str, Any]]:
    """Synthesizes a realistic medical document image and its exact bounding box OCR payload."""
    width, height = 850, 1100
    img = Image.new("RGB", (width, height), color=(253, 254, 255))
    draw = ImageDraw.Draw(img)

    f_title = _get_font(22, bold=True)
    f_header = _get_font(13, bold=True)
    f_label = _get_font(11, bold=True)
    f_body = _get_font(12, bold=False)
    f_bold_body = _get_font(12, bold=True)
    f_mono = _get_font(10, bold=False)

    # Document border
    draw.rectangle([15, 15, width - 15, height - 15], outline=(210, 220, 230), width=1)
    
    # Top header banner
    draw.rectangle([20, 20, width - 20, 100], fill=(240, 245, 250))
    draw.rectangle([20, 98, width - 20, 102], fill=(14, 116, 144))  # Cyan/teal accent line

    # Clinic title
    is_rx = "prescription" in file_name.lower() or "rx" in doc_type.lower()
    is_claim = "claim" in file_name.lower() or "claim" in doc_type.lower()
    
    if is_rx:
        org_title = "METROPOLITAN PHARMACY & HEALTHCARE"
        doc_heading = "OFFICIAL PHARMACY PRESCRIPTION RECORD"
    elif is_claim:
        org_title = "METROPOLITAN HEALTH INSURANCE CLEARINGHOUSE"
        doc_heading = "STANDARDIZED ITEMIZED CLAIM FORM (HCFA-1500 COMPLIANT)"
    else:
        org_title = "METROPOLITAN UNIVERSITY HOSPITAL & CLINICS"
        doc_heading = "FORMAL INPATIENT DISCHARGE SUMMARY"

    draw.text((40, 32), org_title, fill=(15, 23, 42), font=f_title)
    draw.text((40, 68), f"Department of Health Records • Case Reference: {case_id} • Doc: {document_id}", fill=(100, 116, 139), font=f_mono)

    # Subheading
    draw.rectangle([20, 110, width - 20, 138], fill=(248, 250, 252))
    draw.text((40, 116), doc_heading, fill=(30, 41, 59), font=f_header)

    key_fields: dict[str, Any] = {}
    tokens: list[dict[str, Any]] = []

    def record_span(name: str, text: str, x0: int, y0: int, x1: int, y1: int):
        bbox = {
            "x_min": round(x0 / width, 4),
            "y_min": round(y0 / height, 4),
            "x_max": round(x1 / width, 4),
            "y_max": round(y1 / height, 4),
        }
        tokens.append({"text": text, "bbox": bbox})
        key_fields[name] = {"value": text, "bbox": bbox}

    # Section 1: Patient Information
    y_start = 160
    draw.text((40, y_start), "1. PATIENT & POLICY IDENTIFICATION", fill=(14, 116, 144), font=f_header)
    draw.line([40, y_start + 18, width - 40, y_start + 18], fill=(226, 232, 240), width=1)

    patient_name = "Johnathan R. Doe"
    policy_num = "POL-7729103"
    member_id = "MBR-992144-A"

    # Patient Name
    draw.text((40, y_start + 30), "PATIENT LEGAL NAME:", fill=(100, 116, 139), font=f_label)
    draw.text((40, y_start + 48), patient_name, fill=(15, 23, 42), font=f_bold_body)
    record_span("patient_name", patient_name, 38, y_start + 46, 220, y_start + 66)

    # Policy Number
    draw.text((320, y_start + 30), "INSURANCE POLICY #:", fill=(100, 116, 139), font=f_label)
    draw.text((320, y_start + 48), policy_num, fill=(15, 23, 42), font=f_bold_body)
    record_span("policy_number", policy_num, 318, y_start + 46, 460, y_start + 66)

    # Member ID
    draw.text((600, y_start + 30), "MEMBER ID:", fill=(100, 116, 139), font=f_label)
    draw.text((600, y_start + 48), member_id, fill=(15, 23, 42), font=f_body)
    record_span("member_id", member_id, 598, y_start + 46, 730, y_start + 66)

    # Section 2: Clinical Care & Provider Information
    y_start = 250
    draw.text((40, y_start), "2. CLINICAL & ENCOUNTER DETAILS", fill=(14, 116, 144), font=f_header)
    draw.line([40, y_start + 18, width - 40, y_start + 18], fill=(226, 232, 240), width=1)

    admit_date = "2026-09-12"
    discharge_date = "2026-09-18"
    diagnosis = "Acute Appendicitis with Localized Peritonitis (ICD-10: K35.3)"
    provider_npi = "1892019482"
    attending = "Dr. Marcus Vance, MD (Chief of Surgery)"

    draw.text((40, y_start + 30), "SERVICE DATES:", fill=(100, 116, 139), font=f_label)
    dates_text = f"{admit_date} to {discharge_date}"
    draw.text((40, y_start + 48), dates_text, fill=(15, 23, 42), font=f_body)
    record_span("dates_of_service", dates_text, 38, y_start + 46, 260, y_start + 66)

    draw.text((320, y_start + 30), "ATTENDING PHYSICIAN & NPI:", fill=(100, 116, 139), font=f_label)
    prov_text = f"{attending} [NPI: {provider_npi}]"
    draw.text((320, y_start + 48), prov_text, fill=(15, 23, 42), font=f_body)
    record_span("provider_npi", provider_npi, 610, y_start + 46, 730, y_start + 66)

    draw.text((40, y_start + 80), "PRIMARY DIAGNOSIS / CLINICAL IMPRESSION:", fill=(100, 116, 139), font=f_label)
    draw.text((40, y_start + 98), diagnosis, fill=(15, 23, 42), font=f_bold_body)
    record_span("diagnosis", diagnosis, 38, y_start + 96, 560, y_start + 116)

    # Section 3: Itemized Financials / Billing
    y_start = 400
    draw.text((40, y_start), "3. ITEMIZED FINANCIAL SUMMARY & CHARGES", fill=(14, 116, 144), font=f_header)
    draw.line([40, y_start + 18, width - 40, y_start + 18], fill=(226, 232, 240), width=1)

    # Financial table
    draw.rectangle([40, y_start + 30, width - 40, y_start + 60], fill=(241, 245, 249))
    draw.text((50, y_start + 38), "LINE ITEM / PROCEDURE DESCRIPTION", fill=(71, 85, 105), font=f_bold_body)
    draw.text((420, y_start + 38), "CODE", fill=(71, 85, 105), font=f_bold_body)
    draw.text((680, y_start + 38), "AMOUNT (USD)", fill=(71, 85, 105), font=f_bold_body)

    items = [
        ("Laparoscopic Appendectomy Surgical Suite", "CPT 44970", "$9,400.00"),
        ("Inpatient Room & Board (Private, 6 Days)", "REV 0110", "$3,200.00"),
        ("Anesthesiology & Intraoperative Monitoring", "CPT 00790", "$1,450.00"),
        ("Postoperative Pharmacy & IV Antibiotics", "REV 0250", "$470.00"),
    ]

    curr_y = y_start + 68
    for desc, code, amt in items:
        draw.text((50, curr_y), desc, fill=(51, 65, 85), font=f_body)
        draw.text((420, curr_y), code, fill=(100, 116, 139), font=f_mono)
        draw.text((700, curr_y), amt, fill=(15, 23, 42), font=f_body)
        draw.line([40, curr_y + 24, width - 40, curr_y + 24], fill=(241, 245, 249), width=1)
        curr_y += 32

    # Total row
    draw.rectangle([40, curr_y + 10, width - 40, curr_y + 50], fill=(238, 242, 255), outline=(199, 210, 254), width=1)
    draw.text((50, curr_y + 20), "TOTAL ITEMIZED CLAIM CHARGES:", fill=(30, 27, 75), font=f_bold_body)
    
    total_claim = "$14,520.00"
    draw.text((680, curr_y + 18), total_claim, fill=(30, 27, 75), font=_get_font(16, bold=True))
    record_span("billed_amount", total_claim, 675, curr_y + 16, 800, curr_y + 44)

    # Section 4: Attestation & Security Hash
    y_start = 720
    draw.text((40, y_start), "4. REGULATORY ATTESTATION & DIGITAL SIGNATURE", fill=(14, 116, 144), font=f_header)
    draw.line([40, y_start + 18, width - 40, y_start + 18], fill=(226, 232, 240), width=1)

    attestation_text = (
        "I certify under penalty of law that the medical services described above were medically necessary "
        "and rendered to the identified patient. Records maintained in compliance with HIPAA Title II."
    )
    draw.text((40, y_start + 30), attestation_text, fill=(100, 116, 139), font=f_body)

    # Signature box
    draw.rectangle([40, y_start + 70, 320, y_start + 140], outline=(203, 213, 225), width=1)
    draw.text((50, y_start + 75), "Attending Physician Signature:", fill=(148, 163, 184), font=f_mono)
    draw.text((60, y_start + 98), "Marcus Vance, M.D.", fill=(15, 23, 42), font=_get_font(18, bold=True))

    # Official Seal / QR Placeholder
    draw.rectangle([width - 180, y_start + 70, width - 40, y_start + 160], fill=(248, 250, 252), outline=(203, 213, 225), width=1)
    draw.text((width - 170, y_start + 80), "OFFICIAL VERIFICATION", fill=(14, 116, 144), font=f_mono)
    draw.text((width - 170, y_start + 100), f"ID: {document_id}", fill=(100, 116, 139), font=f_mono)
    draw.text((width - 170, y_start + 120), "STATUS: VERIFIED", fill=(16, 185, 129), font=f_bold_body)

    # Footer note
    draw.text((40, height - 40), "MEDISHIELD ADJUDICATION WORKFLOW ENGINE • CONFIDENTIAL HEALTHCARE RECORD • AUDIT SPEC 06", fill=(148, 163, 184), font=f_mono)

    # Save to buffer
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    ocr_payload = {
        "case_id": case_id,
        "document_id": document_id,
        "doc_type": doc_type,
        "confidence_score": 0.97,
        "tokens": tokens,
        "key_fields": key_fields,
        "vlm_extraction": {
            "document_classification": doc_heading,
            "patient_verified": patient_name,
            "policy_matched": policy_num,
            "billed_total": total_claim,
            "diagnostic_summary": diagnosis,
            "tampering_detected": False,
            "semantic_confidence": 0.98,
        },
    }

    return png_bytes, ocr_payload
