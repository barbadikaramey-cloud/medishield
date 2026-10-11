"""Unit tests for OCR reconciliation, geometric extraction, and claims validation (Spec 05 § 5)."""


from medishield.agents.claims import ClaimsAgent
from medishield.db.enums import AgentVerdict
from medishield.domain.contracts import (
    OCRBoundingBox,
    OCRDocumentPayload,
    OCRPagePayload,
    OCRTextSpan,
    ServiceLineItem,
)
from medishield.domain.provenance import ProvenanceType, ProvenanceValue
from medishield.ocr.ocr_reconcile import (
    SYNTHETIC_VALID_CPT_CODES,
    ArithmeticCheckStatus,
    ReconcileStatus,
    evaluate_claim_totals,
    extract_cpt_codes_from_table,
    extract_money_value_geometrically,
    reconcile_field_reading,
)


def _make_span(
    span_id: str,
    text: str,
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
    conf: float = 0.98,
) -> OCRTextSpan:
    return OCRTextSpan(
        span_id=span_id,
        page_number=1,
        text=text,
        confidence=conf,
        bbox=OCRBoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max),
    )


def _make_doc(spans: list[OCRTextSpan], doc_id: str = "DOC-TEST-01") -> OCRDocumentPayload:
    return OCRDocumentPayload(
        document_id=doc_id,
        total_pages=1,
        pages=[
            OCRPagePayload(
                page_number=1,
                width_px=1000,
                height_px=1400,
                raw_text="\n".join(s.text for s in spans),
                spans=spans,
            )
        ],
    )


# ── Test 1: "48 NON-COV CHARGES" gives no amount ─────────────────────────────


def test_box_number_48_non_cov_charges_gives_no_amount() -> None:
    """Proves that '48 NON-COV CHARGES' is recognized as a box number/non-total and yields no amount."""
    span48 = _make_span("span-0044", "48 NON-COV CHARGES", 0.7765, 0.3145, 0.9529, 0.3273)
    doc = _make_doc([span48])
    spans_with_doc = [(doc, 1, span48)]

    pv, amt, has_label = extract_money_value_geometrically(spans_with_doc)

    assert pv is None
    assert amt is None
    assert has_label is False  # "NON-COV CHARGES" is not a total charges label


# ── Test 2: UB-04 Box 47 header finds $4,095.00 in the column below ──────────


def test_geometric_extraction_finds_total_in_column_below() -> None:
    """Proves that '47TOTALCHARGES' locates '$4,095.00' located vertically below in the same column."""
    header_span = _make_span("span-0043", "47TOTALCHARGES", 0.6235, 0.3141, 0.7788, 0.3273)
    line1 = _make_span("span-0049", "$3,750.00", 0.6235, 0.3809, 0.7012, 0.3945)
    line2 = _make_span("span-0054", "$250.00", 0.6235, 0.4200, 0.6800, 0.4336)
    line3 = _make_span("span-0059", "$95.00", 0.6235, 0.4500, 0.6700, 0.4636)
    total_span = _make_span("span-0067", "$4,095.00", 0.6230, 0.4930, 0.7100, 0.5060)

    doc = _make_doc([header_span, line1, line2, line3, total_span])
    spans_with_doc = [(doc, 1, s) for s in [header_span, line1, line2, line3, total_span]]

    pv, amt, has_label = extract_money_value_geometrically(spans_with_doc)

    assert has_label is True
    assert amt == 4095.00
    assert pv is not None
    assert pv.value == 4095.00
    assert pv.provenance.span_id == "span-0067"


# ── Test 3: CPT excludes ZIP codes and IDs ───────────────────────────────────


def test_cpt_extraction_excludes_zip_codes_and_identifiers() -> None:
    """Proves that address ZIP codes (60611, 60601) and group IDs (89131, 00001) are excluded."""
    spans = [
        # Address block
        _make_span("s1", "123 N Michigan Ave, Chicago, IL 60611", 0.05, 0.10, 0.45, 0.12),
        _make_span("s2", "Billing Address: Chicago IL 60601", 0.05, 0.13, 0.45, 0.15),
        # Identifiers
        _make_span("s3", "Group ID: GRP-89131", 0.50, 0.10, 0.80, 0.12),
        _make_span("s4", "Form Revision: 00001", 0.50, 0.13, 0.80, 0.15),
        # Legitimate procedure table
        _make_span("s5", "1. 90837 Psychotherapy 60min", 0.05, 0.40, 0.50, 0.42),
        _make_span("s6", "2. 93000 Routine Electrocardiogram", 0.05, 0.44, 0.50, 0.46),
    ]
    doc = _make_doc(spans)
    spans_with_doc = [(doc, 1, s) for s in spans]

    extracted_cpts = extract_cpt_codes_from_table(spans_with_doc, SYNTHETIC_VALID_CPT_CODES)
    extracted_values = [c.value for c in extracted_cpts]

    # Must contain legitimate clinical CPT codes
    assert "90837" in extracted_values
    assert "93000" in extracted_values

    # Must NOT contain ZIPs or form IDs
    assert "60611" not in extracted_values
    assert "60601" not in extracted_values
    assert "89131" not in extracted_values
    assert "00001" not in extracted_values


# ── Test 4: Total mismatch gives CONFLICT / ESCALATE (Never auto-REJECT) ─────


def test_total_mismatch_gives_conflict_and_fails_arithmetic() -> None:
    """Proves that when sum of lines differs from declared total, arithmetic check fails but yields CONFLICT/ESCALATE."""
    agent = ClaimsAgent()
    spans = [
        _make_span("s1", "NPI: 1234567890", 0.05, 0.10, 0.30, 0.12),
        _make_span("s2", "TOTAL CHARGE: $500.00", 0.60, 0.50, 0.90, 0.52),
        _make_span("s3", "1. 99213 Office Visit 2026-03-15 1.0 $100.00 $100.00", 0.05, 0.30, 0.80, 0.32),
        _make_span("s4", "2. 93000 Routine ECG 2026-03-15 1.0 $150.00 $150.00", 0.05, 0.34, 0.80, 0.36),
    ]
    doc = _make_doc(spans)
    out = agent.execute("case-mismatch", [doc])

    # Sum is 250.00, header declared 500.00
    assert out.line_items_sum_valid is False
    assert out.verdict == AgentVerdict.CONFLICT  # Rule: Escalate for human review, never REJECT
    assert any("ARITHMETIC_DRIFT" in err for err in out.validation_errors)


# ── Test 5: Label present but value missing gives INDETERMINATE (Not derived) 


def test_total_label_present_without_value_gives_indeterminate() -> None:
    """Proves that when form contains a 'TOTAL CHARGES' label but no amount, it is INDETERMINATE and NOT auto-derived."""
    spans = [
        _make_span("s1", "TOTAL CHARGES:", 0.60, 0.50, 0.80, 0.52),
        # Notice no amount next to or below label
    ]
    doc = _make_doc(spans)
    pv, amt, has_label = extract_money_value_geometrically([(doc, 1, spans[0])])

    assert has_label is True
    assert amt is None

    line = ServiceLineItem(
        line_number=1,
        cpt_code=ProvenanceValue(value="99213", confidence=0.95),
        description=ProvenanceValue(value="Visit", confidence=0.95),
        service_date=ProvenanceValue(value="2026-03-15", confidence=0.95),
        quantity=ProvenanceValue(value=1.0, confidence=0.95),
        unit_charge=ProvenanceValue(value=150.00, confidence=0.95),
        total_charge=ProvenanceValue(value=150.00, confidence=0.95),
    )

    final_pv, final_amt, status, errs = evaluate_claim_totals(
        declared_total=amt,
        declared_total_pv=pv,
        service_lines=[line],
        has_total_label=has_label,
    )

    assert status == ArithmeticCheckStatus.INDETERMINATE
    assert final_pv is None
    assert final_amt is None
    assert any("EXTRACTION_FAILURE" in e for e in errs)


# ── Test 6: Form without total label allows DERIVED total ────────────────────


def test_form_without_total_label_allows_derived_total() -> None:
    """Proves that a form with NO total label derives total with NOT_APPLICABLE arithmetic check and reduced quality score."""
    line1 = ServiceLineItem(
        line_number=1,
        cpt_code=ProvenanceValue(value="99213", confidence=0.90),
        description=ProvenanceValue(value="Visit", confidence=0.90),
        service_date=ProvenanceValue(value="2026-03-15", confidence=0.90),
        quantity=ProvenanceValue(value=1.0, confidence=0.90),
        unit_charge=ProvenanceValue(value=100.00, confidence=0.90),
        total_charge=ProvenanceValue(value=100.00, confidence=0.90),
    )
    line2 = ServiceLineItem(
        line_number=2,
        cpt_code=ProvenanceValue(value="93000", confidence=0.85),
        description=ProvenanceValue(value="ECG", confidence=0.85),
        service_date=ProvenanceValue(value="2026-03-15", confidence=0.85),
        quantity=ProvenanceValue(value=1.0, confidence=0.85),
        unit_charge=ProvenanceValue(value=50.00, confidence=0.85),
        total_charge=ProvenanceValue(value=50.00, confidence=0.85),
    )

    final_pv, final_amt, status, _errs = evaluate_claim_totals(
        declared_total=None,
        declared_total_pv=None,
        service_lines=[line1, line2],
        has_total_label=False,  # NO total label on the form
    )

    assert status == ArithmeticCheckStatus.NOT_APPLICABLE
    assert final_amt == 150.00
    assert final_pv is not None
    assert final_pv.value == 150.00
    assert final_pv.confidence == 0.85  # min(line confidences)
    assert final_pv.provenance.provenance_type == ProvenanceType.INFERRED
    assert final_pv.provenance.evidence_quality_score == 0.70


# ── Test 7: VLM ungrounded value is rejected ─────────────────────────────────


def test_vlm_ungrounded_value_is_rejected() -> None:
    """Proves that a VLM candidate citing a non-existent span or hallucinated value is marked VLM_UNGROUNDED."""
    spans_by_id = {
        "span-1": "PATIENT NAME: John Doe",
        "span-2": "TOTAL CHARGES: $150.00",
    }

    # Hallucinated span ID
    status, val, _cited_id = reconcile_field_reading(
        field_name="total_amount",
        ocr_value=None,
        ocr_span_id=None,
        vlm_candidate={"value": 150.00, "span_id": "span-999"},
        all_spans_by_id=spans_by_id,
    )
    assert status == ReconcileStatus.VLM_UNGROUNDED
    assert val is None

    # Grounded span ID citing real span
    status_g, val_g, cited_id_g = reconcile_field_reading(
        field_name="total_amount",
        ocr_value=None,
        ocr_span_id=None,
        vlm_candidate={"value": 150.00, "span_id": "span-2"},
        all_spans_by_id=spans_by_id,
    )
    assert status_g == ReconcileStatus.VLM_ONLY_GROUNDED
    assert val_g == 150.00
    assert cited_id_g == "span-2"


def test_geometric_extraction_prefers_dollar_over_bare_decimal() -> None:
    """Proves that $4,095.00 is chosen over bare diagnosis code decimals like 125.10."""
    page_totals_span = _make_span("span-0067", "PAGETOTALS", 0.0814, 0.4817, 0.1477, 0.5004)
    dollar_total = _make_span("span-0068", "$4,095.00", 0.6227, 0.4947, 0.6785, 0.5175)
    bare_diag_code = _make_span("span-0082", "125.10", 0.0355, 0.6450, 0.0738, 0.6621)

    doc = _make_doc([page_totals_span, dollar_total, bare_diag_code])
    spans = [(doc, 1, s) for s in [page_totals_span, dollar_total, bare_diag_code]]

    pv, amt, has_label = extract_money_value_geometrically(spans)
    assert has_label is True
    assert amt == 4095.00
    assert pv is not None
    assert pv.value == 4095.00
    assert pv.provenance.span_id == "span-0068"


def test_extract_geometric_key_value_pairs_box_and_colon() -> None:
    """Proves that geometric key-value pairing extracts both box format and inline colon format."""
    from medishield.ocr.preprocessor import extract_geometric_key_value_pairs

    name_label = _make_span("s-1", "8PATIENT NAME(Last,First,MI)", 0.039, 0.187, 0.139, 0.207)
    name_val = _make_span("s-2", "Christopher Harris I.", 0.040, 0.210, 0.163, 0.230)
    dob_label = _make_span("s-3", "10 BIRTHDATE", 0.533, 0.198, 0.584, 0.218)
    dob_val = _make_span("s-4", "04/25/1954", 0.534, 0.218, 0.596, 0.240)
    inline_npi = _make_span("s-5", "NPI: NPI-5610293847", 0.039, 0.160, 0.133, 0.180)

    spans = [name_label, name_val, dob_label, dob_val, inline_npi]
    add_spans, kv_pairs = extract_geometric_key_value_pairs(spans, page_num=1)

    # Check key-value records
    kv_map = {kv["key"]: kv["value"] for kv in kv_pairs}
    assert "PATIENT NAME" in kv_map or any("PATIENT NAME" in k for k in kv_map)
    assert any("Christopher Harris" in v for v in kv_map.values())
    assert any("04/25/1954" in v for v in kv_map.values())
    assert any("5610293847" in v for v in kv_map.values())

    # Check that -k and -v spans were generated
    span_ids = [s.span_id for s in add_spans]
    assert any(sid.endswith("-k") for sid in span_ids)
    assert any(sid.endswith("-v") for sid in span_ids)
