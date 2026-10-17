import io

import pytest
from PIL import Image, ImageDraw

from medishield.agents.classifier import DocumentClassifierAgent
from medishield.db.enums import AgentVerdict, DocumentType
from medishield.intake.storage import InMemoryStorageBackend
from medishield.ocr.preprocessor import (
    DefaultOCREngine,
    OCRPreprocessorEngine,
    retrieve_ocr_payload,
)

# ── Sample Medical Document Fixtures ─────────────────────────────────────────

SAMPLE_CLAIM_FORM_TEXT = """
HEALTH INSURANCE CLAIM FORM
CMS-1500
1. MEDICARE / MEDICAID / OTHER
2. PATIENT'S NAME: John Doe
3. PATIENT'S BIRTH DATE: 05/14/1982
4. INSURED'S NAME: John Doe
5. INSURED'S ID NUMBER: MEM-2026-9901
Total Charge: $2,450.00
Patient Signature: John Doe Date: 2026-09-12
Physician Signature: Dr. Sarah Jenkins Date: 2026-09-15
"""

SAMPLE_DRIVER_LICENSE_TEXT = """
STATE DRIVER LICENSE
DL NUMBER: D9817264
DOB: 11/20/1988
EXP DATE: 11/20/2028
NAME: JANE MARIE SMITH
SEX: F EYES: BRN HT: 5-06
CLASS: C
RESTRICTIONS: NONE
"""

SAMPLE_DISCHARGE_SUMMARY_TEXT = """
MERCY GENERAL HOSPITAL
CLINICAL DISCHARGE SUMMARY
PATIENT: Robert Johnson
ADMISSION DATE: 2026-08-01
DISCHARGE DATE: 2026-08-08
CHIEF COMPLAINT: Severe chest pain, shortness of breath
PRIMARY DIAGNOSIS: Acute Myocardial Infarction, Hypertension, Diabetes Mellitus Type 2
HOSPITAL COURSE: Patient admitted via Emergency Department to ICU. Underwent cardiac catheterization.
ATTENDING PHYSICIAN: Dr. Marcus Vance, MD
CONDITION ON DISCHARGE: Stable, ambulatory.
DISCHARGE INSTRUCTIONS: Low sodium diet, follow up with cardiology in 2 weeks.
Hospital Seal / Official Stamp Attached
"""

SAMPLE_PRESCRIPTION_TEXT = """
METROPOLITAN HEALTH CLINIC
Rx: Amoxicillin 500mg
Sig: Take 1 capsule orally three times daily for 10 days
Dispense: 30 capsules
Refills: 0
Dr. Emily Adams, MD
DEA #: BA9182736
Pharmacy: Metro Care Pharmacy
Physician Signature: Dr. E. Adams
"""

SAMPLE_POLICY_AMENDMENT_TEXT = """
GLOBAL SHIELD LIFE & HEALTH
POLICY AMENDMENT ENDORSEMENT
Amendment Schedule: AMEND-2026-01
Policy Rider to Policy #: POL-481920
Coverage Modification: Inpatient surgical benefit limit increased to $150,000.
Endorsement Effective Date: 2026-01-01
Underwriting Approval: Corporate Underwriting Committee
Terms and Conditions Amendment: Section 4.2 Rider Benefit attached.
"""

SAMPLE_MINIMAL_SEARCHABLE_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
    b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n"
    b"4 0 obj << /Length 65 >> stream\n"
    b"BT\n"
    b"/F1 12 Tf\n"
    b"(HEALTH INSURANCE CLAIM FORM) Tj\n"
    b"(MEMBER ID: MEM-12345) Tj\n"
    b"ET\n"
    b"endstream\n"
    b"endobj\n"
    b"xref\n0 5\n0000000000 65535 f \n"
    b"trailer << /Size 5 /Root 1 0 R >>\n"
    b"startxref\n250\n%%EOF"
)


# ── OCR Preprocessor Tests ───────────────────────────────────────────────────

class TestOCRPreprocessorEngine:
    @pytest.fixture
    def storage(self) -> InMemoryStorageBackend:
        return InMemoryStorageBackend()

    @pytest.fixture
    def preprocessor(self) -> OCRPreprocessorEngine:
        return OCRPreprocessorEngine()

    def test_process_searchable_pdf_extracts_spans_and_persists(
        self,
        preprocessor: OCRPreprocessorEngine,
        storage: InMemoryStorageBackend,
    ) -> None:
        case_id = "CASE-20261003-0001"
        document_id = "DOC-001"

        output = preprocessor.process_document(
            content=SAMPLE_MINIMAL_SEARCHABLE_PDF,
            document_id=document_id,
            case_id=case_id,
            mime_type="application/pdf",
            storage_backend=storage,
        )

        assert output.document_id == document_id
        assert output.verdict == AgentVerdict.PASS
        assert output.is_searchable_pdf is True
        assert output.image_quality_score >= 0.40
        assert "ocr/CASE-20261003-0001/DOC-001.json" in output.payload_storage_uri

        # Verify payload persisted and retrievable
        payload = retrieve_ocr_payload(case_id, document_id, storage)
        assert payload.document_id == document_id
        assert payload.case_id == case_id
        assert payload.total_pages >= 1
        assert len(payload.pages[0].spans) > 0

        # Check span formatting and normalized bounding box constraints
        for span in payload.pages[0].spans:
            assert span.span_id.startswith("span-p1-")
            assert 0.0 <= span.bbox.x_min <= span.bbox.x_max <= 1.0
            assert 0.0 <= span.bbox.y_min <= span.bbox.y_max <= 1.0

    def test_process_plain_text_document(
        self,
        preprocessor: OCRPreprocessorEngine,
        storage: InMemoryStorageBackend,
    ) -> None:
        case_id = "CASE-20261003-0002"
        document_id = "DOC-002"
        content = SAMPLE_CLAIM_FORM_TEXT.encode("utf-8")

        output = preprocessor.process_document(
            content=content,
            document_id=document_id,
            case_id=case_id,
            mime_type="text/plain",
            storage_backend=storage,
        )

        assert output.verdict == AgentVerdict.PASS
        assert output.image_quality_score >= 0.80

        payload = retrieve_ocr_payload(case_id, document_id, storage)
        raw_text = payload.pages[0].raw_text
        assert "CMS-1500" in raw_text
        assert "INSURED'S ID NUMBER" in raw_text

        # Verify colon-separated value span exists
        val_spans = [s for s in payload.pages[0].spans if s.text == "MEM-2026-9901"]
        assert len(val_spans) >= 1
        assert val_spans[0].bbox.x_min >= 0.40

    def test_empty_content_returns_unavailable(
        self,
        preprocessor: OCRPreprocessorEngine,
        storage: InMemoryStorageBackend,
    ) -> None:
        output = preprocessor.process_document(
            content=b"",
            document_id="DOC-EMPTY",
            case_id="CASE-EMPTY",
            mime_type="application/pdf",
            storage_backend=storage,
        )

        assert output.verdict == AgentVerdict.UNAVAILABLE
        assert output.image_quality_score == 0.0
        assert output.total_pages == 0

    def test_blank_page_returns_indeterminate(
        self,
        preprocessor: OCRPreprocessorEngine,
        storage: InMemoryStorageBackend,
    ) -> None:
        output = preprocessor.process_document(
            content=b"    \n   \n   ",
            document_id="DOC-BLANK",
            case_id="CASE-BLANK",
            mime_type="text/plain",
            storage_backend=storage,
        )

        assert output.verdict == AgentVerdict.INDETERMINATE
        assert output.image_quality_score < 0.40

    def test_process_raster_png_with_text_extracts_spans(
        self,
        preprocessor: OCRPreprocessorEngine,
        storage: InMemoryStorageBackend,
    ) -> None:
        case_id = "CASE-20261005-RASTER-01"
        document_id = "DOC-RASTER-01"

        img = Image.new("RGB", (600, 200), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.text((20, 20), "HEALTH INSURANCE CLAIM FORM CMS-1500", fill=(0, 0, 0))
        draw.text((20, 70), "MEMBER ID: MEM-2026-9901", fill=(0, 0, 0))
        draw.text((20, 120), "TOTAL CHARGE: $2,450.00", fill=(0, 0, 0))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        content = buf.getvalue()

        output = preprocessor.process_document(
            content=content,
            document_id=document_id,
            case_id=case_id,
            mime_type="image/png",
            storage_backend=storage,
        )

        assert output.document_id == document_id
        assert output.verdict == AgentVerdict.PASS
        assert output.image_quality_score >= 0.50
        assert output.total_pages == 1

        payload = retrieve_ocr_payload(case_id, document_id, storage)
        assert len(payload.pages[0].spans) > 0
        raw_text = payload.pages[0].raw_text
        assert "HEALTH INSURANCE CLAIM FORM" in raw_text or "MEM-2026-9901" in raw_text

        # Verify normalized bounding box constraints
        for span in payload.pages[0].spans:
            assert span.span_id.startswith("span-p1-")
            assert 0.0 <= span.bbox.x_min <= span.bbox.x_max <= 1.0
            assert 0.0 <= span.bbox.y_min <= span.bbox.y_max <= 1.0

    def test_process_raster_png_with_defaulted_pdf_mime_extracts_spans(
        self,
        preprocessor: OCRPreprocessorEngine,
        storage: InMemoryStorageBackend,
    ) -> None:
        """Proves that a raster PNG image (such as an ID card or scan) is not misparsed as a PDF
        even if the caller or workflow defaults mime_type to 'application/pdf'."""
        case_id = "CASE-RASTER-DEFAULT-MIME"
        document_id = "DOC-ID-CARD-01"

        img = Image.new("RGB", (600, 200), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.text((20, 20), "STATE DRIVER LICENSE", fill=(0, 0, 0))
        draw.text((20, 70), "NAME: JANE DOE", fill=(0, 0, 0))
        draw.text((20, 120), "DOB: 1990-05-15", fill=(0, 0, 0))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        content = buf.getvalue()

        # Passed with misleading/defaulted mime_type='application/pdf'
        output = preprocessor.process_document(
            content=content,
            document_id=document_id,
            case_id=case_id,
            mime_type="application/pdf",
            storage_backend=storage,
        )

        assert output.document_id == document_id
        assert output.verdict == AgentVerdict.PASS
        assert output.image_quality_score >= 0.50

        payload = retrieve_ocr_payload(case_id, document_id, storage)
        assert len(payload.pages[0].spans) > 0
        raw_text = payload.pages[0].raw_text
        assert "DRIVER LICENSE" in raw_text or "JANE DOE" in raw_text

    def test_process_blank_raster_png_returns_indeterminate(
        self,
        preprocessor: OCRPreprocessorEngine,
        storage: InMemoryStorageBackend,
    ) -> None:
        case_id = "CASE-20261005-RASTER-02"
        document_id = "DOC-RASTER-02"

        img = Image.new("RGB", (200, 200), color=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        content = buf.getvalue()

        output = preprocessor.process_document(
            content=content,
            document_id=document_id,
            case_id=case_id,
            mime_type="image/png",
            storage_backend=storage,
        )

        assert output.verdict == AgentVerdict.INDETERMINATE
        assert output.image_quality_score < 0.40
        assert output.payload_storage_uri == ""



# ── Document Classifier Agent Tests ──────────────────────────────────────────

class TestDocumentClassifierAgent:
    @pytest.fixture
    def classifier(self) -> DocumentClassifierAgent:
        return DocumentClassifierAgent()

    def test_classify_claim_form(self, classifier: DocumentClassifierAgent) -> None:
        output = classifier.classify(
            document_id="DOC-CLAIM",
            text=SAMPLE_CLAIM_FORM_TEXT,
        )

        assert output.doc_type == DocumentType.CLAIM_FORM
        assert output.confidence >= 0.70
        assert output.verdict == AgentVerdict.PASS
        assert "has_signature_block" in output.visual_characteristics
        assert output.visual_characteristics["has_signature_block"] is True

    def test_classify_driver_license_id(self, classifier: DocumentClassifierAgent) -> None:
        output = classifier.classify(
            document_id="DOC-ID",
            text=SAMPLE_DRIVER_LICENSE_TEXT,
        )

        assert output.doc_type == DocumentType.ID_DOCUMENT
        assert output.confidence >= 0.70
        assert output.verdict == AgentVerdict.PASS
        assert "GOVERNMENT_ID" in output.routing_tags

    def test_classify_discharge_summary(self, classifier: DocumentClassifierAgent) -> None:
        output = classifier.classify(
            document_id="DOC-DISCHARGE",
            text=SAMPLE_DISCHARGE_SUMMARY_TEXT,
        )

        assert output.doc_type == DocumentType.DISCHARGE_SUMMARY
        assert output.confidence >= 0.70
        assert output.verdict == AgentVerdict.PASS
        assert "INPATIENT_FACILITY" in output.routing_tags
        assert "EMERGENCY_ADMISSION" in output.routing_tags
        assert "CHRONIC_CARE" in output.routing_tags
        assert output.visual_characteristics["has_stamp_or_seal"] is True

    def test_classify_prescription(self, classifier: DocumentClassifierAgent) -> None:
        output = classifier.classify(
            document_id="DOC-RX",
            text=SAMPLE_PRESCRIPTION_TEXT,
        )

        assert output.doc_type == DocumentType.PRESCRIPTION
        assert output.confidence >= 0.70
        assert output.verdict == AgentVerdict.PASS
        assert "PHARMACY_RX" in output.routing_tags

    def test_classify_policy_amendment(self, classifier: DocumentClassifierAgent) -> None:
        output = classifier.classify(
            document_id="DOC-AMEND",
            text=SAMPLE_POLICY_AMENDMENT_TEXT,
        )

        assert output.doc_type == DocumentType.POLICY_AMENDMENT
        assert output.confidence >= 0.70
        assert output.verdict == AgentVerdict.PASS

    def test_classify_unknown_gibberish_returns_indeterminate(
        self,
        classifier: DocumentClassifierAgent,
    ) -> None:
        output = classifier.classify(
            document_id="DOC-UNK",
            text="Lorem ipsum dolor sit amet, consectetur adipiscing elit. Random filler text without medical tokens.",
        )

        assert output.doc_type == DocumentType.UNKNOWN
        assert output.confidence < 0.70
        assert output.verdict == AgentVerdict.INDETERMINATE

    def test_classify_from_ocr_payload_object(
        self,
        classifier: DocumentClassifierAgent,
    ) -> None:
        engine = DefaultOCREngine()
        pages = engine.extract_pages(SAMPLE_CLAIM_FORM_TEXT.encode("utf-8"), "text/plain")
        from medishield.domain.contracts import OCRDocumentPayload

        payload = OCRDocumentPayload(
            document_id="DOC-OBJ",
            total_pages=len(pages),
            pages=pages,
        )

        output = classifier.classify(
            document_id="DOC-OBJ",
            ocr_payload=payload,
        )

        assert output.doc_type == DocumentType.CLAIM_FORM
        assert output.confidence >= 0.70
        assert output.verdict == AgentVerdict.PASS

    def test_classify_from_filename_only(
        self,
        classifier: DocumentClassifierAgent,
    ) -> None:
        """Known dataset names hint a type, but content is required for PASS."""
        test_cases = [
            ("claim_PT_15075.png", DocumentType.CLAIM_FORM),
            ("id_PT_15075.png", DocumentType.ID_DOCUMENT),
            ("discharge_PT_15075.png", DocumentType.DISCHARGE_SUMMARY),
            ("rx_PT_15075.png", DocumentType.PRESCRIPTION),
            ("amend_PT_15075.png", DocumentType.POLICY_AMENDMENT),
        ]
        for filename, expected_type in test_cases:
            output = classifier.classify(
                document_id="DOC-FN",
                file_name=filename,
            )
            assert output.doc_type == expected_type
            assert output.confidence < 0.70
            assert output.verdict == AgentVerdict.INDETERMINATE

    def test_dataset_filename_resolves_category_over_incidental_terms(
        self,
        classifier: DocumentClassifierAgent,
    ) -> None:
        output = classifier.classify(
            document_id="DOC-DATASET-DISCHARGE",
            file_name="discharge_PT_19116.png",
            text="DISCHARGE SUMMARY. Discharge medications include a prescription and dosage.",
        )

        assert output.doc_type == DocumentType.DISCHARGE_SUMMARY
        assert output.confidence >= 0.70
        assert output.verdict == AgentVerdict.PASS

    def test_unrecognized_filename_uses_content_classifier(
        self,
        classifier: DocumentClassifierAgent,
    ) -> None:
        output = classifier.classify(
            document_id="DOC-USER-UPLOAD",
            file_name="scan_2026_10_12.png",
            text=SAMPLE_DRIVER_LICENSE_TEXT,
        )

        assert output.doc_type == DocumentType.ID_DOCUMENT
        assert output.verdict == AgentVerdict.PASS

    def test_prefixed_dataset_filename_classification(
        self,
        classifier: DocumentClassifierAgent,
    ) -> None:
        """Prefixed dataset filenames (e.g. DOC-0A2917-03_id_PT_15075.png) must classify correctly."""
        test_cases = [
            ("DOC-0A2917-01_claim_PT_15075.png", DocumentType.CLAIM_FORM),
            ("DOC-0A2917-03_id_PT_15075.png", DocumentType.ID_DOCUMENT),
            ("DOC-0A2917-02_discharge_PT_15075.png", DocumentType.DISCHARGE_SUMMARY),
            ("DOC-0A2917-05_rx_PT_15075.png", DocumentType.PRESCRIPTION),
            ("DOC-0A2917-04_amend_PT_15075.png", DocumentType.POLICY_AMENDMENT),
        ]
        for filename, expected_type in test_cases:
            output = classifier.classify(
                document_id="DOC-PREF",
                file_name=filename,
                text="Document text content with generic words.",
            )
            assert output.doc_type == expected_type
            assert output.verdict == AgentVerdict.PASS

    def test_non_driver_id_card_text_classification(
        self,
        classifier: DocumentClassifierAgent,
    ) -> None:
        """Non-driver identification card text without filename must classify as ID_DOCUMENT, not CLAIM_FORM."""
        id_text = (
            "STATE OF ILLINOIS\n"
            "NON-DRIVER\n"
            "IDENTIFICATION CARD\n"
            "ID CARD\n"
            "ID NUMBER: I1423-2496\n"
            "NAME: PATRICIA MILLER H.\n"
            "DOB: 01/26/1997\n"
            "EXP: 11/14/2029\n"
            "PHOTO ID"
        )
        output = classifier.classify(
            document_id="DOC-ID-CARD",
            file_name="upload_file.pdf",
            text=id_text,
        )
        assert output.doc_type == DocumentType.ID_DOCUMENT
        assert output.confidence >= 0.70
        assert output.verdict == AgentVerdict.PASS

    def test_classifier_node_reclassifies_unverified_claim_form(self) -> None:
        """classifier_node must reclassify documents that were defaulted to CLAIM_FORM without confidence."""
        from medishield.intake.storage import InMemoryStorageBackend
        from medishield.workflows.nodes import classifier_node

        storage = InMemoryStorageBackend()

        state = {
            "case_id": "CASE-TEST-RECLASSIFY",
            "documents": [
                {
                    "document_id": "DOC-ID-01",
                    "file_name": "id_PT_62350.png",
                    "doc_type": "CLAIM_FORM",  # Erroneous default
                    "classification_confidence": None,  # Unverified
                },
                {
                    "document_id": "DOC-RX-01",
                    "file_name": "rx_PT_62350.png",
                    "doc_type": "CLAIM_FORM",  # Erroneous default
                    "classification_confidence": 0.0,  # Zero confidence
                },
            ],
            "workflow_state": "SPLIT",
        }
        res_state = classifier_node(state, storage)
        docs = res_state["documents"]
        assert docs[0]["doc_type"] == DocumentType.ID_DOCUMENT.value
        assert docs[0]["classification_confidence"] > 0.0
        assert docs[1]["doc_type"] == DocumentType.PRESCRIPTION.value
        assert docs[1]["classification_confidence"] > 0.0



