"""Unit tests for Phase 2C: VLM-Assisted Visual Cross-Check and Recovery (Spec 05 § 5, Spec 06 § 2).

Verifies the bounded recovery pipeline:
  Stage A: Standard OCR & deterministic extraction (y_tolerance=0.012)
  Stage B: Deterministic retry (y_tolerance=0.024)
  Stage C: VLM-assisted visual cross-check with original document image / crop
  Stage D: Typed visual-evidence contract and candidate verification
  Stage E: Independent cross-check via targeted local OCR reread
"""

from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image, ImageDraw

from medishield.agents.claims import ClaimsAgent
from medishield.db.enums import AgentVerdict, BusinessDecision, DocumentType
from medishield.domain.contracts import (
    FraudAgentOutput,
    KYCAgentOutput,
    OCRBoundingBox,
    OCRDocumentPayload,
    OCRPagePayload,
    OCRTextSpan,
    PolicyCoverageOutput,
    RiskLevel,
    VisualEvidenceCandidate,
    VisualEvidenceStatus,
)
from medishield.domain.decision import (
    DecisionInputContext,
    DeterministicDecisionEngine,
)
from medishield.domain.provenance import BBox, ProvenanceType
from medishield.intake.storage import InMemoryStorageBackend
from medishield.ocr.ocr_reconcile import (
    ResilientVisionClient,
)

DATASET_ROOT = Path(__file__).resolve().parent.parent.parent / "dataset"


# ── Synthetic Fixture Helpers ─────────────────────────────────────────────────


def _create_synthetic_claim_image(
    line1_text: str = "99213 Office Visit $150.00",
    line2_text: str = "93000 Routine ECG $50.00",
    total_text: str = "TOTAL CHARGE: $200.00",
    width: int = 1000,
    height: int = 1200,
) -> bytes:
    """Generates a real in-memory PNG image of a medical claim form with readable text."""
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)

    # Header section (y in 50..200)
    draw.text((50, 50), "HEALTH INSURANCE CLAIM FORM", fill="black")
    draw.text((50, 90), "PROVIDER: Dr. Sarah Jenkins Hospital", fill="black")
    draw.text((50, 130), "NPI: 1234567890", fill="black")
    draw.text((50, 170), "DATE OF SERVICE: 2026-09-15", fill="black")
    draw.text((50, 210), "DIAGNOSIS: ICD-10 J06.9", fill="black")

    # Billing table header (y = 350)
    draw.text((50, 350), "CPT/HCPCS   DESCRIPTION                   CHARGES", fill="black")

    # Itemized rows (y in 400..600)
    if line1_text:
        draw.text((50, 420), line1_text, fill="black")
    if line2_text:
        draw.text((50, 480), line2_text, fill="black")

    # Total charge (y = 900)
    if total_text:
        draw.text((500, 900), total_text, fill="black")

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_span(
    span_id: str,
    text: str,
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
    conf: float = 0.98,
    page_number: int = 1,
) -> OCRTextSpan:
    return OCRTextSpan(
        span_id=span_id,
        page_number=page_number,
        text=text,
        confidence=conf,
        bbox=OCRBoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max),
    )


def _make_doc(
    spans: list[OCRTextSpan],
    doc_id: str = "claim_PT_TEST.png",
) -> OCRDocumentPayload:
    return OCRDocumentPayload(
        document_id=doc_id,
        total_pages=1,
        pages=[
            OCRPagePayload(
                page_number=1,
                width_px=1000,
                height_px=1200,
                raw_text="\n".join(s.text for s in spans),
                spans=spans,
            )
        ],
    )


# ── Stage A: Normal Operation & Bounded Execution ─────────────────────────────


class TestVLMInvocationBoundaries:
    def test_clean_ocr_does_not_invoke_vlm(self) -> None:
        """When OCR extraction is complete and line math reconciles cleanly,
        VLM fallback must NOT be called."""
        spans = [
            _make_span("sp-h", "HEALTH INSURANCE CLAIM FORM", 0.05, 0.04, 0.50, 0.06),
            _make_span("sp-npi", "NPI: 1234567890", 0.05, 0.10, 0.25, 0.12),
            _make_span("sp-cpt", "99213", 0.05, 0.35, 0.15, 0.37),
            _make_span("sp-amt", "$150.00", 0.70, 0.35, 0.85, 0.37),
            _make_span("sp-lbl", "TOTAL CHARGE:", 0.50, 0.75, 0.65, 0.77),
            _make_span("sp-tot", "$150.00", 0.70, 0.75, 0.85, 0.77),
        ]
        doc = _make_doc(spans)

        mock_vision = MagicMock(spec=ResilientVisionClient)
        mock_vision.is_available.return_value = True

        agent = ClaimsAgent(vision_client=mock_vision, enable_model_fallback=True)
        out = agent.execute("CASE-CLEAN", [doc])

        assert out.verdict == AgentVerdict.PASS
        assert out.line_items_sum_valid is True
        assert mock_vision.read_fields.call_count == 0
        assert mock_vision.cross_check_table.call_count == 0

    def test_resolved_deterministic_geometry_retry_does_not_invoke_vlm(self) -> None:
        """When Pass 1 has geometry jitter (e.g. 0.015 y-delta) but Pass 2
        bounded geometry retry (y_tolerance=0.024) cleanly recovers the row,
        VLM must NOT be called."""
        spans = [
            _make_span("sp-h", "HEALTH INSURANCE CLAIM FORM", 0.05, 0.04, 0.50, 0.06),
            _make_span("sp-npi", "NPI: 1234567890", 0.05, 0.10, 0.25, 0.12),
            # Line 1: aligned
            _make_span("sp-cpt1", "99213", 0.05, 0.350, 0.15, 0.360),
            _make_span("sp-amt1", "$150.00", 0.70, 0.350, 0.85, 0.360),
            # Line 2: jittered by 0.015 (delta > 0.012, recovered by 0.024)
            _make_span("sp-cpt2", "93000", 0.05, 0.435, 0.15, 0.445),
            _make_span("sp-amt2", "$50.00", 0.70, 0.420, 0.85, 0.430),
            # Total: $200.00
            _make_span("sp-lbl", "TOTAL CHARGE:", 0.50, 0.75, 0.65, 0.77),
            _make_span("sp-tot", "$200.00", 0.70, 0.75, 0.85, 0.77),
        ]
        doc = _make_doc(spans)

        mock_vision = MagicMock(spec=ResilientVisionClient)
        mock_vision.is_available.return_value = True

        agent = ClaimsAgent(vision_client=mock_vision, enable_model_fallback=True)
        out = agent.execute("CASE-PASS2-RECOVERED", [doc])

        assert out.verdict == AgentVerdict.PASS
        assert len(out.service_lines) == 2
        assert mock_vision.read_fields.call_count == 0
        assert mock_vision.cross_check_table.call_count == 0

    def test_disabled_or_unauthorized_vlm_makes_zero_external_calls(self) -> None:
        """When ENABLE_MODEL_FALLBACK is False or unauthorized, ClaimsAgent makes
        zero external calls even when an unresolved arithmetic failure occurs."""
        spans = [
            _make_span("sp-h", "HEALTH INSURANCE CLAIM FORM", 0.05, 0.04, 0.50, 0.06),
            _make_span("sp-npi", "NPI: 1234567890", 0.05, 0.10, 0.25, 0.12),
            _make_span("sp-cpt1", "99213", 0.05, 0.35, 0.15, 0.37),
            _make_span("sp-amt1", "$150.00", 0.70, 0.35, 0.85, 0.37),
            _make_span("sp-lbl", "TOTAL CHARGE:", 0.50, 0.75, 0.65, 0.77),
            _make_span("sp-tot", "$500.00", 0.70, 0.75, 0.85, 0.77),
        ]
        doc = _make_doc(spans)

        with patch("requests.post") as mock_post:
            agent = ClaimsAgent(vision_client=None, enable_model_fallback=False)
            out = agent.execute("CASE-UNAUTHORIZED", [doc])

            assert out.verdict == AgentVerdict.CONFLICT
            assert mock_post.call_count == 0


# ── Stage C & D: Real Image Handling & Visual-Evidence Contract ───────────────


class TestVisualEvidenceContractAndImageHandling:
    def test_vlm_receives_non_empty_image_bytes(self) -> None:
        """Proves that VLM cross-check passes real, non-empty image bytes (or targeted crop)
        rather than empty bytes or text-only prompts."""
        img_bytes = _create_synthetic_claim_image()
        storage = InMemoryStorageBackend()
        storage.put_bytes("cases/CASE-IMG/pages/claim_doc_p1.png", img_bytes, "image/png")

        spans = [
            _make_span("sp-h", "HEALTH INSURANCE CLAIM FORM", 0.05, 0.04, 0.50, 0.06),
            _make_span("sp-npi", "NPI: 1234567890", 0.05, 0.10, 0.25, 0.12),
            _make_span("sp-cpt1", "70553", 0.05, 0.35, 0.15, 0.37),
            _make_span("sp-amt1", "$2,100.00", 0.70, 0.35, 0.85, 0.37),
            # Row 2 CPT is missing from OCR; declared total is $2,195.00
            _make_span("sp-amt2", "$95.00", 0.70, 0.43, 0.85, 0.45),
            _make_span("sp-lbl", "TOTAL CHARGE:", 0.50, 0.75, 0.65, 0.77),
            _make_span("sp-tot", "$2,195.00", 0.70, 0.75, 0.85, 0.77),
        ]
        doc = _make_doc(spans, doc_id="claim_doc.png")

        mock_vision = MagicMock(spec=ResilientVisionClient)
        mock_vision.is_available.return_value = True

        # Mock returns structured candidate reading
        mock_vision.cross_check_table.return_value = [
            VisualEvidenceCandidate(
                case_id="CASE-IMG",
                document_id="claim_doc.png",
                page_number=1,
                bbox=BBox(x0=0.05, y0=0.42, x1=0.25, y1=0.46),
                field_name="cpt_code",
                proposed_value="93000",
                exact_text="93000",
                model_id="gemini-3.8-flash",
                prompt_version="v1.0-visual-crosscheck",
                status=VisualEvidenceStatus.VLM_PROPOSED,
            )
        ]

        agent = ClaimsAgent(
            vision_client=mock_vision,
            storage_backend=storage,
            enable_model_fallback=True,
        )
        agent.execute("CASE-IMG", [doc], document_images={"claim_doc.png": img_bytes})

        assert mock_vision.cross_check_table.call_count == 1
        call_args = mock_vision.cross_check_table.call_args
        image_passed = call_args[0][0] if call_args[0] else call_args[1].get("image_bytes")
        assert image_passed is not None
        assert len(image_passed) > 0, "SECURITY VIOLATION: VLM called with empty image bytes!"

    def test_visual_evidence_candidate_typed_contract(self) -> None:
        """Validates that VisualEvidenceCandidate strictly supports required metadata:
        provenance, bounding box, model ID, prompt version, and explicit status."""
        cand = VisualEvidenceCandidate(
            case_id="CASE-123",
            document_id="doc-456",
            page_number=1,
            bbox=BBox(x0=0.25, y0=0.65, x1=0.35, y1=0.69),
            field_name="cpt_code",
            proposed_value="93000",
            exact_text="93000",
            model_id="gemini-3.8-flash",
            prompt_version="v1.0-visual-crosscheck",
            status=VisualEvidenceStatus.VLM_PROPOSED,
            verification_method="local_ocr_crop_reread",
        )
        assert cand.status == VisualEvidenceStatus.VLM_PROPOSED
        assert cand.bbox is not None
        assert cand.bbox.x0 == 0.25
        assert cand.model_id == "gemini-3.8-flash"


# ── Stage E: Independent Cross-Check & Corroboration ──────────────────────────


class TestIndependentCrossCheckAndCorroboration:
    def test_vlm_candidate_corroborated_by_local_ocr_receives_span_provenance(self) -> None:
        """When VLM proposes a missing code (e.g. CPT 93000) and the targeted local
        OCR reread on the crop corroborates it, the candidate is confirmed and attached
        with verified SPAN provenance referencing the actual generated span."""
        img_bytes = _create_synthetic_claim_image(
            line1_text="70553 MRI Brain $2,100.00",
            line2_text="93000 Routine ECG $95.00",
            total_text="TOTAL CHARGE: $2,195.00",
        )

        # Full-page OCR missed CPT 93000 (represented by having amount $95.00 but no CPT span)
        spans = [
            _make_span("sp-h", "HEALTH INSURANCE CLAIM FORM", 0.05, 0.04, 0.50, 0.06),
            _make_span("sp-npi", "NPI: 1234567890", 0.05, 0.10, 0.25, 0.12),
            _make_span("sp-cpt1", "70553", 0.05, 0.34, 0.15, 0.36),
            _make_span("sp-desc1", "MRI Brain", 0.20, 0.34, 0.40, 0.36),
            _make_span("sp-amt1", "$2,100.00", 0.70, 0.34, 0.85, 0.36),
            _make_span("sp-desc2", "Routine ECG", 0.20, 0.39, 0.40, 0.41),
            _make_span("sp-amt2", "$95.00", 0.70, 0.39, 0.85, 0.41),
            _make_span("sp-lbl", "TOTAL CHARGE:", 0.50, 0.74, 0.65, 0.76),
            _make_span("sp-tot", "$2,195.00", 0.70, 0.74, 0.85, 0.76),
        ]
        doc = _make_doc(spans, doc_id="claim_corroborated.png")

        mock_vision = MagicMock(spec=ResilientVisionClient)
        mock_vision.is_available.return_value = True
        mock_vision.cross_check_table.return_value = [
            VisualEvidenceCandidate(
                case_id="CASE-CORROBORATE",
                document_id="claim_corroborated.png",
                page_number=1,
                bbox=BBox(x0=0.04, y0=0.38, x1=0.18, y1=0.42),
                field_name="cpt_code",
                proposed_value="93000",
                exact_text="93000",
                model_id="gemini-3.8-flash",
                prompt_version="v1.0-visual-crosscheck",
                status=VisualEvidenceStatus.VLM_PROPOSED,
            )
        ]

        agent = ClaimsAgent(vision_client=mock_vision, enable_model_fallback=True)
        out = agent.execute(
            "CASE-CORROBORATE",
            [doc],
            document_images={"claim_corroborated.png": img_bytes},
        )

        # After successful local OCR reread corroboration:
        assert out.verdict == AgentVerdict.PASS
        assert len(out.service_lines) == 2
        line2 = out.service_lines[1]
        assert line2.hcpcs is not None
        assert line2.hcpcs.value == "93000"
        # Must have verified SPAN provenance referencing the reread span
        assert line2.hcpcs.provenance is not None
        assert line2.hcpcs.provenance.provenance_type == ProvenanceType.SPAN

    def test_uncorroborated_vlm_reading_cannot_become_falsely_span_verified(self) -> None:
        """When VLM proposes a code that local OCR cannot corroborate from the crop,
        the candidate remains UNRESOLVED and CANNOT be marked ProvenanceType.SPAN."""
        # Image that does NOT contain CPT 99284 in the image
        img_bytes = _create_synthetic_claim_image(
            line1_text="99213 Office Visit $150.00",
            line2_text="Uncoded Service $50.00",
            total_text="TOTAL CHARGE: $200.00",
        )

        spans = [
            _make_span("sp-h", "HEALTH INSURANCE CLAIM FORM", 0.05, 0.04, 0.50, 0.06),
            _make_span("sp-npi", "NPI: 1234567890", 0.05, 0.10, 0.25, 0.12),
            _make_span("sp-cpt1", "99213", 0.05, 0.35, 0.15, 0.37),
            _make_span("sp-amt1", "$150.00", 0.70, 0.35, 0.85, 0.37),
            _make_span("sp-amt2", "$50.00", 0.70, 0.42, 0.85, 0.44),
            _make_span("sp-lbl", "TOTAL CHARGE:", 0.50, 0.75, 0.65, 0.77),
            _make_span("sp-tot", "$200.00", 0.70, 0.75, 0.85, 0.77),
        ]
        doc = _make_doc(spans, doc_id="claim_uncorroborated.png")

        mock_vision = MagicMock(spec=ResilientVisionClient)
        mock_vision.is_available.return_value = True
        # Hallucinated / ungrounded VLM proposal
        mock_vision.cross_check_table.return_value = [
            VisualEvidenceCandidate(
                case_id="CASE-UNCORROBORATED",
                document_id="claim_uncorroborated.png",
                page_number=1,
                bbox=BBox(x0=0.05, y0=0.42, x1=0.15, y1=0.44),
                field_name="cpt_code",
                proposed_value="99284",
                exact_text="99284",
                model_id="gemini-3.8-flash",
                prompt_version="v1.0-visual-crosscheck",
                status=VisualEvidenceStatus.VLM_PROPOSED,
            )
        ]

        agent = ClaimsAgent(vision_client=mock_vision, enable_model_fallback=True)
        out = agent.execute(
            "CASE-UNCORROBORATED",
            [doc],
            document_images={"claim_uncorroborated.png": img_bytes},
        )

        # Uncorroborated candidate must NOT auto-approve
        assert out.verdict != AgentVerdict.PASS
        # Must fail closed: Decision Engine must ESCALATE
        ctx = DecisionInputContext(
            case_id="CASE-UNCORROBORATED",
            execution_id="exec-uncorroborated",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=KYCAgentOutput(verdict=AgentVerdict.PASS, confidence=0.95, latency_ms=10),
            claims_output=out,
            policy_output=PolicyCoverageOutput(
                verdict=AgentVerdict.PASS,
                confidence=0.95,
                covered=True,
                coverage_percentage=100.0,
                applicable_service_codes=["99213", "99284"],
                reasoning_summary="Covered.",
                latency_ms=10,
            ),
            fraud_output=FraudAgentOutput(verdict=AgentVerdict.PASS, confidence=1.0, fraud_score=0.05, risk_level=RiskLevel.LOW, latency_ms=10),
        )
        dec = DeterministicDecisionEngine.adjudicate(ctx)
        assert dec.decision != BusinessDecision.APPROVE
        assert dec.decision == BusinessDecision.ESCALATE

    def test_disagreement_between_vlm_and_ocr_preserves_conflict(self) -> None:
        """When VLM reading conflicts with physical OCR spans (e.g. VLM sees $300, OCR sees $150),
        the system preserves CONFLICT and does NOT pick the reading that happens to make math work."""
        img_bytes = _create_synthetic_claim_image()
        spans = [
            _make_span("sp-h", "HEALTH INSURANCE CLAIM FORM", 0.05, 0.04, 0.50, 0.06),
            _make_span("sp-npi", "NPI: 1234567890", 0.05, 0.10, 0.25, 0.12),
            _make_span("sp-cpt1", "99213", 0.05, 0.35, 0.15, 0.37),
            _make_span("sp-amt1", "$150.00", 0.70, 0.35, 0.85, 0.37),
            _make_span("sp-lbl", "TOTAL CHARGE:", 0.50, 0.75, 0.65, 0.77),
            _make_span("sp-tot", "$300.00", 0.70, 0.75, 0.85, 0.77),
        ]
        doc = _make_doc(spans, doc_id="claim_disagree.png")

        mock_vision = MagicMock(spec=ResilientVisionClient)
        mock_vision.is_available.return_value = True
        # VLM proposes $300 to match total, but OCR span is clearly $150
        mock_vision.cross_check_table.return_value = [
            VisualEvidenceCandidate(
                case_id="CASE-DISAGREE",
                document_id="claim_disagree.png",
                page_number=1,
                bbox=BBox(x0=0.70, y0=0.35, x1=0.85, y1=0.37),
                field_name="line_amount",
                proposed_value=300.00,
                exact_text="$300.00",
                model_id="gemini-3.8-flash",
                prompt_version="v1.0-visual-crosscheck",
                status=VisualEvidenceStatus.CONFLICTING,
            )
        ]

        agent = ClaimsAgent(vision_client=mock_vision, enable_model_fallback=True)
        out = agent.execute(
            "CASE-DISAGREE",
            [doc],
            document_images={"claim_disagree.png": img_bytes},
        )

        assert out.verdict == AgentVerdict.CONFLICT
        assert out.line_items_sum_valid is False


# ── Critical Real-Case Regressions: PT_20322 ──────────────────────────────────


class TestPT20322RealWorldRegression:
    def test_pt20322_missing_token_local_geometry_retry_alone_cannot_invent_code(self) -> None:
        """In the real PT_20322 claim form, CPT 93000 is completely missing from OCR text spans.
        Proves that deterministic retry alone (with no external/VLM fallback) cannot invent the code,
        fails closed, and escalates to human review."""
        spans = [
            _make_span("sp-h1", "HEALTH INSURANCE CLAIM FORM", 0.30, 0.05, 0.70, 0.07),
            _make_span("sp-npi", "NPI: 3847561029", 0.05, 0.15, 0.25, 0.17),
            # Line 1: CPT 70553, $2100.00
            _make_span("sp-cpt1", "70553", 0.271, 0.649, 0.301, 0.659),
            _make_span("sp-desc1", "MRI brain with contrast material", 0.355, 0.650, 0.506, 0.662),
            _make_span("sp-amt1", "$2,100.00", 0.571, 0.654, 0.623, 0.665),
            # Line 2: Real PT_20322 defect: CPT 93000 token is COMPLETELY MISSING from OCR spans!
            _make_span("sp-desc2", "Electrocardiogram, routine EcG with", 0.354, 0.669, 0.519, 0.682),
            _make_span("sp-amt2", "$95.00", 0.570, 0.674, 0.606, 0.684),
            # Total charge: $2,195.00
            _make_span("sp-lbl", "TOTAL CHARGE:", 0.50, 0.84, 0.65, 0.86),
            _make_span("sp-tot", "$2,195.00", 0.655, 0.840, 0.732, 0.855),
        ]
        doc = _make_doc(spans, doc_id="claim_PT_20322.png")

        # Deterministic offline agent (no VLM)
        agent = ClaimsAgent(vision_client=None, enable_model_fallback=False)
        out = agent.execute("CASE-PT20322-OFFLINE", [doc])

        # Must not invent CPT 93000:
        cpt_codes = [c.value for c in out.cpt_codes if c.value]
        assert "93000" not in cpt_codes

        # Downstream Decision Engine MUST ESCALATE and NEVER APPROVE:
        ctx = DecisionInputContext(
            case_id="CASE-PT20322-OFFLINE",
            execution_id="exec-pt20322-offline",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=KYCAgentOutput(verdict=AgentVerdict.PASS, confidence=0.95, latency_ms=10),
            claims_output=out,
            policy_output=PolicyCoverageOutput(
                verdict=AgentVerdict.PASS,
                confidence=0.95,
                covered=True,
                coverage_percentage=100.0,
                applicable_service_codes=["70553", "93000"],
                reasoning_summary="Covered.",
                latency_ms=10,
            ),
            fraud_output=FraudAgentOutput(verdict=AgentVerdict.PASS, confidence=1.0, fraud_score=0.05, risk_level=RiskLevel.LOW, latency_ms=10),
        )
        dec = DeterministicDecisionEngine.adjudicate(ctx)
        assert dec.decision != BusinessDecision.APPROVE
        assert dec.decision == BusinessDecision.ESCALATE

    def test_pt20322_vlm_crosscheck_with_real_image_and_local_ocr_corroboration(self) -> None:
        """End-to-End Visual Recovery on real dataset file:
        In dataset/claim_forms/claim_PT_20322.png, CPT 93000 is physically visible on the page
        at y in [0.65, 0.69], x in [0.25, 0.35].
        When VLM proposes CPT 93000 in this bounding box:
        - Targeted local OCR reread on that region reads '93000' with high confidence.
        - Provenance is verified and attached to the recovered service line.
        - The claim reaches complete, verified PASS."""
        real_img_path = DATASET_ROOT / "claim_forms" / "claim_PT_20322.png"
        if not real_img_path.exists():
            pytest.skip(f"Real dataset image not found at {real_img_path}")

        with open(real_img_path, "rb") as f:
            real_img_bytes = f.read()

        # Spans matching actual full-page OCR of claim_PT_20322.png (where CPT 93000 is missing):
        spans = [
            _make_span("sp-h1", "HEALTH INSURANCE CLAIM FORM", 0.30, 0.05, 0.70, 0.07),
            _make_span("sp-npi", "NPI: 3847561029", 0.05, 0.15, 0.25, 0.17),
            # Line 1: CPT 70553, $2100.00
            _make_span("sp-cpt1", "70553", 0.271, 0.649, 0.301, 0.659),
            _make_span("sp-desc1", "MRI brain with contrast material", 0.355, 0.650, 0.506, 0.662),
            _make_span("sp-amt1", "$2,100.00", 0.571, 0.654, 0.623, 0.665),
            # Line 2: Missing CPT 93000 token, but description and amount present:
            _make_span("sp-desc2", "Electrocardiogram, routine EcG with", 0.354, 0.669, 0.519, 0.682),
            _make_span("sp-amt2", "$95.00", 0.570, 0.674, 0.606, 0.684),
            # Declared total: $2,195.00
            _make_span("sp-lbl", "TOTAL CHARGE:", 0.50, 0.84, 0.65, 0.86),
            _make_span("sp-tot", "$2,195.00", 0.655, 0.840, 0.732, 0.855),
        ]
        doc = _make_doc(spans, doc_id="claim_PT_20322.png")

        mock_vision = MagicMock(spec=ResilientVisionClient)
        mock_vision.is_available.return_value = True
        # VLM visually spots CPT 93000 in Box 24D of row 2:
        mock_vision.cross_check_table.return_value = [
            VisualEvidenceCandidate(
                case_id="CASE-PT20322-VLM",
                document_id="claim_PT_20322.png",
                page_number=1,
                bbox=BBox(x0=0.25, y0=0.65, x1=0.35, y1=0.69),
                field_name="cpt_code",
                proposed_value="93000",
                exact_text="93000",
                model_id="gemini-3.8-flash",
                prompt_version="v1.0-visual-crosscheck",
                status=VisualEvidenceStatus.VLM_PROPOSED,
            )
        ]

        agent = ClaimsAgent(vision_client=mock_vision, enable_model_fallback=True)
        out = agent.execute(
            "CASE-PT20322-VLM",
            [doc],
            document_images={"claim_PT_20322.png": real_img_bytes},
        )

        # Successful end-to-end recovery:
        assert out.verdict == AgentVerdict.PASS
        assert len(out.service_lines) == 2
        assert out.service_lines[0].hcpcs and out.service_lines[0].hcpcs.value == "70553"
        assert out.service_lines[1].hcpcs and out.service_lines[1].hcpcs.value == "93000"
        assert out.service_lines[1].hcpcs.provenance is not None
        assert out.service_lines[1].hcpcs.provenance.provenance_type == ProvenanceType.SPAN
        assert out.line_items_sum_valid is True
        assert out.calculated_lines_sum == 2195.00
