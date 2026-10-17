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
    ResilientVisionClient,
    VLMProviderError,
    evaluate_claim_totals,
    extract_cpt_codes_from_table,
    extract_money_value_geometrically,
    extract_service_lines_from_table,
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


def test_extract_cpt_codes_preserves_unsupported_codes() -> None:
    """Proves that 5-digit CPT candidates survive extraction and unsupported codes get calibrated confidence."""
    spans = [
        _make_span("s1", "CPT/HCPCS: 59400", 0.05, 0.30, 0.35, 0.32),
        _make_span("s2", "PROCEDURE 27447 KNEE ARTHROPLASTY", 0.05, 0.35, 0.50, 0.37),
        _make_span("s3", "LINE 3: 99999 UNKNOWN EXPERIMENTAL", 0.05, 0.40, 0.50, 0.42),
        _make_span("s4", "LINE 4: 99213 OFFICE VISIT", 0.05, 0.45, 0.50, 0.47),
    ]
    doc = _make_doc(spans)
    extracted = extract_cpt_codes_from_table([(doc, 1, s) for s in spans])

    extracted_values = [p.value for p in extracted]
    assert "59400" in extracted_values
    assert "27447" in extracted_values
    assert "99999" in extracted_values
    assert "99213" in extracted_values

    # Supported codes get high confidence, unsupported codes get calibrated candidate confidence
    val_map = {p.value: p for p in extracted}
    assert val_map["99213"].confidence == 0.96
    assert val_map["59400"].confidence == 0.70
    assert val_map["27447"].confidence == 0.70
    assert val_map["99999"].confidence == 0.70



def test_extract_cpt_codes_ignores_prescriptions_and_ids() -> None:
    """Proves that prescription and ID documents are ignored to prevent barcode/NDC/DL leakage into CPT codes."""
    rx_spans = [_make_span("s1", "RX NUMBER 88671:0", 0.05, 0.40, 0.50, 0.42)]
    id_spans = [_make_span("s2", "ZIP 60611 LIC 12345", 0.05, 0.40, 0.50, 0.42)]
    rx_doc = _make_doc(rx_spans, doc_id="rx_PT_12345")
    id_doc = _make_doc(id_spans, doc_id="id_PT_12345")

    extracted = extract_cpt_codes_from_table([
        (rx_doc, 1, rx_spans[0]),
        (id_doc, 1, id_spans[0]),
    ])
    assert len(extracted) == 0



def test_is_plausible_revenue_code() -> None:
    """Proves that calendar years like 2025 are rejected as revenue codes, while UB-04 codes pass."""
    from medishield.ocr.ocr_reconcile import is_plausible_revenue_code

    assert is_plausible_revenue_code("0110") is True
    assert is_plausible_revenue_code("0120") is True
    assert is_plausible_revenue_code("250") is True
    assert is_plausible_revenue_code("2025") is False
    assert is_plausible_revenue_code("2024") is False
    assert is_plausible_revenue_code("0") is False
    assert is_plausible_revenue_code("abc") is False


def test_extract_service_lines_ignores_discharge_rx_and_ids() -> None:
    """Proves that discharge summaries, prescriptions, and ID cards never emit billing service lines."""
    rx_span = _make_span("rx-1", "RX 88671:0 AMOXICILLIN 500MG $45.00", 0.1, 0.35, 0.8, 0.37)
    id_span = _make_span("id-1", "LIC 12345 FEE $25.00 01/01/2025", 0.1, 0.40, 0.8, 0.42)
    discharge_span = _make_span("dc-1", "DISCHARGE SUMMARY TOTAL ESTIMATE $1,500.00", 0.1, 0.45, 0.8, 0.47)

    rx_doc = _make_doc([rx_span], doc_id="rx_PT_001")
    id_doc = _make_doc([id_span], doc_id="id_PT_001")
    dc_doc = _make_doc([discharge_span], doc_id="discharge_PT_001")

    spans = [
        (rx_doc, 1, rx_span),
        (id_doc, 1, id_span),
        (dc_doc, 1, discharge_span),
    ]

    lines = extract_service_lines_from_table(spans)
    assert len(lines) == 0


def test_extract_service_lines_excludes_total_charges_row() -> None:
    """Proves that summary total rows (e.g. 28. TOTAL CHARGE) are excluded from individual service lines."""
    line1_date = _make_span("s1", "11/15/2025", 0.05, 0.65, 0.15, 0.66)
    line1_cpt = _make_span("s2", "99213", 0.25, 0.65, 0.35, 0.66)
    line1_amt = _make_span("s3", "$150.00", 0.60, 0.65, 0.70, 0.66)

    total_label = _make_span("s4", "28. TOTAL CHARGES:", 0.45, 0.84, 0.58, 0.86)
    total_amt = _make_span("s5", "$150.00", 0.65, 0.84, 0.75, 0.86)

    claim_doc = _make_doc([line1_date, line1_cpt, line1_amt, total_label, total_amt], doc_id="claim_PT_001")
    spans = [(claim_doc, 1, s) for s in [line1_date, line1_cpt, line1_amt, total_label, total_amt]]

    lines = extract_service_lines_from_table(spans)
    assert len(lines) == 1
    assert lines[0].amount.value == 150.00
    assert lines[0].hcpcs.value == "99213"


def test_claims_agent_document_routing_and_isolation() -> None:
    """Proves that ClaimsAgent routes financial fields and service lines strictly from claim forms."""
    claim_line_cpt = _make_span("cl-1", "90837", 0.25, 0.65, 0.35, 0.66)
    claim_line_amt = _make_span("cl-2", "$250.00", 0.60, 0.65, 0.70, 0.66)
    claim_tot_label = _make_span("cl-3", "TOTAL CHARGE:", 0.50, 0.84, 0.62, 0.86)
    claim_tot_amt = _make_span("cl-4", "$250.00", 0.65, 0.84, 0.75, 0.86)
    claim_npi = _make_span("cl-5", "NPI: 1234567890", 0.05, 0.90, 0.25, 0.92)

    discharge_diag = _make_span("dc-1", "DIAGNOSIS: ICD-10 I21.9", 0.05, 0.30, 0.40, 0.32)
    discharge_date = _make_span("dc-2", "ADMISSION DATE: 11/15/2025", 0.05, 0.35, 0.40, 0.37)

    id_name = _make_span("id-1", "JOHN DOE DL 99999999", 0.05, 0.20, 0.40, 0.22)
    id_fake_amt = _make_span("id-2", "$5000.00", 0.60, 0.40, 0.75, 0.42)

    claim_doc = _make_doc([claim_line_cpt, claim_line_amt, claim_tot_label, claim_tot_amt, claim_npi], doc_id="claim_PT_999")
    discharge_doc = _make_doc([discharge_diag, discharge_date], doc_id="discharge_PT_999")
    id_doc = _make_doc([id_name, id_fake_amt], doc_id="id_PT_999")

    agent = ClaimsAgent(llm_client=None)
    out = agent.execute("CASE-TEST", [id_doc, discharge_doc, claim_doc])

    assert out.claim_amount is not None
    assert out.claim_amount.value == 250.00
    assert len(out.service_lines) == 1
    assert out.service_lines[0].amount.value == 250.00
    assert out.provider_npi.value == "1234567890"
    assert any(c.value == "I21.9" for c in out.icd10_codes)
    assert any(c.value == "90837" for c in out.cpt_codes)
    assert out.verdict == AgentVerdict.PASS


def test_resilient_vision_client_token_expired_fallback() -> None:
    """Proves that ResilientVisionClient catches 401 token expired on primary and cascades to secondary."""
    class MockPrimaryExpired:
        def is_available(self) -> bool:
            return True

        def read_fields(self, image_bytes: bytes, spans_with_ids: list, field_queries: list):
            raise VLMProviderError("Gemini", "TOKEN_EXPIRED", 401, "API key expired")

    class MockSecondaryActive:
        def is_available(self) -> bool:
            return True

        def read_fields(self, image_bytes: bytes, spans_with_ids: list, field_queries: list):
            return {"total_amount": {"value": 350.0, "span_id": "span-1"}}

    client = ResilientVisionClient(
        gemini_client=MockPrimaryExpired(),
        hf_client=MockSecondaryActive(),
        groq_client=None,
    )
    res = client.read_fields(b"", [], ["total_amount"])
    assert res == {"total_amount": {"value": 350.0, "span_id": "span-1"}}


def test_resilient_vision_client_rate_limit_fallback() -> None:
    """Proves that ResilientVisionClient catches 429 rate limit on secondary and cascades to tertiary."""
    class MockPrimaryUnavailable:
        def is_available(self) -> bool:
            return False

        def read_fields(self, image_bytes: bytes, spans_with_ids: list, field_queries: list):
            return {}

    class MockSecondaryRateLimited:
        def is_available(self) -> bool:
            return True

        def read_fields(self, image_bytes: bytes, spans_with_ids: list, field_queries: list):
            raise VLMProviderError("HuggingFace", "RATE_LIMIT_EXCEEDED", 429, "TPM exhausted")

    class MockTertiaryActive:
        def is_available(self) -> bool:
            return True

        def read_fields(self, image_bytes: bytes, spans_with_ids: list, field_queries: list):
            return {"total_amount": {"value": 900.0, "span_id": "span-2"}}

    client = ResilientVisionClient(
        gemini_client=MockPrimaryUnavailable(),
        hf_client=MockSecondaryRateLimited(),
        groq_client=MockTertiaryActive(),
    )
    res = client.read_fields(b"", [], ["total_amount"])
    assert res == {"total_amount": {"value": 900.0, "span_id": "span-2"}}


def test_resilient_vision_client_all_exhausted_returns_empty() -> None:
    """Proves that when all VLM providers fail or tokens expire, it safely returns empty dict without crashing."""
    class MockPrimaryFailed:
        def is_available(self) -> bool:
            return True

        def read_fields(self, image_bytes: bytes, spans_with_ids: list, field_queries: list):
            raise VLMProviderError("Gemini", "TOKEN_EXPIRED", 401, "Expired")

    class MockSecondaryFailed:
        def is_available(self) -> bool:
            return True

        def read_fields(self, image_bytes: bytes, spans_with_ids: list, field_queries: list):
            raise VLMProviderError("HuggingFace", "RATE_LIMIT_EXCEEDED", 429, "Quota")

    class MockTertiaryFailed:
        def is_available(self) -> bool:
            return True

        def read_fields(self, image_bytes: bytes, spans_with_ids: list, field_queries: list):
            raise VLMProviderError("Groq", "RATE_LIMIT_EXCEEDED", 429, "TPD")

    client = ResilientVisionClient(
        gemini_client=MockPrimaryFailed(),
        hf_client=MockSecondaryFailed(),
        groq_client=MockTertiaryFailed(),
    )
    res = client.read_fields(b"", [], ["total_amount"])
    assert res == {}


def test_extract_service_lines_excludes_box55_estimated_amt_due_and_post_totals_rows() -> None:
    """Proves that Box 55 ESTIMATED AMT DUE and rows below PAGE TOTALS are excluded from service lines."""
    # Line 1: Room & Board $3,750.00 at y ~ 0.35
    l1_rev = _make_span("s1", "0120", 0.05, 0.34, 0.15, 0.36)
    l1_desc = _make_span("s2", "Room & Board - Semi-Private", 0.20, 0.34, 0.45, 0.36)
    l1_amt = _make_span("s3", "$3,750.00", 0.60, 0.34, 0.70, 0.36)

    # Line 2: Psychotherapy CPT 90837 $250.00 at y ~ 0.38
    l2_rev = _make_span("s4", "0490", 0.05, 0.38, 0.15, 0.40)
    l2_desc = _make_span("s5", "Psychotherapy Session, 60 min", 0.20, 0.38, 0.45, 0.40)
    l2_cpt = _make_span("s6", "90837", 0.48, 0.38, 0.55, 0.40)
    l2_amt = _make_span("s7", "$250.00", 0.60, 0.38, 0.70, 0.40)

    # PAGE TOTALS at y ~ 0.53
    tot_label = _make_span("s8", "PAGE TOTALS", 0.08, 0.53, 0.18, 0.55)
    tot_amt = _make_span("s9", "$4,000.00", 0.60, 0.53, 0.70, 0.55)

    # Box 55 ESTIMATED AMT DUE at y ~ 0.57 - 0.60
    b55_label = _make_span("s10", "55 ESTIMATED AMT DUE", 0.70, 0.565, 0.85, 0.58)
    b50_payer = _make_span("s11", "MediShield Health Ins. Ltd.", 0.03, 0.585, 0.20, 0.60)
    b56_npi = _make_span("s12", "NPI-4756102938", 0.35, 0.585, 0.50, 0.60)
    b55_amt = _make_span("s13", "$4,000.00", 0.70, 0.585, 0.85, 0.60)

    # Box 74 PRINCIPAL PROCEDURE at y ~ 0.75
    b74_label = _make_span("s14", "74 PRINCIPAL PROCEDURE (CPT)", 0.05, 0.74, 0.30, 0.76)
    b74_cpt = _make_span("s15", "CPT 90837", 0.05, 0.76, 0.20, 0.78)

    spans = [
        l1_rev, l1_desc, l1_amt,
        l2_rev, l2_desc, l2_cpt, l2_amt,
        tot_label, tot_amt,
        b55_label, b50_payer, b56_npi, b55_amt,
        b74_label, b74_cpt,
    ]
    doc = _make_doc(spans, doc_id="claim_PT_TEST")
    doc_spans = [(doc, 1, s) for s in spans]

    lines = extract_service_lines_from_table(doc_spans)
    # Must only extract the 2 actual service lines, excluding PAGE TOTALS and Box 55
    assert len(lines) == 2
    assert lines[0].amount.value == 3750.00
    assert lines[1].amount.value == 250.00
    assert sum(l.amount.value for l in lines) == 4000.00


def test_extract_table_cpt_row_occurrences_excludes_box74_principal_procedure() -> None:
    """Proves that Box 74 PRINCIPAL PROCEDURE CPT code below table is not counted as duplicate table row."""
    from medishield.agents.claims import ClaimsAgent

    # Table row CPT 90837 at y ~ 0.38
    t_cpt = _make_span("s1", "90837", 0.48, 0.38, 0.55, 0.40)
    # PAGE TOTALS at y ~ 0.53
    tot_label = _make_span("s2", "PAGE TOTALS", 0.08, 0.53, 0.18, 0.55)
    # Box 74 Principal Procedure CPT 90837 at y ~ 0.75
    b74_label = _make_span("s3", "74 PRINCIPAL PROCEDURE (CPT)", 0.05, 0.74, 0.30, 0.76)
    b74_cpt = _make_span("s4", "CPT 90837", 0.05, 0.76, 0.20, 0.78)

    spans = [t_cpt, tot_label, b74_label, b74_cpt]
    doc = _make_doc(spans, doc_id="claim_PT_TEST")
    doc_spans = [(doc, 1, s) for s in spans]

    agent = ClaimsAgent()
    occurrences = agent._extract_table_cpt_row_occurrences(doc_spans)
    # Should only find 1 table occurrence (at y=0.39), not 2
    assert len(occurrences) == 1
    assert occurrences[0][0] == "90837"
    assert occurrences[0][1] == "s1"


def test_extract_service_lines_preserves_cms1500_rows_with_rendering_provider_npi() -> None:
    """Proves that CMS-1500 service lines containing Box 24J rendering provider NPI (e.g. NPI-7293847561)
    are preserved as valid service lines and not dropped by payer metadata filters."""
    line1_date = _make_span("s1", "09/25/2025", 0.06, 0.65, 0.12, 0.66)
    line1_cpt = _make_span("s2", "36415", 0.26, 0.65, 0.30, 0.66)
    line1_desc = _make_span("s3", "Collection of venous blood", 0.35, 0.65, 0.53, 0.66)
    line1_amt = _make_span("s4", "$35.00", 0.58, 0.65, 0.62, 0.66)
    line1_npi = _make_span("s5", "NPI-7293847561", 0.78, 0.65, 0.86, 0.66)

    total_label = _make_span("s6", "28. TOTAL CHARGE:", 0.52, 0.84, 0.62, 0.86)
    total_amt = _make_span("s7", "$35.00", 0.67, 0.84, 0.76, 0.86)

    spans = [line1_date, line1_cpt, line1_desc, line1_amt, line1_npi, total_label, total_amt]
    claim_doc = _make_doc(spans, doc_id="claim_PT_39451")
    doc_spans = [(claim_doc, 1, s) for s in spans]

    lines = extract_service_lines_from_table(doc_spans)
    assert len(lines) == 1
    assert lines[0].hcpcs.value == "36415"
    assert lines[0].amount.value == 35.00



