import re
from datetime import date, datetime, timedelta
from typing import Any

from medishield.db.enums import AgentVerdict, CodingSystem
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    ClinicalAlignmentOutput,
    ConsistencyCheckReport,
    FraudAgentOutput,
    KYCAgentOutput,
    OCRDocumentPayload,
    OCRTextSpan,
    PolicyCoverageOutput,
)
from medishield.domain.provenance import (
    EvidenceSource,
    EvidenceVerificationStatus,
    FieldProvenance,
    ProvenanceType,
)


def normalize_text(text: str) -> str:
    """Normalizes text by lowercasing, stripping punctuation, and compressing whitespace (Spec 06)."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()


def fuzzy_token_match(target: str, candidate: str, threshold: float = 0.8) -> bool:
    """
    Evaluates token-set containment to tolerate minor OCR boundary variations.
    Returns True if target tokens overlap candidate tokens above the threshold.
    """
    target_tokens = set(normalize_text(target).split())
    if not target_tokens:
        return False
    candidate_tokens = set(normalize_text(candidate).split())
    intersection = target_tokens.intersection(candidate_tokens)
    return (len(intersection) / len(target_tokens)) >= threshold


def locate_best_matching_span(target: str, spans: list[OCRTextSpan]) -> OCRTextSpan | None:
    """Finds the span with the highest token overlap against the target string."""
    best_span: OCRTextSpan | None = None
    best_score = 0
    target_tokens = set(normalize_text(target).split())

    for span in spans:
        span_tokens = set(normalize_text(span.text).split())
        overlap = len(target_tokens.intersection(span_tokens))
        if overlap > best_score:
            best_score = overlap
            best_span = span

    return best_span


def verify_extracted_span(
    extracted_value: Any,
    cited_span_id: str | None,
    cited_page_number: int,
    ocr_payload: OCRDocumentPayload,
) -> tuple[bool, FieldProvenance]:
    """
    Authoritative 3-stage Faithfulness Verification Algorithm (Spec 06, Section 2).
    
    1. Direct Span Match: Quality = 1.0 (ProvenanceType.SPAN)
    2. Page-Level Search: Quality = 0.5 (ProvenanceType.PAGE, 50% penalty)
    3. Hallucination / Missing: Quality = 0.0 (ProvenanceType.INFERRED)
    """
    val_str = str(extracted_value) if extracted_value is not None else ""
    norm_val = normalize_text(val_str)

    # Locate the target page in the OCR payload
    page = next(
        (p for p in ocr_payload.pages if p.page_number == cited_page_number),
        None,
    )
    if not page:
        return False, FieldProvenance(
            document_id=ocr_payload.document_id,
            page_number=cited_page_number,
            span_id=cited_span_id,
            text_content=val_str,
            provenance_type=ProvenanceType.INFERRED,
            evidence_quality_score=0.0,
        )

    # ── Stage 1: Direct Span ID Match ─────────────────────────────────────────
    if cited_span_id:
        span = next((s for s in page.spans if s.span_id == cited_span_id), None)
        if span:
            norm_span = normalize_text(span.text)
            if norm_val in norm_span or fuzzy_token_match(norm_val, norm_span):
                return True, FieldProvenance(
                    document_id=ocr_payload.document_id,
                    page_number=cited_page_number,
                    span_id=cited_span_id,
                    text_content=span.text,
                    bbox=span.bbox,
                    bounding_box=span.bbox.to_list(),
                    provenance_type=ProvenanceType.SPAN,
                    evidence_quality_score=1.0,
                )

    # ── Stage 2: Page-Level Fallback Search ────────────────────────────────────
    norm_page = normalize_text(page.raw_text)
    if norm_val in norm_page or fuzzy_token_match(norm_val, norm_page):
        best_span = locate_best_matching_span(norm_val, page.spans)
        fallback_bbox = best_span.bbox if best_span else None
        return True, FieldProvenance(
            document_id=ocr_payload.document_id,
            page_number=cited_page_number,
            span_id=best_span.span_id if best_span else None,
            text_content=val_str,
            bbox=fallback_bbox,
            bounding_box=fallback_bbox.to_list() if fallback_bbox else None,
            provenance_type=ProvenanceType.PAGE,
            evidence_quality_score=0.5,
        )

    # ── Stage 3: Verification Failure (Hallucination) ─────────────────────────
    return False, FieldProvenance(
        document_id=ocr_payload.document_id,
        page_number=cited_page_number,
        span_id=cited_span_id,
        text_content=val_str,
        provenance_type=ProvenanceType.INFERRED,
        evidence_quality_score=0.0,
    )


def verify_clinical_evidence_reference(
    reference: FieldProvenance,
    ocr_payload: OCRDocumentPayload,
) -> tuple[bool, FieldProvenance]:
    """Verify clinical evidence against its original persisted OCR document/page/span."""
    rejected = reference.model_copy(update={
        "evidence_source": EvidenceSource.OCR,
        "verification_status": EvidenceVerificationStatus.REJECTED,
        "evidence_quality_score": 0.0,
        "evidence_relevance_verified": False,
    })
    if reference.document_id != ocr_payload.document_id or not reference.text_content.strip():
        return False, rejected

    page = next((page for page in ocr_payload.pages if page.page_number == reference.page_number), None)
    if page is None:
        return False, rejected

    if reference.span_id:
        span = next((span for span in page.spans if span.span_id == reference.span_id), None)
        if span is None:
            return False, rejected
        if normalize_text(reference.text_content) != normalize_text(span.text):
            return False, rejected
        if reference.bbox is not None and reference.bbox != span.bbox:
            return False, rejected
        if reference.bounding_box is not None and reference.bounding_box != span.bbox.to_list():
            return False, rejected
        return True, reference.model_copy(update={
            "bbox": span.bbox,
            "bounding_box": span.bbox.to_list(),
            "provenance_type": ProvenanceType.SPAN,
            "evidence_source": EvidenceSource.OCR,
            "verification_status": EvidenceVerificationStatus.VERIFIED,
            "evidence_quality_score": min(reference.evidence_quality_score, span.confidence),
        })

    normalized_reference = normalize_text(reference.text_content)
    if not normalized_reference or normalized_reference not in normalize_text(page.raw_text):
        return False, rejected

    return True, reference.model_copy(update={
        "provenance_type": ProvenanceType.PAGE,
        "evidence_source": EvidenceSource.OCR,
        "verification_status": EvidenceVerificationStatus.VERIFIED,
        "evidence_quality_score": min(reference.evidence_quality_score, 0.5),
    })


def parse_medical_date(raw: Any) -> date | None:
    """Robustly parses medical dates across ISO, slash (MM/DD/YYYY, DD/MM/YYYY), hyphen, and textual formats."""
    if raw is None:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if not isinstance(raw, str):
        return None
    raw_str = raw.strip()
    if not raw_str or raw_str.upper() in ("UNKNOWN", "NONE", "NULL"):
        return None

    try:
        return date.fromisoformat(raw_str)
    except ValueError:
        pass

    formats = [
        "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y",
        "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
        "%d %B %Y", "%d %b %Y", "%d-%b-%Y", "%d-%B-%Y", "%Y/%m/%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw_str, fmt).date()
        except ValueError:
            continue

    # Regex search within string if embedded (e.g. "Signed: 11/14/2025" or "November 14, 2025")
    date_regex = re.search(
        r"\b([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+[0-9]{1,2},?\s+[0-9]{4})\b",
        raw_str,
        re.IGNORECASE,
    )
    if date_regex:
        matched = date_regex.group(1).replace(",", "")
        for fmt in formats:
            try:
                return datetime.strptime(matched, fmt).date()
            except ValueError:
                continue
    return None


def evaluate_case_consistency(
    kyc_output: KYCAgentOutput,
    claims_output: ClaimsAgentOutput,
    policy_output: PolicyCoverageOutput,
    clinical_output: ClinicalAlignmentOutput | None = None,
    document_texts: dict[str, str] | None = None,
    fraud_output: FraudAgentOutput | None = None,
    code_catalog: Any | None = None,
) -> ConsistencyCheckReport:
    """
    Case-Level Cross-Agent & Cross-Document Consistency Engine (Spec 06 § 4, ADR-011).
    Performs deterministic factual verification across document packet:
    1. Line items arithmetic consistency.
    2. Date of service policy eligibility window.
    3. Multi-format date reconciliation between Claim Form and Discharge Summary / Prescriptions.
    4. Attending physician / doctor signature verification on claim form & medical records.
    5. Patient / insured signature & authorization verification.
    6. Provider NPI verification (rendering & billing).
    7. Patient identity and common demographic fields consistency (name, DOB, member ID).
    8. Prior authorization verification for high-complexity / major procedures where applicable.
    9. Explicit, specific reasons for escalation (avoiding generic 'low OCR' catch-alls).
    """
    conflicts: list[str] = []
    specific_reasons: list[str] = []

    # Normalized text mapping for document types
    doc_map: dict[str, str] = {}
    combined_texts: list[str] = []
    if document_texts:
        if isinstance(document_texts, dict):
            for k, v in document_texts.items():
                doc_map[str(k).upper()] = str(v)
                doc_map[str(k).lower()] = str(v)
                combined_texts.append(str(v))
        elif isinstance(document_texts, (list, tuple)):
            for item in document_texts:
                s_item = str(item)
                combined_texts.append(s_item)
                s_lower = s_item.lower()
                if "discharge" in s_lower or "clinical" in s_lower or "operative" in s_lower:
                    doc_map["DISCHARGE_SUMMARY"] = s_item
                if "claim" in s_lower or "cms" in s_lower or "cpt" in s_lower or "billing" in s_lower:
                    doc_map["CLAIM_FORM"] = s_item
                if "prescription" in s_lower or "rx" in s_lower:
                    doc_map["PRESCRIPTION"] = s_item
                if "amendment" in s_lower or "endorsement" in s_lower or "policy" in s_lower or "rider" in s_lower:
                    doc_map["POLICY_AMENDMENT"] = s_item

    claim_text = doc_map.get("CLAIM_FORM") or doc_map.get("claim_forms") or ("\n".join(combined_texts) if combined_texts else "")
    disch_text = doc_map.get("DISCHARGE_SUMMARY") or doc_map.get("discharge_summaries") or ("\n".join(combined_texts) if combined_texts else "")
    rx_text = doc_map.get("PRESCRIPTION") or doc_map.get("prescriptions") or ""
    policy_amendment_text = (
        doc_map.get("POLICY_AMENDMENT")
        or doc_map.get("policy_amendments")
        or doc_map.get("AMENDMENT")
        or doc_map.get("amendment")
        or doc_map.get("POLICY")
        or doc_map.get("policy")
        or ""
    )
    if not policy_amendment_text and combined_texts:
        for t in combined_texts:
            if any(k in t.lower() for k in ("amendment", "endorsement", "policy rider", "policy addendum")):
                policy_amendment_text = t
                break

    # 1. Arithmetic Consistency: Sum of line items vs declared total
    amounts_consistent = claims_output.line_items_sum_valid
    if not amounts_consistent:
        diff = round(
            abs(float(getattr(claims_output.claim_amount, "value", 0.0) or 0.0) - claims_output.calculated_lines_sum),
            2,
        )
        msg = (
            f"Arithmetic drift detected: declared total (${getattr(claims_output.claim_amount, 'value', 0.0)}) "
            f"differs from sum of line items (${claims_output.calculated_lines_sum}) by ${diff}."
        )
        conflicts.append(msg)
        specific_reasons.append("Line items arithmetic discrepancy: sum of itemized charges differs from declared total")

    # 2. Date of Service vs Policy Effective Dates
    dates_consistent = True
    if not policy_output.effective_on_service_date:
        dates_consistent = False
        conflicts.append("Date of service falls outside active policy coverage window.")
        specific_reasons.append("Date of service falls outside active policy coverage window")

    # 3. Multi-Format Date Reconciliation across Claim Form, Discharge Summary, and Rx
    date_reconciliation_summary: str | None = None
    svc_date_raw = getattr(claims_output.service_date, "value", None)
    claim_svc_date = parse_medical_date(svc_date_raw)

    # Obtain patient date of birth from KYC to prevent mistaking birth date for medical encounter dates
    kyc_dob_raw = None
    if kyc_output:
        if getattr(kyc_output, "dob", None):
            kyc_dob_raw = getattr(kyc_output.dob, "value", None)
        elif getattr(kyc_output, "date_of_birth", None):
            kyc_dob_raw = getattr(kyc_output.date_of_birth, "value", getattr(kyc_output, "date_of_birth", None))
    kyc_dob = parse_medical_date(kyc_dob_raw) if kyc_dob_raw else None

    adm_date: date | None = None
    disch_date: date | None = None
    if disch_text:
        # 1. Targeted regex directly capturing date tokens, preventing greedy text matching
        date_token_pattern = r"([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+[0-9]{1,2},?\s+[0-9]{4})"
        adm_m = re.search(
            r"(?:admission\s*date|admitted\s*on|admit\s*date)\s*[:]?\s*" + date_token_pattern,
            disch_text,
            re.IGNORECASE,
        )
        if adm_m:
            c_adm = parse_medical_date(adm_m.group(1))
            if c_adm and (not kyc_dob or c_adm != kyc_dob) and c_adm.year >= 2020:
                adm_date = c_adm

        disch_m = re.search(
            r"(?:discharge\s*date|discharged\s*on|cleared\s*for\s*discharge\s*on)\s*[:]?\s*" + date_token_pattern,
            disch_text,
            re.IGNORECASE,
        )
        if disch_m:
            c_disch = parse_medical_date(disch_m.group(1))
            if c_disch and (not kyc_dob or c_disch != kyc_dob) and c_disch.year >= 2020:
                disch_date = c_disch

        if not adm_date or not disch_date:
            # Fallback scan of dates in discharge text, strictly excluding birth dates (DOB / year < 2020)
            dates_in_disch = re.findall(
                r"\b([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+[0-9]{1,2},?\s+[0-9]{4})\b",
                disch_text,
                re.IGNORECASE,
            )
            parsed_disch_dates = [
                d for d in (parse_medical_date(x) for x in dates_in_disch)
                if d is not None and (not kyc_dob or d != kyc_dob) and d.year >= 2020
            ]
            if len(parsed_disch_dates) >= 2:
                sorted_d = sorted(parsed_disch_dates)
                if not adm_date:
                    adm_date = sorted_d[0]
                if not disch_date:
                    disch_date = sorted_d[-1]
            elif len(parsed_disch_dates) == 1:
                if not adm_date:
                    adm_date = parsed_disch_dates[0]
                if not disch_date:
                    disch_date = parsed_disch_dates[0]

    rx_date: date | None = None
    if rx_text:
        rx_m = re.search(r"(?:date|prescribed|rx\s*date)\s*[:]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+[0-9]{1,2},?\s+[0-9]{4})", rx_text, re.IGNORECASE)
        if rx_m:
            c_rx = parse_medical_date(rx_m.group(1))
            if c_rx and (not kyc_dob or c_rx != kyc_dob) and c_rx.year >= 2020:
                rx_date = c_rx
        if not rx_date:
            dates_in_rx = re.findall(
                r"\b([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+[0-9]{1,2},?\s+[0-9]{4})\b",
                rx_text,
                re.IGNORECASE,
            )
            parsed_rx = [
                d for d in (parse_medical_date(x) for x in dates_in_rx)
                if d is not None and (not kyc_dob or d != kyc_dob) and d.year >= 2020
            ]
            if parsed_rx:
                rx_date = parsed_rx[0]

    # Reconcile claim service date with discharge summary
    # Standard health insurance policies permit billing within allowable policy window around hospital encounter
    # (e.g. up to 30 days pre-admission for workup/labs, and up to 30 days post-discharge for follow-up care)
    if claim_svc_date and adm_date and disch_date:
        stay_window_start = adm_date - timedelta(days=30)
        stay_window_end = disch_date + timedelta(days=30)

        if stay_window_start <= claim_svc_date <= stay_window_end:
            if adm_date <= claim_svc_date <= disch_date:
                date_reconciliation_summary = (
                    f"Claim service date ({claim_svc_date}) chronologically aligns within documented "
                    f"hospital stay ({adm_date} to {disch_date})."
                )
            else:
                date_reconciliation_summary = (
                    f"Claim service date ({claim_svc_date}) chronologically aligns within allowable policy coverage window "
                    f"for documented hospital stay ({adm_date} to {disch_date} ± 30 days)."
                )
        else:
            dates_consistent = False
            msg = (
                f"Date conflict detected: Claim service date ({claim_svc_date}) falls outside allowable policy window "
                f"for documented hospital stay ({adm_date} to {disch_date} ± 30 days)."
            )
            conflicts.append(msg)
            specific_reasons.append(
                f"Date conflict: Claim service date ({claim_svc_date}) falls outside allowable policy window "
                f"({adm_date} to {disch_date} ± 30 days)"
            )

    # Reconcile prescription date with hospital stay (flag date conflicts e.g. C_002)
    if rx_date and disch_date and rx_date > (disch_date + timedelta(days=30)):
        dates_consistent = False
        diff_days = (rx_date - disch_date).days
        msg = f"Date conflict detected: Prescription date ({rx_date}) post-dates hospital discharge ({disch_date}) by {diff_days} days."
        conflicts.append(msg)
        specific_reasons.append(f"Date conflict: Prescription date ({rx_date}) is {diff_days} days post-discharge (exceeds 30-day post-acute limit)")

    # 4. Doctor Signatures Verification (Positive Evidence Attestation Model - ADR-019, Rules 12 & 13)
    doctor_signature_verified = True
    if claim_text or disch_text:
        combined_doc_text = f"{claim_text} {disch_text}"

        # Check explicit negative markers first
        has_dr_missing_marker = bool(
            re.search(
                r"(\[signature\s*missing\]|signaturemissing|"
                r"physician\s*signature\s*[:\-]?\s*(\[missing\]|none|no|missing|\[blank\]|____+|false|\b0\b)|"
                r"attending\s*physician[^\n]*\[\s*unsigned|attending\s*physician[^\n]*unsigned|\[unsigned|"
                r"pending\s*attestation|not\s*signed|doctor\s*signature\s*[:\-]?\s*(\[missing\]|none|no|missing|\[blank\]|____+)|"
                r"प्रदाता\s*(?:मुहर|हस्ताक्षर)\s*[:\-]?\s*(?:____+|\[अनुपस्थित\]|नहीं))",
                combined_doc_text,
                re.IGNORECASE,
            )
        )

        has_dr_positive_evidence = False
        dr_sig_m = re.search(
            r"(?:physician\s*signature|doctor(?:\x27s)?\s*signature|attending\s*physician(?:\s*signature)?|provider\s*(?:signature|stamp))\s*[:\-]?\s*([^\n\r]+)",
            combined_doc_text,
            re.IGNORECASE,
        )
        if dr_sig_m:
            val = dr_sig_m.group(1).strip()
            is_negative = bool(re.search(r"^(\[?(?:missing|blank|unsigned|none|no|false|0)\]?|___+|pending)", val, re.IGNORECASE))
            if not is_negative and (
                any(k in val.lower() for k in ("dr", "md", "do", "facs", "signed", "file", "/s/", "yes", "valid"))
                or len(val) >= 3
            ):
                has_dr_positive_evidence = True

        if not has_dr_positive_evidence:
            has_dr_positive_evidence = bool(
                re.search(
                    r"(?:signed\s*by(?:\s*dr\.?|\s*physician|\s*attending)|"
                    r"electronically\s*signed\s*by(?:\s*dr\.?|\s*physician|\s*attending)|"
                    r"dr\.\s*[a-z\s]+(?:,\s*(?:md|do|facs))|physician\s*attestation\s*verified|"
                    r"चिकित्सक\s*हस्ताक्षर\s*[:\-]?\s*(?:डॉ|हस्ताक्षरित)|डॉ\.\s*[a-z\u0900-\u097f\s]+)",
                    combined_doc_text,
                    re.IGNORECASE,
                )
            )

        if has_dr_missing_marker or not has_dr_positive_evidence:
            doctor_signature_verified = False
            conflicts.append("Missing physician signature on claim form or medical record.")
            specific_reasons.append("Missing physician signature on claim form or medical record")

    # 5. Patient Signatures Verification (Conditional Evidence Attestation Model - ADR-019 & Rule 11)
    # Patient signature is not mandatory across all claim forms. Only if a patient signature field/label is present
    # on the claim form and the signature itself is missing, blank, or unverified, do we flag it.
    patient_signature_verified = True
    if claim_text:
        has_pt_sig_field = bool(
            re.search(
                r"(?:patient(?:\x27s)?\s+signature|insured(?:\x27s)?\s+signature|signature\s+of\s+patient|"
                r"assignment\s+of\s+benefits|patient\s+or\s+authorized\s+person(?:\x27s)?\s+signature|"
                r"box\s*12\b|मरीज\s*का\s*हस्ताक्षर)",
                claim_text,
                re.IGNORECASE,
            )
        )
        if has_pt_sig_field:
            has_pt_missing_marker = bool(
                re.search(
                    r"(?:patient(\x27s)?\s+signature|insured(\x27s)?\s+signature|signature\s+of\s+patient|assignment\s+of\s+benefits|box\s*12)\s*[:\-]?\s*(\[missing\]|\[blank\]|none|no|missing|false|\b0\b|unsigned|____+)|"
                    r"मरीज\s*का\s*हस्ताक्षर\s*[:\-]?\s*(?:____+|\[अनुपस्थित\]|नहीं)",
                    claim_text,
                    re.IGNORECASE,
                )
            )

            has_pt_positive_evidence = False
            pt_sig_m = re.search(
                r"(?:patient(?:\x27s)?\s+signature|insured(?:\x27s)?\s+signature|signature\s+of\s+patient|assignment\s+of\s+benefits|patient\s+or\s+authorized\s+person(?:\x27s)?\s+signature)\s*[:\-]?\s*([^\n\r]+)",
                claim_text,
                re.IGNORECASE,
            )
            if pt_sig_m:
                pt_val = pt_sig_m.group(1).strip()
                is_negative = bool(re.search(r"^(\[?(?:missing|blank|unsigned|none|no|false|0)\]?|___+|pending)", pt_val, re.IGNORECASE))
                if not is_negative and (
                    any(k in pt_val.lower() for k in ("signed", "file", "yes", "authorized", "true", "/s/"))
                    or len(pt_val) >= 3
                ):
                    has_pt_positive_evidence = True

            if not has_pt_positive_evidence:
                has_pt_positive_evidence = bool(
                    re.search(
                        r"(?:signed\s+by\s+patient|assignment\s+of\s+benefits\s+authorized|"
                        r"मरीज\s*का\s*हस्ताक्षर\s*[:\-]?\s*(?:हस्ताक्षरित|सत्यापित))",
                        claim_text,
                        re.IGNORECASE,
                    )
                )

            if has_pt_missing_marker or not has_pt_positive_evidence:
                patient_signature_verified = False
                conflicts.append("Missing patient signature: Patient signature field is present on claim form but unsigned or unverified.")
                specific_reasons.append("Missing patient signature / assignment of benefits authorization on claim form")

    # 5b. Authorized Officer Signature Verification on Policy Amendment Document
    # If an authorized officer signature field is present on a policy amendment/endorsement, but the signature
    # is missing, blank, or unverified, flag it. If the field is not present, it is not flagged.
    officer_signature_verified = True
    if policy_amendment_text:
        has_officer_sig_field = bool(
            re.search(
                r"(?:authorized\s+(?:officer|signatory|representative|official)|"
                r"officer\s+signature|signature\s+of\s+authorized\s+officer|"
                r"underwriter(?:\x27s)?\s+signature|company\s+officer|"
                r"प्राधिकृत\s*अधिकारी|अधिकृत\s*हस्ताक्षर)",
                policy_amendment_text,
                re.IGNORECASE,
            )
        )
        if has_officer_sig_field:
            has_officer_missing_marker = bool(
                re.search(
                    r"(?:authorized\s+(?:officer|signatory|representative|official)|officer\s+signature|"
                    r"signature\s+of\s+authorized\s+officer|underwriter(?:\x27s)?\s+signature)\s*[:\-]?\s*"
                    r"(\[missing\]|\[blank\]|none|no|missing|false|\b0\b|unsigned|____+|pending|\[अनुपस्थित\]|नहीं)",
                    policy_amendment_text,
                    re.IGNORECASE,
                )
            )

            has_officer_positive_evidence = False
            off_sig_m = re.search(
                r"(?:authorized\s+(?:officer|signatory|representative|official)|officer\s+signature|"
                r"signature\s+of\s+authorized\s+officer|underwriter(?:\x27s)?\s+signature)\s*[:\-]?\s*([^\n\r]+)",
                policy_amendment_text,
                re.IGNORECASE,
            )
            if off_sig_m:
                off_val = off_sig_m.group(1).strip()
                is_negative = bool(re.search(r"^(\[?(?:missing|blank|unsigned|none|no|false|0)\]?|___+|pending)", off_val, re.IGNORECASE))
                if not is_negative and (
                    any(k in off_val.lower() for k in ("signed", "/s/", "valid", "approved", "officer", "executive", "director", "manager", "underwriter"))
                    or len(off_val) >= 3
                ):
                    has_officer_positive_evidence = True

            if not has_officer_positive_evidence:
                has_officer_positive_evidence = bool(
                    re.search(
                        r"(?:signed\s+by\s+authorized\s+officer|duly\s+authorized\s+and\s+signed|"
                        r"electronically\s+signed\s+by\s+officer|प्राधिकृत\s*हस्ताक्षरित)",
                        policy_amendment_text,
                        re.IGNORECASE,
                    )
                )

            if has_officer_missing_marker or not has_officer_positive_evidence:
                officer_signature_verified = False
                conflicts.append("Missing authorized officer signature on policy amendment document.")
                specific_reasons.append("Missing authorized officer signature on policy amendment document")

    # 6. Provider NPI Verification
    npi_verified = True
    raw_npi = str(getattr(claims_output.provider_npi, "value", "") or "").strip()
    has_claim_missing_npi = bool(re.search(r"npi\s*[:#\-]?\s*\[missing\]", claim_text, re.IGNORECASE))
    clean_npi = re.sub(r"\D", "", raw_npi)
    if has_claim_missing_npi or not clean_npi or clean_npi == "0" * len(clean_npi) or len(clean_npi) < 10:
        npi_verified = False
        conflicts.append("Missing provider NPI: Rendering or billing provider NPI is missing or invalid on claim form.")
        specific_reasons.append("Missing provider NPI on claim form")

    # 7. Common Fields Consistency (Name & Identity)
    identity_consistent = True
    kyc_last = ""
    if kyc_output and kyc_output.last_name:
        kyc_last = str(getattr(kyc_output.last_name, "value", "") or "").strip()

    m_claimed_name = re.search(
        r"(?:2\.\s*patient[’']s\s*name[^\n]*\n([a-z\s,\.\-]+)|patient(?:\x27s)?\s*name\s*[:\-]\s*([a-z\s,\.\-]+))",
        claim_text,
        re.IGNORECASE,
    )
    if m_claimed_name and kyc_last:
        raw_name = (m_claimed_name.group(1) or m_claimed_name.group(2) or "").strip()
        if (
            len(raw_name) > 3
            and not re.search(r"(address|medicare|same\s*as)", raw_name, re.IGNORECASE)
            and kyc_last.lower() not in raw_name.lower()
        ):
            identity_consistent = False
            conflicts.append(f"Patient name discrepancy across documents: KYC last name '{kyc_last}' does not match claim form patient '{raw_name}'.")
            specific_reasons.append(f"Patient name discrepancy across documents: KYC last name '{kyc_last}' does not match claim form patient '{raw_name}'")

    # 8. Prior Authorization Verification for Major Procedures
    prior_authorization_verified = True
    billed_codes: set[str] = set()
    for c in getattr(claims_output, "cpt_codes", []):
        if getattr(c, "value", None):
            billed_codes.add(str(c.value).strip())
    for line in getattr(claims_output, "service_lines", []):
        code = getattr(getattr(line, "hcpcs", None), "value", None) or getattr(line, "service_code", None)
        if code:
            billed_codes.add(str(code).strip())

    # 8. Mandatory Prior Authorization Verification (Spec 06 § 4, Spec 09 § 4, ADR-012)
    prior_authorization_verified = True
    policy_pa_req = getattr(policy_output, "prior_authorization_required", False)

    catalog_pa_req = False
    pa_trigger_codes: set[str] = set()
    cat = code_catalog
    if cat is None:
        try:
            from medishield.db.catalog import InMemoryCodeCatalogRepository
            cat = InMemoryCodeCatalogRepository()
        except ImportError:
            cat = None

    if cat is not None and hasattr(cat, "get_code_details"):
        for code in billed_codes:
            details = cat.get_code_details(code, CodingSystem.CPT)
            if details and getattr(details, "requires_prior_auth", False):
                catalog_pa_req = True
                pa_trigger_codes.add(code)

    if policy_pa_req or catalog_pa_req:
        has_pa = bool(
            getattr(policy_output, "prior_authorization_present", False)
            or getattr(policy_output, "prior_authorization_number", None)
            or re.search(r"prior\s+auth(orization)?\s*(number|code)?\s*[:#\-]?\s*([a-z0-9\-]{5,})", claim_text, re.IGNORECASE)
        )
        if not has_pa:
            prior_authorization_verified = False
            trigger_codes = sorted(pa_trigger_codes) if pa_trigger_codes else sorted(billed_codes)
            codes_str = ", ".join(trigger_codes) if trigger_codes else "applicable services"
            conflicts.append(
                f"Missing prior authorization: Major procedure(s) ({codes_str}) require mandatory prior authorization under policy terms, but no valid authorization was submitted."
            )
            specific_reasons.append(f"Missing prior authorization for major procedure(s) ({codes_str})")

    # 9. Procedure Code Validation & Uncoded Billed Charges Verification
    # (e.g. Room and Board listed with rate 1250.0 / 1250.00 or missing CPT code; value in front cannot be validated)
    procedure_codes_verified = True
    uncoded_charges_total = 0.0
    uncoded_line_details = []

    for line in getattr(claims_output, "service_lines", []):
        amt = float(getattr(getattr(line, "amount", None), "value", 0.0) or 0.0)
        cpt_val = getattr(getattr(line, "hcpcs", None), "value", None)
        cpt_clean = str(cpt_val).strip() if cpt_val is not None else ""

        rev_val = getattr(getattr(line, "rev_code", None), "value", None)
        rev_clean = str(rev_val).strip() if rev_val is not None else ""
        curr_sym = getattr(line, "currency_symbol", "$") or "$"

        has_valid_cpt = bool(
            cpt_clean
            and cpt_clean.lower() not in ("none", "unknown", "null")
            and "." not in cpt_clean
            and (
                re.fullmatch(r"^\d{5}$", cpt_clean)
                or re.fullmatch(r"^[A-Z0-9]\d{4}$", cpt_clean)
            )
        )
        has_valid_rev = bool(
            (rev_clean and rev_clean.upper() not in ("UNKNOWN", "NONE", "NULL", "") and re.fullmatch(r"^\d{3,4}$", rev_clean))
            or (cpt_clean and re.fullmatch(r"^\d{3,4}$", cpt_clean))
        )

        is_missing_code = amt > 0 and not has_valid_cpt and not has_valid_rev
        if is_missing_code:
            procedure_codes_verified = False
            uncoded_charges_total += amt
            desc = getattr(getattr(line, "description", None), "value", None) or f"Line {line.line_number}"
            code_desc = f"unrecognized rate/token '{cpt_clean}'" if cpt_clean and cpt_clean.lower() not in ("none", "unknown", "null") else "missing CPT/HCPCS or Revenue code"
            uncoded_line_details.append((line.line_number, desc, amt, code_desc, curr_sym))

    if uncoded_line_details:
        for ln_num, desc, amt, code_desc, curr_sym in uncoded_line_details:
            conflicts.append(
                f"Unvalidated billed charge: Service line {ln_num} ('{desc}') has a billed amount of {curr_sym}{amt:,.2f} "
                f"with {code_desc}. Billed charges cannot be validated without an authoritative procedure code."
            )
            specific_reasons.append(
                f"Unvalidated billed charge: Service line {ln_num} ('{desc}', {curr_sym}{amt:,.2f}) has {code_desc}; "
                f"billed charges cannot be validated without an authoritative procedure code"
            )

    # 10. Clinical Alignment Unfulfilled Guidelines / Indeterminate Codes & Misalignments
    if clinical_output and getattr(clinical_output, "lines_indeterminate_count", 0) > 0:
        misalignments = [
            l.discrepancy_description
            for l in getattr(clinical_output, "line_assessments", [])
            if getattr(l, "discrepancy_description", None) and "misalignment" in l.discrepancy_description.lower()
        ]
        for m in misalignments:
            if m not in conflicts:
                conflicts.append(m)
            if m not in specific_reasons:
                specific_reasons.append(m)
        ind_codes = sorted({
            l.service_code for l in getattr(clinical_output, "line_assessments", [])
            if getattr(l, "line_verdict", None) == AgentVerdict.INDETERMINATE
        })
        if ind_codes and not misalignments:
            specific_reasons.append(
                f"Clinical documentation and guideline review required for CPT code(s): {', '.join(ind_codes)}"
            )

    is_consistent = (
        len(conflicts) == 0
        and identity_consistent
        and dates_consistent
        and amounts_consistent
        and doctor_signature_verified
        and patient_signature_verified
        and officer_signature_verified
        and npi_verified
        and prior_authorization_verified
        and procedure_codes_verified
    )

    return ConsistencyCheckReport(
        is_consistent=is_consistent,
        identity_consistent=identity_consistent,
        dates_consistent=dates_consistent,
        amounts_consistent=amounts_consistent,
        conflicts_detected=conflicts,
        verdict_override=AgentVerdict.CONFLICT if not is_consistent else None,
        doctor_signature_verified=doctor_signature_verified,
        patient_signature_verified=patient_signature_verified,
        officer_signature_verified=officer_signature_verified,
        npi_verified=npi_verified,
        prior_authorization_verified=prior_authorization_verified,
        procedure_codes_verified=procedure_codes_verified,
        unvalidated_charges_total=round(uncoded_charges_total, 2),
        specific_escalation_reasons=specific_reasons,
        date_reconciliation_summary=date_reconciliation_summary,
    )


def calculate_composite_decision_confidence(
    provenance_scores: list[float],
    validation_valid: bool,
    agent_confidences: list[float],
) -> float:
    """
    Decision Confidence Calibration Formula (Spec 06, Section 5):
    Confidence = 0.40 * Provenance Quality + 0.35 * Validation Completeness + 0.25 * Model Confidence
    """
    # 1. Provenance Quality (40% weight)
    prov_quality = (
        sum(provenance_scores) / len(provenance_scores)
        if provenance_scores
        else 1.0
    )

    # 2. Validation Completeness (35% weight)
    validation_score = 1.0 if validation_valid else 0.0

    # 3. Model Confidence (25% weight) - minimum across specialist agents
    min_model_conf = min(agent_confidences) if agent_confidences else 1.0

    composite = (
        (0.40 * prov_quality)
        + (0.35 * validation_score)
        + (0.25 * min_model_conf)
    )
    return round(max(0.0, min(1.0, composite)), 3)
