"""Claims Extraction Agent: Coding extraction, line item parsing, and arithmetic validation (Spec 05 § 5)."""

import io
import logging
import re
import time
from collections import Counter
from pathlib import Path

from PIL import Image

from medishield.agents.classifier import DocumentClassifierAgent
from medishield.core.config import settings
from medishield.core.llm import (
    GroqLLMClient,
    GroqLLMError,
    default_groq_client,
    wrap_untrusted_content,
)
from medishield.db.catalog import CodeCatalogRepository
from medishield.db.enums import AgentVerdict, CodingSystem, DocumentType, IndeterminateReason
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    OCRBoundingBox,
    OCRDocumentPayload,
    OCRTextSpan,
    ServiceLine,
    VisualEvidenceCandidate,
    VisualEvidenceStatus,
)
from medishield.domain.faithfulness import parse_medical_date, verify_extracted_span
from medishield.domain.provenance import (
    ClinicalAssertionStatus,
    EvidenceSource,
    EvidenceVerificationStatus,
    FieldProvenance,
    ProvenanceType,
    ProvenanceValue,
)
from medishield.intake.storage import StorageBackend
from medishield.ocr.multilingual import extract_multilingual_clinical_indications
from medishield.ocr.ocr_reconcile import (
    RE_ADDRESS_INDICATOR,
    RE_HEADER_METADATA_INDICATOR,
    ArithmeticCheckStatus,
    ResilientVisionClient,
    detect_claim_currency,
    evaluate_claim_totals,
    extract_cpt_codes_from_table,
    extract_money_value_geometrically,
    extract_service_lines_from_table,
    find_unassigned_charge_anchors,
    is_plausible_revenue_code,
)
from medishield.ocr.preprocessor import get_rapid_ocr_engine

logger = logging.getLogger(__name__)

RE_HEADER_FIELD_PREFIX = re.compile(
    r"^(?:\d{1,2}[a-zA-Z]?\s*[-:.]?\s*)?"
    r"(?:primary\s+|principal\s+|admitting\s+)?"
    r"(?:procedure|cpt|hcpcs|code|diagnosis|dx|icd|npi|dob|date|service\s*date|date\s*of\s*service|member|patient|provider|physician|charge|total|claim|box)\s*[:#]",
    re.IGNORECASE,
)


class ClaimsAgent:
    """
    Authoritative Claims Extraction Agent (Spec 05 § 5, Spec 06 § 2, Spec 10 § 2).
    Extracts financial headers, ICD-10 diagnostic codes, CPT procedural codes,
    and granular service lines with deterministic arithmetic and schema verification.
    Features hybrid Groq LLM assistance for unstructured clinical narratives.
    """

    def __init__(
        self,
        llm_client: GroqLLMClient | None = None,
        vision_client: ResilientVisionClient | None = None,
        storage_backend: StorageBackend | None = None,
        code_catalog: CodeCatalogRepository | None = None,
        enable_model_fallback: bool | None = None,
    ) -> None:
        if enable_model_fallback is not None:
            self.enable_model_fallback = enable_model_fallback
        elif llm_client is not None or vision_client is not None:
            self.enable_model_fallback = True
        else:
            self.enable_model_fallback = getattr(settings, "ENABLE_MODEL_FALLBACK", False)

        self.llm_client = llm_client if llm_client is not None else (default_groq_client if self.enable_model_fallback else None)
        self.vision_client = vision_client if vision_client is not None else (ResilientVisionClient() if self.enable_model_fallback else None)
        self.storage_backend = storage_backend
        if code_catalog is not None:
            self.code_catalog = code_catalog
        else:
            try:
                from medishield.db.catalog import SqlCodeCatalogRepository
                from medishield.db.session import sync_engine

                self.code_catalog = SqlCodeCatalogRepository(sync_engine)
            except Exception:  # noqa: BLE001
                from medishield.db.catalog import InMemoryCodeCatalogRepository

                self.code_catalog = InMemoryCodeCatalogRepository()

    def execute(
        self,
        case_id: str,
        document_payloads: list[OCRDocumentPayload],
        storage_backend: StorageBackend | None = None,
        document_images: dict[str, bytes] | None = None,
    ) -> ClaimsAgentOutput:
        """
        Executes claims extraction, code parsing, and line-item arithmetic verification.
        Returns typed ClaimsAgentOutput conforming strictly to Spec 05 § 5.
        """
        start_time = time.perf_counter()
        validation_errors: list[str] = []
        visual_candidates: list[VisualEvidenceCandidate] = []
        model_calls_made = 0

        # 1. Partition document payloads and spans by document type (Spec 03 § 4, Spec 05 § 5)
        classifier = DocumentClassifierAgent()

        claim_billing_spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]] = []
        clinical_spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]] = []
        all_spans_with_doc: list[tuple[OCRDocumentPayload, int, OCRTextSpan]] = []

        for doc in document_payloads:
            doc_spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]] = []
            for page in doc.pages:
                for span in page.spans:
                    tup = (doc, page.page_number, span)
                    doc_spans.append(tup)
                    all_spans_with_doc.append(tup)

            doc_id_lower = doc.document_id.lower()
            if (
                doc_id_lower.startswith("claim_")
                or "ub-04" in doc_id_lower
                or "cms-1500" in doc_id_lower
                or "bill" in doc_id_lower
            ):
                doc_type = DocumentType.CLAIM_FORM
            elif (
                doc_id_lower.startswith("id_")
                or "passport" in doc_id_lower
                or "license" in doc_id_lower
                or "identity" in doc_id_lower
            ):
                doc_type = DocumentType.ID_DOCUMENT
            elif (
                doc_id_lower.startswith("discharge_")
                or "inpatient" in doc_id_lower
            ):
                doc_type = DocumentType.DISCHARGE_SUMMARY
            elif (
                doc_id_lower.startswith("rx_")
                or "prescription" in doc_id_lower
            ):
                doc_type = DocumentType.PRESCRIPTION
            elif (
                doc_id_lower.startswith("amend_")
                or "rider" in doc_id_lower
                or "endorsement" in doc_id_lower
            ):
                doc_type = DocumentType.POLICY_AMENDMENT
            else:
                classified = classifier.classify(
                    document_id=doc.document_id,
                    ocr_payload=doc,
                    file_name=doc.document_id,
                )
                doc_type = classified.doc_type

            if doc_type == DocumentType.CLAIM_FORM:
                claim_billing_spans.extend(doc_spans)
            elif doc_type == DocumentType.DISCHARGE_SUMMARY:
                clinical_spans.extend(doc_spans)
            elif doc_type in (DocumentType.ID_DOCUMENT, DocumentType.PRESCRIPTION, DocumentType.POLICY_AMENDMENT):
                # Never route to claim billing or clinical coding
                pass
            else:
                # UNKNOWN: if not explicitly ID/Rx/amend/discharge, allow as claim billing candidate
                claim_billing_spans.extend(doc_spans)

        # Fallback for synthetic single-document tests without standard filenames or headers
        if not claim_billing_spans:
            claim_billing_spans = [
                s for s in all_spans_with_doc
                if not s[0].document_id.lower().startswith(("id_", "rx_", "amend_", "discharge_"))
            ] or all_spans_with_doc

        # 2. Extract header financial and provider fields strictly from claim billing documents
        claim_currency, claim_currency_symbol = detect_claim_currency(claim_billing_spans or all_spans_with_doc)
        claim_amount_pv, raw_claim_amount, has_total_label = self._extract_claim_amount(claim_billing_spans)

        # Service date: search claim form first; fallback to discharge summary if missing
        service_date_pv = self._extract_service_date(claim_billing_spans)
        if not service_date_pv and clinical_spans:
            service_date_pv = self._extract_service_date(clinical_spans)

        # Provider info: search claim form first; fallback to clinical if missing
        provider_name_pv = self._extract_provider_name(claim_billing_spans)
        if not provider_name_pv and clinical_spans:
            provider_name_pv = self._extract_provider_name(clinical_spans)

        provider_npi_pv, raw_npi = self._extract_provider_npi(claim_billing_spans)
        if not provider_npi_pv and clinical_spans:
            provider_npi_pv, raw_npi = self._extract_provider_npi(clinical_spans)

        # 3. Extract diagnostic and procedural coding
        # ICD-10 diagnostic codes come from clinical discharge summaries and claim forms
        icd10_codes = self._extract_icd10_codes(clinical_spans + claim_billing_spans)
        unverified_icd10_codes = [
            str(item.value)
            for item in icd10_codes
            if self.code_catalog is None
            or not self.code_catalog.is_valid_code(str(item.value), CodingSystem.ICD10_CM)
            or item.provenance is None
            or item.provenance.evidence_source != EvidenceSource.OCR
            or item.provenance.verification_status != EvidenceVerificationStatus.VERIFIED
        ]
        if unverified_icd10_codes:
            validation_errors.append(
                "UNVERIFIED_ICD10_CODES: Diagnosis code(s) "
                f"{', '.join(sorted(set(unverified_icd10_codes)))} lack verified OCR provenance or active catalog entries."
            )

        # CPT procedural codes strictly from claim billing table
        cpt_codes = self._extract_cpt_codes(claim_billing_spans)

        # 4. Extract granular billing service line items strictly from claim billing table (Pass 1)
        service_lines = self._extract_service_lines(
            claim_billing_spans,
            y_tolerance=0.012,
            currency=claim_currency,
            currency_symbol=claim_currency_symbol,
        )

        # 4b. Hybrid LLM Extraction fallback for unstructured text (Rule 11 & Spec 01 § 1.1)
        # Bounded to at most 1 external model call across the entire execution.
        if (
            (not cpt_codes and not service_lines)
            and self.enable_model_fallback
            and self.llm_client
            and self.llm_client.is_available()
            and model_calls_made < 1
        ):
            try:
                model_calls_made += 1
                llm_icd, llm_cpt, llm_lines = self._extract_with_groq_llm(claim_billing_spans, document_payloads)
                if not icd10_codes and llm_icd:
                    icd10_codes = llm_icd
                if not cpt_codes and llm_cpt:
                    cpt_codes = llm_cpt
                if not service_lines and llm_lines:
                    grounded_lines = []
                    for candidate in llm_lines:
                        grounded = self._ground_candidate_line(candidate, claim_billing_spans)
                        if grounded:
                            grounded_lines.append(grounded)
                    if grounded_lines:
                        service_lines = grounded_lines
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Hybrid Groq LLM extraction failed: {exc}")

        # If service date was not found in headers or was invalid, derive from line item dates
        if (not service_date_pv or getattr(service_date_pv, "value", None) in ("UNKNOWN", "None", "", None)) and service_lines:
            for line in service_lines:
                sl_date_val = getattr(getattr(line, "service_date", None), "value", None)
                if sl_date_val and sl_date_val not in ("UNKNOWN", "None", ""):
                    p_dt = parse_medical_date(sl_date_val)
                    if p_dt and p_dt.year >= 2020:
                        service_date_pv = line.service_date
                        break

        # 5. Deterministic Arithmetic & Schema Validation (Rule 11)
        schema_valid = True
        calculated_lines_sum = 0.0

        # Validate Provider NPI format (10-digit standard for US claims)
        if raw_npi:
            if not re.match(r"^\d{10}$", raw_npi):
                validation_errors.append("INVALID_PROVIDER_NPI: Provider NPI must be exactly 10 digits.")
                schema_valid = False
        elif claim_currency == "USD":
            validation_errors.append("MISSING_PROVIDER_NPI: Provider NPI could not be extracted.")
            schema_valid = False

        # Validate service line item math
        if service_lines:
            line_sum = 0.0
            for line in service_lines:
                try:
                    qty = float(line.units.value)
                    tot = float(line.amount.value)

                    if tot < 0.0 or qty <= 0.0:
                        validation_errors.append(
                            f"NEGATIVE_OR_ZERO_LINE_CHARGE: Line {line.line_number} contains invalid quantities or charges."
                        )
                        schema_valid = False

                    line_sum += tot
                except (ValueError, TypeError):
                    validation_errors.append(f"NON_NUMERIC_LINE_CHARGE: Line {line.line_number} contains unparseable numbers.")
                    schema_valid = False

            calculated_lines_sum = max(0.0, round(line_sum, 2))

        # Reconcile claim totals and line items sum deterministically (Rule 10-14, ADR-018)
        (
            final_claim_pv,
            final_raw_amount,
            arithmetic_status,
            total_validation_errors,
        ) = evaluate_claim_totals(
            declared_total=raw_claim_amount,
            declared_total_pv=claim_amount_pv,
            service_lines=service_lines,
            has_total_label=has_total_label,
            currency_symbol=claim_currency_symbol,
        )
        validation_errors.extend(total_validation_errors)
        claim_amount_pv = final_claim_pv
        raw_claim_amount = final_raw_amount

        # Completeness Check (Spec 01 § 1.1, Spec 02 P6, Step 2.2):
        # Count billing table CPT occurrences across distinct rows vs service line CPTs
        table_cpt_occurrences = self._extract_table_cpt_row_occurrences(claim_billing_spans)
        table_cpt_counts = Counter(c for c, _, _, _, _ in table_cpt_occurrences)

        service_line_cpt_counts = Counter(
            str(line.hcpcs.value).strip()
            for line in service_lines
            if line.hcpcs and line.hcpcs.value and str(line.hcpcs.value).strip() not in ("", "None", "UNKNOWN", "null")
        )
        missing_table_cpts: list[str] = []
        for code, table_cnt in table_cpt_counts.items():
            line_cnt = service_line_cpt_counts.get(code, 0)
            if line_cnt < table_cnt:
                missing_table_cpts.extend([code] * (table_cnt - line_cnt))

        has_uncoded_charged_lines = any(
            line.amount and line.amount.value is not None and float(line.amount.value) > 0
            and (not line.hcpcs or not line.hcpcs.value or str(line.hcpcs.value).strip() in ("", "None", "UNKNOWN", "null"))
            and (not line.rev_code or not line.rev_code.value or str(line.rev_code.value).strip() in ("", "None", "UNKNOWN", "null"))
            for line in service_lines
        )

        # Stage 5: Bounded Local Recovery and Fail-Closed Fallback (Spec 01 § 1.1, Rules 10-14)
        if arithmetic_status == ArithmeticCheckStatus.FAIL or bool(missing_table_cpts) or has_uncoded_charged_lines:
            # Pass 2: Bounded deterministic geometry retry with slightly wider y_tolerance (0.024 vs 0.012)
            # Recovers rows dropped due to minor scanner/table jitter (e.g. PT_20322 CPT 93000)
            retry_lines = self._extract_service_lines(
                claim_billing_spans,
                y_tolerance=0.024,
                currency=claim_currency,
                currency_symbol=claim_currency_symbol,
            )
            if retry_lines and retry_lines != service_lines:
                (
                    retry_claim_pv,
                    retry_raw_amount,
                    retry_status,
                    _retry_errors,
                ) = evaluate_claim_totals(
                    declared_total=raw_claim_amount,
                    declared_total_pv=claim_amount_pv,
                    service_lines=retry_lines,
                    has_total_label=has_total_label,
                    currency_symbol=claim_currency_symbol,
                )
                retry_service_cpt_counts = Counter(
                    str(line.hcpcs.value).strip()
                    for line in retry_lines
                    if line.hcpcs and line.hcpcs.value and str(line.hcpcs.value).strip() not in ("", "None", "UNKNOWN", "null")
                )
                retry_missing_cpts = [
                    code
                    for code, table_cnt in table_cpt_counts.items()
                    if retry_service_cpt_counts.get(code, 0) < table_cnt
                ]
                if retry_status == ArithmeticCheckStatus.PASS and not retry_missing_cpts:
                    # Successful deterministic recovery: all lines reconciled cleanly with SPAN provenance
                    service_lines = retry_lines
                    calculated_lines_sum = sum(float(l.amount.value) for l in service_lines)
                    arithmetic_status = ArithmeticCheckStatus.PASS
                    validation_errors = [e for e in validation_errors if "ARITHMETIC_DRIFT" not in e and "INCOMPLETE_SERVICE_LINES" not in e]
                    claim_amount_pv = retry_claim_pv
                    raw_claim_amount = retry_raw_amount

                    # Reconcile any recovered CPT procedure codes
                    recovered_cpt_values = {pv.value for pv in cpt_codes if pv.value}
                    for line in service_lines:
                        if line.hcpcs and line.hcpcs.value and line.hcpcs.value not in recovered_cpt_values:
                            cpt_codes.append(line.hcpcs)
                            recovered_cpt_values.add(line.hcpcs.value)

            # Determine if there is an unresolved table extraction problem:
            # - Arithmetic check failed
            # - Missing procedure codes detected in table compared to service lines
            # - Any service line has an amount but lacks both a verified CPT procedure code AND a revenue code
            current_service_cpt_counts = Counter(
                str(line.hcpcs.value).strip()
                for line in service_lines
                if line.hcpcs and line.hcpcs.value and str(line.hcpcs.value).strip() not in ("", "None", "UNKNOWN", "null")
            )
            has_table_missing_cpts = any(
                current_service_cpt_counts.get(code, 0) < table_cnt
                for code, table_cnt in table_cpt_counts.items()
            )
            has_uncoded_charged_lines = any(
                line.amount and line.amount.value is not None and float(line.amount.value) > 0
                and (not line.hcpcs or not line.hcpcs.value or str(line.hcpcs.value).strip() in ("", "None", "UNKNOWN", "null"))
                and (not getattr(line, "rev_code", None) or not line.rev_code.value or str(line.rev_code.value).strip() in ("", "None", "UNKNOWN", "null"))
                for line in service_lines
            )
            # Only trigger expensive external VLM/LLM calls if arithmetic failed or table CPTs are missing (ADR-020 & Rule 12).
            # When arithmetic passes and all table CPTs are matched, deterministic extraction is authoritative.
            unresolved_issue = (
                arithmetic_status != ArithmeticCheckStatus.PASS
                or has_table_missing_cpts
                or has_uncoded_charged_lines
            )

            # Stage C: VLM-Assisted Visual Cross-Check (Spec 05 § 5, Spec 06 § 2)
            # Prioritize visual cross-check when vision client is configured and available
            if (
                unresolved_issue
                and self.enable_model_fallback
                and self.vision_client is not None
                and self.vision_client.is_available()
                and model_calls_made < 1
            ):
                model_calls_made += 1
                claim_doc = next(
                    (d for d in document_payloads if d.document_id.lower().startswith("claim_") or "bill" in d.document_id.lower() or "cms-1500" in d.document_id.lower() or "ub-04" in d.document_id.lower()),
                    document_payloads[0] if document_payloads else None,
                )
                if claim_doc:
                    img_bytes = self._resolve_document_image(
                        case_id=case_id,
                        doc_id=claim_doc.document_id,
                        page_number=1,
                        document_images=document_images,
                        storage=storage_backend,
                    )
                    if img_bytes:
                        try:
                            vlm_candidates = self.vision_client.cross_check_table(
                                image_bytes=img_bytes,
                                page_number=1,
                                case_id=case_id,
                                document_id=claim_doc.document_id,
                            )
                            for cand in vlm_candidates:
                                corroborated_cand, new_span = self._corroborate_visual_candidate(
                                    candidate=cand,
                                    image_bytes=img_bytes,
                                    doc=claim_doc,
                                    claim_billing_spans=claim_billing_spans,
                                )
                                visual_candidates.append(corroborated_cand)

                                if corroborated_cand.status == VisualEvidenceStatus.OCR_CORROBORATED and new_span:
                                    claim_billing_spans.append((claim_doc, new_span.page_number, new_span))
                                    cand_val = str(cand.proposed_value).strip()

                                    if cand.field_name in ("cpt_code", "hcpcs"):
                                        assigned = False
                                        # First try lines without CPT
                                        for line in service_lines:
                                            if not line.hcpcs or not line.hcpcs.value or str(line.hcpcs.value).strip() in ("", "None", "UNKNOWN", "null"):
                                                ok_sp, prov = verify_extracted_span(cand_val, new_span.span_id, new_span.page_number, claim_doc)
                                                if ok_sp and prov:
                                                    line.hcpcs = ProvenanceValue(value=cand_val, confidence=new_span.confidence, provenance=prov)
                                                    assigned = True
                                                    if cand_val not in [pv.value for pv in cpt_codes if pv.value]:
                                                        cpt_codes.append(line.hcpcs)
                                                    break
                                        if not assigned:
                                            ok_sp, prov = verify_extracted_span(cand_val, new_span.span_id, new_span.page_number, claim_doc)
                                            if ok_sp and prov:
                                                pv = ProvenanceValue(value=cand_val, confidence=new_span.confidence, provenance=prov)
                                                cpt_codes.append(pv)

                                elif corroborated_cand.status == VisualEvidenceStatus.CONFLICTING:
                                    validation_errors.append(
                                        f"CONFLICTING_VISUAL_EVIDENCE: Visual evidence for {cand.field_name} conflicts with OCR text."
                                    )
                                elif corroborated_cand.status == VisualEvidenceStatus.UNRESOLVED and cand.field_name in ("cpt_code", "hcpcs", "line_amount"):
                                    validation_errors.append(
                                        f"UNRESOLVED_VISUAL_CANDIDATE: Visual candidate {cand.proposed_value} for {cand.field_name} could not be corroborated by OCR."
                                    )

                            # If any lines or codes were recovered, re-extract or update service lines
                            if any(c.status == VisualEvidenceStatus.OCR_CORROBORATED for c in visual_candidates):
                                recovered_lines = self._extract_service_lines(
                                    claim_billing_spans,
                                    y_tolerance=0.012,
                                    currency=claim_currency,
                                    currency_symbol=claim_currency_symbol,
                                )
                                if len(recovered_lines) >= len(service_lines):
                                    for idx, r_line in enumerate(recovered_lines):
                                        if (
                                            (not r_line.hcpcs or not r_line.hcpcs.value)
                                            and idx < len(service_lines)
                                            and service_lines[idx].hcpcs
                                            and service_lines[idx].hcpcs.value
                                        ):
                                            r_line.hcpcs = service_lines[idx].hcpcs
                                    service_lines = recovered_lines
                                else:
                                    recovered_retry = self._extract_service_lines(
                                        claim_billing_spans,
                                        y_tolerance=0.024,
                                        currency=claim_currency,
                                        currency_symbol=claim_currency_symbol,
                                    )
                                    if len(recovered_retry) >= len(service_lines):
                                        for idx, r_line in enumerate(recovered_retry):
                                            if (
                                                (not r_line.hcpcs or not r_line.hcpcs.value)
                                                and idx < len(service_lines)
                                                and service_lines[idx].hcpcs
                                                and service_lines[idx].hcpcs.value
                                            ):
                                                r_line.hcpcs = service_lines[idx].hcpcs
                                        service_lines = recovered_retry

                                existing_cpt_set = {pv.value for pv in cpt_codes if pv.value}
                                for line in service_lines:
                                    if line.hcpcs and line.hcpcs.value and line.hcpcs.value not in existing_cpt_set:
                                        cpt_codes.append(line.hcpcs)
                                        existing_cpt_set.add(line.hcpcs.value)

                                (
                                    _v_claim_pv,
                                    _v_raw_amt,
                                    v_status,
                                    _v_errors,
                                ) = evaluate_claim_totals(
                                    declared_total=raw_claim_amount,
                                    declared_total_pv=claim_amount_pv,
                                    service_lines=service_lines,
                                    has_total_label=has_total_label,
                                    currency_symbol=claim_currency_symbol,
                                )
                                if v_status == ArithmeticCheckStatus.PASS:
                                    service_lines_sum = sum(float(l.amount.value) for l in service_lines)
                                    calculated_lines_sum = service_lines_sum
                                    arithmetic_status = ArithmeticCheckStatus.PASS
                                    validation_errors = [
                                        e for e in validation_errors
                                        if "ARITHMETIC_DRIFT" not in e and "INCOMPLETE_SERVICE_LINES" not in e
                                    ]
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(f"VLM visual cross-check failed: {exc}")

            # Fallback text-based Groq LLM extraction (only if VLM did not consume the budget and arithmetic is failing)
            if (
                arithmetic_status == ArithmeticCheckStatus.FAIL
                and self.enable_model_fallback
                and self.llm_client
                and self.llm_client.is_available()
                and model_calls_made < 1
            ):
                model_calls_made += 1
                table_spans = [s for s in claim_billing_spans if 0.22 <= s[2].bbox.y1 <= 0.88]
                try:
                    _, _, llm_lines = self._extract_with_groq_llm(table_spans, document_payloads)
                    if llm_lines:
                        # Ground every candidate line against physical OCR evidence
                        grounded_lines = []
                        all_lines_grounded = True
                        for candidate in llm_lines:
                            grounded_line = self._ground_candidate_line(candidate, claim_billing_spans)
                            if grounded_line is None:
                                all_lines_grounded = False
                                break
                            grounded_lines.append(grounded_line)

                        if all_lines_grounded and grounded_lines:
                            new_sum = sum(float(l.amount.value) for l in grounded_lines)
                            if raw_claim_amount is not None and abs(new_sum - raw_claim_amount) < 0.01:
                                service_lines = grounded_lines
                                calculated_lines_sum = new_sum
                                arithmetic_status = ArithmeticCheckStatus.PASS
                                validation_errors = [e for e in validation_errors if "ARITHMETIC_DRIFT" not in e]
                        else:
                            # Rule 12 & Rule 13: Preserve arithmetic failure when candidate values lack grounding
                            logger.info("Candidate service lines lack OCR span grounding; preserving arithmetic failure.")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Fallback Groq extraction failed: {exc}")

        # Completeness Guard Final Verification (Phase 2B Step 2.1 & Step 2.2):
        # Check whether any detected table procedure codes or repeated rows remain missing from service lines
        final_service_cpt_counts = Counter(
            str(line.hcpcs.value).strip()
            for line in service_lines
            if line.hcpcs and line.hcpcs.value and str(line.hcpcs.value).strip() not in ("", "None", "UNKNOWN", "null")
        )
        remaining_missing_table_cpts = [
            code
            for code, table_cnt in table_cpt_counts.items()
            if final_service_cpt_counts.get(code, 0) < table_cnt
        ]
        unassigned_charge_spans = find_unassigned_charge_anchors(claim_billing_spans, service_lines)
        if unassigned_charge_spans:
            validation_errors = [
                error for error in validation_errors
                if not error.startswith("ARITHMETIC_DRIFT:")
            ]
            validation_errors.append(
                "INCOMPLETE_BILL_EXTRACTION: Positive charge rows in the OCR table are not represented "
                f"by extracted service lines ({len(unassigned_charge_spans)} unassigned amount span(s))."
            )
            arithmetic_status = ArithmeticCheckStatus.INDETERMINATE
        if remaining_missing_table_cpts:
            validation_errors = [
                error for error in validation_errors
                if not error.startswith("ARITHMETIC_DRIFT:")
            ]
            arithmetic_status = ArithmeticCheckStatus.INDETERMINATE
            validation_errors.append(
                f"INCOMPLETE_SERVICE_LINES: Detected billing procedure code(s) {sorted(set(remaining_missing_table_cpts))} in claim table are missing from itemized service lines."
            )

        has_unverified_line = any(
            (not l.amount or l.amount.provenance is None) for l in service_lines
        )
        line_items_sum_valid = False
        if has_unverified_line and arithmetic_status == ArithmeticCheckStatus.PASS:
            arithmetic_status = ArithmeticCheckStatus.FAIL
            line_items_sum_valid = False
            validation_errors.append("UNVERIFIED_LINE_CHARGES: Service line charges lack verified OCR provenance.")
        elif remaining_missing_table_cpts:
            # Completeness guard: detected table procedure codes are missing from itemized lines
            line_items_sum_valid = False
        elif unassigned_charge_spans:
            line_items_sum_valid = False
        elif arithmetic_status == ArithmeticCheckStatus.PASS:
            line_items_sum_valid = True
            if not service_lines and raw_claim_amount is not None:
                calculated_lines_sum = raw_claim_amount
        elif arithmetic_status == ArithmeticCheckStatus.NOT_APPLICABLE:
            # Derived total: line arithmetic check is marked not applicable
            line_items_sum_valid = True
        elif arithmetic_status == ArithmeticCheckStatus.FAIL:
            line_items_sum_valid = False
        else:  # INDETERMINATE
            line_items_sum_valid = False
            schema_valid = False

        # 6. Fallback defaults for mandatory header provenance values if unparseable
        if claim_amount_pv is None:
            claim_amount_pv = ProvenanceValue(
                value=0.0,
                confidence=0.0,
                provenance=FieldProvenance(
                    document_id="UNKNOWN",
                    page_number=1,
                    text_content="",
                    provenance_type=ProvenanceType.INFERRED,
                    evidence_quality_score=0.0,
                ),
            )
        if service_date_pv is None:
            service_date_pv = ProvenanceValue(
                value="UNKNOWN",
                confidence=0.0,
                provenance=FieldProvenance(
                    document_id="UNKNOWN",
                    page_number=1,
                    text_content="",
                    provenance_type=ProvenanceType.INFERRED,
                    evidence_quality_score=0.0,
                ),
            )
        if provider_npi_pv is None:
            provider_npi_pv = ProvenanceValue(
                value="0000000000",
                confidence=0.0,
                provenance=FieldProvenance(
                    document_id="UNKNOWN",
                    page_number=1,
                    text_content="",
                    provenance_type=ProvenanceType.INFERRED,
                    evidence_quality_score=0.0,
                ),
            )

        # 7. Adjudicate Verdict & Calibrated Confidence
        verdict, confidence = self._adjudicate_verdict(
            raw_claim_amount=raw_claim_amount,
            raw_npi=raw_npi,
            schema_valid=schema_valid,
            line_items_sum_valid=line_items_sum_valid,
            service_lines=service_lines,
            validation_errors=validation_errors,
            cpt_codes=cpt_codes,
        )

        elapsed_ms = int((time.perf_counter() - start_time) * 1000)

        reason_codes = self._indeterminate_reasons(
            verdict=verdict,
            validation_errors=validation_errors,
            schema_valid=schema_valid,
            raw_claim_amount=raw_claim_amount,
            raw_npi=raw_npi,
            service_lines=service_lines,
        )

        return ClaimsAgentOutput(
            agent_name="claims_agent",
            verdict=verdict,
            confidence=confidence,
            reason_codes=reason_codes,
            claim_amount=claim_amount_pv,
            currency=claim_currency,
            currency_symbol=claim_currency_symbol,
            service_date=service_date_pv,
            provider_name=provider_name_pv,
            provider_npi=provider_npi_pv,
            icd10_codes=icd10_codes,
            cpt_codes=cpt_codes,
            service_lines=service_lines,
            visual_candidates=visual_candidates,
            schema_valid=schema_valid,
            validation_errors=validation_errors,
            line_items_sum_valid=line_items_sum_valid,
            calculated_lines_sum=calculated_lines_sum,
            latency_ms=elapsed_ms,
        )

    # ── Field Extraction Helpers with Span Grounding ─────────────────────────

    def _extract_claim_amount(
        self,
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> tuple[ProvenanceValue | None, float | None, bool]:
        """Extracts total claim amount geometrically from claim form or itemized bill (Spec 05 § 5)."""
        return extract_money_value_geometrically(spans)

    def _extract_service_date(
        self,
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> ProvenanceValue | None:
        """Extracts date of medical service, strictly excluding patient date of birth (DOB)."""
        pattern = re.compile(
            r"(?:date(?:s)?\s*(?:of)?\s*serv(?:i)?ce|service\s*date|admission\s*date|treatment\s*date|procedure\s*date|dos\b)\s*[:]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+[0-9]{1,2},?\s+[0-9]{4})",
            re.IGNORECASE,
        )
        re_birth = re.compile(r"\b(?:birth|dob|born|birthdate)\b", re.IGNORECASE)

        for doc, page_num, span in spans:
            if re_birth.search(span.text):
                continue
            match = pattern.search(span.text)
            if match:
                raw_date = match.group(1).strip()
                p_dt = parse_medical_date(raw_date)
                if p_dt and p_dt.year >= 2020:
                    _, prov = verify_extracted_span(raw_date, span.span_id, page_num, doc)
                    return ProvenanceValue(value=raw_date, confidence=0.96, provenance=prov)

        # Proximity search within adjacent spans
        header_pattern = re.compile(
            r"(?:date(?:s)?\s*(?:of)?\s*serv(?:i)?ce|service\s*date|admission\s*date|treatment\s*date|procedure\s*date|dos\b)",
            re.IGNORECASE,
        )
        date_cand_pattern = re.compile(r"\b([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+[0-9]{1,2},?\s+[0-9]{4})\b")
        for i, (doc, page_num, span) in enumerate(spans):
            if re_birth.search(span.text):
                continue
            if header_pattern.search(span.text):
                for j in range(i + 1, min(i + 4, len(spans))):
                    next_doc, next_page, next_span = spans[j]
                    if re_birth.search(next_span.text):
                        continue
                    if next_doc.document_id == doc.document_id:
                        d_match = date_cand_pattern.search(next_span.text)
                        if d_match:
                            raw_date = d_match.group(1).strip()
                            p_dt = parse_medical_date(raw_date)
                            if p_dt and p_dt.year >= 2020:
                                _, prov = verify_extracted_span(raw_date, next_span.span_id, next_page, next_doc)
                                return ProvenanceValue(value=raw_date, confidence=0.96, provenance=prov)
        return None

    def _extract_provider_name(
        self,
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> ProvenanceValue | None:
        """Extracts rendering physician or medical facility provider name."""
        pattern = re.compile(
            r"(?:physician\s+signature|physician|attending\s+physician|provider)\s*[:]?\s*(Dr\.\s+[A-Za-z\s]+|[A-Za-z\s]+Hospital|[A-Za-z\s]+Clinic)",
            re.IGNORECASE,
        )
        for doc, page_num, span in spans:
            match = pattern.search(span.text)
            if match:
                name = match.group(1).strip()
                _, prov = verify_extracted_span(name, span.span_id, page_num, doc)
                return ProvenanceValue(value=name, confidence=0.92, provenance=prov)
        return None

    def _extract_provider_npi(
        self,
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> tuple[ProvenanceValue | None, str | None]:
        """Extracts 10-digit National Provider Identifier (NPI)."""
        pattern = re.compile(r"(?:npi|billing\s*npi|provider\s*npi)\s*[:#\-]?\s*(?:npi[-:])?\s*([0-9A-Za-z]+)\b", re.IGNORECASE)
        for doc, page_num, span in spans:
            match = pattern.search(span.text)
            if match:
                npi_str = match.group(1).strip()
                _, prov = verify_extracted_span(npi_str, span.span_id, page_num, doc)
                pv = ProvenanceValue(value=npi_str, confidence=0.98, provenance=prov)
                return pv, npi_str
        return None, None

    def _extract_icd10_codes(
        self,
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> list[ProvenanceValue]:
        """Extracts ICD-10 diagnostic codes across English, Hindi, and Spanish clinical documentation (ADR-018)."""
        icd_pattern = re.compile(r"\b([A-TV-Z][0-9]{2}(?:\.[0-9A-Z]{1,4})?)\b")
        found_codes: list[ProvenanceValue] = []
        seen: set[str] = set()

        for doc, page_num, span in spans:
            span_text = span.text.strip()
            span_lower = span_text.lower()
            if any(k in span_lower for k in (
                "diagnosis", "diagnoses", "icd", "primary", "secondary", "hospital course",
                "निदान", "मुख्य", "प्राथमिक", "गौण", "अस्पताल", "रोग", "लक्षण",
                "diagnóstico", "diagnostico", "secundario", "enfermedad",
            )):
                matches = icd_pattern.findall(span_text)
                # Normalize OCR misreadings of ICD letter 'I' as digit '1' (e.g. ICD-10: 110 -> I10, ICD-10: 125 -> I25)
                for m in re.finditer(r"ICD(?:-10)?\s*[:\-]?\s*1([0-9]{2}(?:\.[0-9]{1,4})?)", span_text, re.IGNORECASE):
                    cand = f"I{m.group(1)}"
                    if cand not in matches:
                        matches.append(cand)

                for code in matches:
                    if code not in seen and len(code) >= 3:
                        seen.add(code)
                        _, prov = verify_extracted_span(span_text, span.span_id, page_num, doc)
                        prov = prov.model_copy(update={
                            "normalized_interpretation": code,
                            "evidence_source": EvidenceSource.OCR,
                            "verification_status": EvidenceVerificationStatus.VERIFIED
                            if prov.provenance_type != ProvenanceType.INFERRED
                            else EvidenceVerificationStatus.REJECTED,
                            "assertion_status": ClinicalAssertionStatus.NOT_ASSESSED,
                        })
                        found_codes.append(ProvenanceValue(value=code, confidence=0.95, provenance=prov))

            # Multilingual Clinical Terminology Entity Linking (Hindi & Spanish vernacular terms)
            multi_indications = extract_multilingual_clinical_indications(
                span_text,
                llm_client=None,
                enable_llm_fallback=False,
            )
            for ind in multi_indications:
                code = ind["icd10_code"]
                if code and code not in seen and self.code_catalog and self.code_catalog.is_valid_code(code, CodingSystem.ICD10_CM):
                    seen.add(code)
                    verified, prov = verify_extracted_span(span_text, span.span_id, page_num, doc)
                    if not verified:
                        continue
                    prov = prov.model_copy(update={
                        "normalized_interpretation": code,
                        "evidence_source": EvidenceSource.OCR,
                        "verification_status": EvidenceVerificationStatus.VERIFIED,
                        "assertion_status": ClinicalAssertionStatus.NOT_ASSESSED,
                    })
                    found_codes.append(ProvenanceValue(value=code, confidence=ind.get("confidence", 0.95), provenance=prov))

        # Full clinical narrative fallback when individual spans did not yield an ICD-10 code (Rule 11)
        if not found_codes and getattr(self, "enable_model_fallback", False) and self.llm_client and self.llm_client.is_available():
            full_text = "\n".join(s[2].text for s in spans if any(k in s[2].text.lower() for k in ("diagnos", "निदान", "अस्पताल", "curso", "alta", "ingreso", "summary", "msh-")))
            if full_text and len(full_text.strip()) > 15:
                full_indications = extract_multilingual_clinical_indications(
                    full_text,
                    llm_client=self.llm_client,
                )
                for ind in full_indications:
                    code = ind["icd10_code"]
                    if code not in seen:
                        seen.add(code)
                        found_codes.append(ProvenanceValue(value=code, confidence=ind.get("confidence", 0.92)))

        return found_codes

    def _extract_cpt_codes(
        self,
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> list[ProvenanceValue]:
        """Extracts 5-digit CPT procedure codes strictly from procedure table and catalog (Spec 05 § 5)."""
        active_codes = self.code_catalog.get_active_codes(CodingSystem.CPT) if self.code_catalog else None
        return extract_cpt_codes_from_table(spans, valid_cpt_codes=active_codes)

    def _extract_table_cpt_row_occurrences(
        self,
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> list[tuple[str, str, float, int, str]]:
        """Extracts CPT procedure code occurrences across distinct billing rows.

        Identifies standalone 5-digit CPT codes located in the billing table region
        (0.22 <= y1 <= 0.88, excluding address/header metadata) and groups them by
        distinct vertical row positions (separation >= 0.015) so repeated codes across
        different rows are accurately counted.

        Returns a list of (cpt_code, span_id, y_center, page_num, doc_id).
        """
        re_cpt = re.compile(r"^\d{5}$")
        re_table_bottom = re.compile(r"\b(?:PAGE\s*TOTALS?|28\.\s*TOTAL\s*CHARGES?)\b", re.IGNORECASE)
        re_summary_proc = re.compile(
            r"\b(?:principal|primary|admitting)\s*(?:procedure|diagnosis)\b",
            re.IGNORECASE,
        )
        table_bottom_by_page: dict[tuple[str, int], float] = {}
        for doc, page_num, span in spans:
            if re_table_bottom.search(span.text):
                key = (doc.document_id, page_num)
                if key not in table_bottom_by_page or span.bbox.y0 < table_bottom_by_page[key]:
                    table_bottom_by_page[key] = span.bbox.y0

        occurrences: list[tuple[str, str, float, int, str]] = []

        for doc, page_num, span in spans:
            if span.span_id.endswith(("-k", "-v")):
                continue

            doc_id_lower = doc.document_id.lower()
            if (
                doc_id_lower.startswith(("id_", "rx_", "amend_", "discharge_"))
                or "id_card" in doc_id_lower
                or "passport" in doc_id_lower
                or "license" in doc_id_lower
                or "prescription" in doc_id_lower
                or "discharge" in doc_id_lower
            ):
                continue

            text = span.text.strip()
            bbox = span.bbox

            if (
                RE_ADDRESS_INDICATOR.search(text)
                or RE_HEADER_METADATA_INDICATOR.search(text)
                or RE_HEADER_FIELD_PREFIX.search(text)
                or re_summary_proc.search(text)
                or "74 PRINCIPAL" in text.upper()
            ):
                continue

            key = (doc.document_id, page_num)
            max_y = table_bottom_by_page.get(key, 0.88)
            if bbox.y1 < 0.22 or bbox.y0 > min(0.88, max_y + 0.02):
                continue

            # Exclude CPT tokens that are field values on a claim summary form (e.g. "Procedure Code" -> "27447")
            header_field_re = re.compile(
                r"^\s*(?:procedure\s*code|cpt\s*code|hcpcs\s*code|principal\s*procedure|primary\s*procedure|admitting\s*procedure)\s*[:#]?\s*$",
                re.IGNORECASE,
            )
            is_header_field_value = any(
                d.document_id == doc.document_id
                and p == page_num
                and abs(((s.bbox.y0 + s.bbox.y1) / 2.0) - ((bbox.y0 + bbox.y1) / 2.0)) < 0.015
                and s.bbox.x1 < bbox.x0
                and header_field_re.match(s.text.strip())
                for d, p, s in spans
            )
            if is_header_field_value:
                continue

            tokens = re.split(r"[\s,;:/]+", text)
            for token in tokens:
                clean_token = token.strip()
                if re_cpt.fullmatch(clean_token):
                    if self.code_catalog is not None and not self.code_catalog.is_valid_code(clean_token, CodingSystem.CPT):
                        continue

                    y_center = (bbox.y0 + bbox.y1) / 2.0
                    # Avoid duplicate counting of the same code on the same row (separation < 0.015 on same page/doc)
                    already_on_row = any(
                        c == clean_token
                        and p == page_num
                        and d == doc.document_id
                        and abs(y - y_center) < 0.015
                        for c, _, y, p, d in occurrences
                    )
                    if not already_on_row:
                        occurrences.append((clean_token, span.span_id, y_center, page_num, doc.document_id))

        return occurrences

    def _extract_service_lines(
        self,
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
        y_tolerance: float = 0.012,
        currency: str = "USD",
        currency_symbol: str = "$",
    ) -> list[ServiceLine]:
        """Extracts structured line items: line #, CPT, description, date, qty, unit, total (ADR-018)."""
        return extract_service_lines_from_table(
            spans,
            y_tolerance=y_tolerance,
            currency=currency,
            currency_symbol=currency_symbol,
        )

    def _ground_candidate_line(
        self,
        candidate: ServiceLine,
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> ServiceLine | None:
        """
        Validates an LLM-generated candidate service line against physical OCR text spans (Spec 06 § 2).
        Returns a grounded ServiceLine with valid ProvenanceValue, or None if ungrounded.
        """
        if not candidate.amount or candidate.amount.value is None:
            return None

        try:
            amt_float = float(candidate.amount.value)
        except (ValueError, TypeError):
            return None

        # Search for candidate amount in physical OCR spans
        amt_match: tuple[OCRDocumentPayload, int, OCRTextSpan] | None = None
        amt_str_2dec = f"{amt_float:.2f}"
        for doc, page_num, span in spans:
            clean_text = span.text.replace("$", "").replace(",", "").strip()
            if amt_str_2dec in clean_text or f"{amt_float:.0f}" == clean_text:
                amt_match = (doc, page_num, span)
                break

        if not amt_match:
            # Candidate amount is completely absent from physical OCR evidence
            return None

        doc, page_num, span = amt_match
        ok, amt_prov = verify_extracted_span(str(amt_float), span.span_id, page_num, doc)
        if not ok or not amt_prov or amt_prov.provenance_type == ProvenanceType.INFERRED:
            return None

        # Search for CPT procedural code if present
        cpt_prov = None
        cpt_val = candidate.hcpcs.value if candidate.hcpcs else None
        if cpt_val and str(cpt_val).strip() not in ("", "None", "UNKNOWN", "null"):
            clean_cpt = str(cpt_val).strip()
            for doc_c, page_c, span_c in spans:
                if clean_cpt in span_c.text:
                    ok_cpt, p_cpt = verify_extracted_span(clean_cpt, span_c.span_id, page_c, doc_c)
                    if ok_cpt and p_cpt.provenance_type != ProvenanceType.INFERRED:
                        cpt_prov = p_cpt
                        break
            if not cpt_prov:
                # Procedural code cannot be grounded in OCR evidence
                return None

        desc_prov = None
        desc_val = candidate.description.value if candidate.description else "Medical Service"
        if candidate.description and candidate.description.provenance:
            desc_prov = candidate.description.provenance
        elif desc_val:
            for doc_d, page_d, span_d in spans:
                if desc_val.lower() in span_d.text.lower():
                    _, desc_prov = verify_extracted_span(desc_val, span_d.span_id, page_d, doc_d)
                    break

        rev_val = candidate.rev_code.value if candidate.rev_code else "UNKNOWN"
        rev_prov = candidate.rev_code.provenance if candidate.rev_code else None
        if not rev_prov and rev_val != "UNKNOWN" and desc_prov:
            rev_prov = desc_prov

        date_prov = None
        date_val = candidate.service_date.value if candidate.service_date else "UNKNOWN"
        if candidate.service_date and candidate.service_date.provenance:
            date_prov = candidate.service_date.provenance
        elif date_val and date_val != "UNKNOWN":
            for doc_dt, page_dt, span_dt in spans:
                if date_val in span_dt.text:
                    _, date_prov = verify_extracted_span(date_val, span_dt.span_id, page_dt, doc_dt)
                    break

        return ServiceLine(
            line_number=candidate.line_number,
            rev_code=ProvenanceValue(value=rev_val, confidence=0.85 if rev_prov else (0.80 if rev_val != "UNKNOWN" else 0.0), provenance=rev_prov),
            description=ProvenanceValue(value=desc_val, confidence=0.85, provenance=desc_prov),
            hcpcs=ProvenanceValue(value=cpt_val, confidence=0.88, provenance=cpt_prov),
            service_date=ProvenanceValue(value=date_val, confidence=0.85, provenance=date_prov),
            units=candidate.units,
            amount=ProvenanceValue(value=amt_float, confidence=0.90, provenance=amt_prov),
            currency=getattr(candidate, "currency", "USD"),
            currency_symbol=getattr(candidate, "currency_symbol", "$"),
        )

    def _resolve_document_image(
        self,
        case_id: str,
        doc_id: str,
        page_number: int,
        document_images: dict[str, bytes] | None,
        storage: StorageBackend | None,
    ) -> bytes | None:
        """Resolves raw document page image bytes from memory dict, storage backend, or local filesystem."""
        if document_images:
            if doc_id in document_images:
                return document_images[doc_id]
            doc_stem = Path(doc_id).stem
            if doc_stem in document_images:
                return document_images[doc_stem]
            for k, v in document_images.items():
                if doc_stem in k or doc_id in k or k in doc_id:
                    return v

        active_storage = storage or self.storage_backend
        clean_doc_id = doc_id.replace("DOC-", "")
        pt_id = case_id.split("-")[-1]
        pt_norm = pt_id if "_" in pt_id else (f"PT_{pt_id[2:]}" if pt_id.startswith("PT") else pt_id)

        if active_storage:
            doc_stem = Path(doc_id).stem
            clean_stem = Path(clean_doc_id).stem
            candidates = [
                f"cases/{case_id}/pages/{doc_stem}_p{page_number}.png",
                f"cases/{case_id}/pages/{clean_stem}_p{page_number}.png",
                f"cases/{case_id}/pages/{doc_id}_p{page_number}.png",
                f"cases/{case_id}/pages/{doc_id}",
                f"cases/{case_id}/pages/{clean_doc_id}",
                f"cases/{case_id}/raw/{doc_id}",
                f"cases/{case_id}/raw/{clean_doc_id}_{clean_doc_id}.png",
                f"cases/{case_id}/raw/{clean_doc_id}",
                f"cases/{case_id}/pages/{doc_stem}.png",
                f"raw/{case_id}/{clean_doc_id}.png",
                f"raw/{case_id}/{clean_doc_id}",
            ]
            for cand_key in candidates:
                try:
                    if active_storage.exists(cand_key):
                        return active_storage.get_bytes(cand_key)
                except Exception:  # noqa: BLE001, S110
                    pass

        dataset_paths = [
            Path("dataset/claim_forms") / doc_id,
            Path("dataset/claim_forms") / f"{Path(doc_id).stem}.png",
            Path("dataset/claim_forms") / clean_doc_id,
            Path("dataset/claim_forms") / f"{Path(clean_doc_id).stem}.png",
            Path("dataset/claim_forms") / f"claim_{pt_norm}.png",
            Path("dataset/claim_forms") / f"claim_{pt_id}.png",
        ]
        for p in dataset_paths:
            if p.exists():
                try:
                    return p.read_bytes()
                except Exception:  # noqa: BLE001, S110
                    pass

        return None

    def _corroborate_visual_candidate(
        self,
        candidate: VisualEvidenceCandidate,
        image_bytes: bytes | None,
        doc: OCRDocumentPayload,
        claim_billing_spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> tuple[VisualEvidenceCandidate, OCRTextSpan | None]:
        """
        Stage E: Independent cross-check via targeted local OCR reread (Spec 05 § 5, Spec 06 § 2).
        Performs local OCR on the crop defined by candidate.bbox.
        If local OCR detects the proposed value, synthesizes a verified OCRTextSpan
        and marks candidate as OCR_CORROBORATED.
        If local OCR does not find the value, candidate remains UNRESOLVED.
        If conflicting text is found, candidate is marked CONFLICTING.
        """
        if candidate.status == VisualEvidenceStatus.CONFLICTING:
            return candidate, None

        proposed_str = str(candidate.proposed_value).strip() if candidate.proposed_value is not None else ""
        if not proposed_str or proposed_str in ("None", "UNKNOWN", "null"):
            candidate.status = VisualEvidenceStatus.UNRESOLVED
            return candidate, None

        for d, p, s in claim_billing_spans:
            if d.document_id == doc.document_id and p == candidate.page_number and proposed_str in s.text:
                candidate.status = VisualEvidenceStatus.OCR_CORROBORATED
                candidate.corroborating_span_id = s.span_id
                candidate.verification_method = "existing_ocr_span_match"
                return candidate, s

        if not image_bytes or not candidate.bbox:
            candidate.status = VisualEvidenceStatus.UNRESOLVED
            return candidate, None

        engine = get_rapid_ocr_engine()
        if not engine:
            candidate.status = VisualEvidenceStatus.UNRESOLVED
            return candidate, None

        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                width_px, height_px = img.size
                bbox = candidate.bbox
                x0 = max(0, int((bbox.x0 - 0.02) * width_px))
                y0 = max(0, int((bbox.y0 - 0.02) * height_px))
                x1 = min(width_px, int((bbox.x1 + 0.02) * width_px))
                y1 = min(height_px, int((bbox.y1 + 0.02) * height_px))

                crop_img = img.crop((x0, y0, x1, y1))
                crop_buf = io.BytesIO()
                crop_img.save(crop_buf, format="PNG")
                crop_bytes = crop_buf.getvalue()

            ocr_res, _ = engine(crop_bytes)
            if not ocr_res:
                candidate.status = VisualEvidenceStatus.UNRESOLVED
                return candidate, None

            found_match = False
            best_conf = 0.0
            best_dt_box = None

            for dt_box, line_text, conf in ocr_res:
                line_str = str(line_text).strip()
                clean_target = proposed_str.replace("$", "").replace(",", "")
                clean_ocr = line_str.replace("$", "").replace(",", "")
                if clean_target in clean_ocr or clean_ocr in clean_target:
                    found_match = True
                    best_conf = float(conf) if conf is not None else 0.95
                    best_dt_box = dt_box
                    break

            if found_match:
                if best_dt_box:
                    xs = [pt[0] for pt in best_dt_box]
                    ys = [pt[1] for pt in best_dt_box]
                    page_x_min = max(0.0, min(1.0, (x0 + min(xs)) / width_px))
                    page_x_max = max(0.0, min(1.0, (x0 + max(xs)) / width_px))
                    page_y_min = max(0.0, min(1.0, (y0 + min(ys)) / height_px))
                    page_y_max = max(0.0, min(1.0, (y0 + max(ys)) / height_px))
                else:
                    page_x_min = bbox.x0
                    page_y_min = bbox.y0
                    page_x_max = bbox.x1
                    page_y_max = bbox.y1

                span_id = f"span-vlm-reread-p{candidate.page_number}-{int(time.time()*1000)%100000}"
                new_span = OCRTextSpan(
                    span_id=span_id,
                    page_number=candidate.page_number,
                    text=proposed_str,
                    confidence=round(best_conf, 4),
                    bbox=OCRBoundingBox(
                        x0=round(page_x_min, 4),
                        y0=round(page_y_min, 4),
                        x1=round(page_x_max, 4),
                        y1=round(page_y_max, 4),
                    ),
                )

                page_idx = candidate.page_number - 1
                if 0 <= page_idx < len(doc.pages):
                    doc.pages[page_idx].spans.append(new_span)

                candidate.status = VisualEvidenceStatus.OCR_CORROBORATED
                candidate.corroborating_span_id = span_id
                candidate.verification_method = "local_ocr_crop_reread"
                return candidate, new_span
            else:
                candidate.status = VisualEvidenceStatus.UNRESOLVED
                return candidate, None

        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Targeted local OCR reread failed for candidate {candidate.proposed_value}: {exc}")
            candidate.status = VisualEvidenceStatus.UNRESOLVED
            return candidate, None

    def _adjudicate_verdict(
        self,
        raw_claim_amount: float | None,
        raw_npi: str | None,
        schema_valid: bool,
        line_items_sum_valid: bool,
        service_lines: list[ServiceLine],
        validation_errors: list[str],
        cpt_codes: list[ProvenanceValue] | None = None,
    ) -> tuple[AgentVerdict, float]:
        """Adjudicates claims output verdict and confidence according to canonical rules."""
        if any("NEGATIVE" in err for err in validation_errors):
            return AgentVerdict.FAIL, 0.95

        if (
            any("ARITHMETIC_DRIFT" in err for err in validation_errors)
            or any("INCOMPLETE_SERVICE_LINES" in err for err in validation_errors)
            or any("CONFLICTING_VISUAL_EVIDENCE" in err for err in validation_errors)
        ):
            return AgentVerdict.CONFLICT, 0.85

        if any("UNRESOLVED_VISUAL_CANDIDATE" in err for err in validation_errors):
            return AgentVerdict.INDETERMINATE, 0.50

        if any("UNVERIFIED_ICD10_CODES" in err for err in validation_errors):
            return AgentVerdict.INDETERMINATE, 0.50

        # Validate format of CPT / HCPCS procedural codes (5 digits or HCPCS alphanumeric)
        all_cpt_values: set[str] = set()
        if cpt_codes:
            for pv in cpt_codes:
                if pv.value and str(pv.value).strip() not in ("", "None", "UNKNOWN", "null"):
                    all_cpt_values.add(str(pv.value).strip())
        for line in service_lines:
            if line.hcpcs and line.hcpcs.value and str(line.hcpcs.value).strip() not in ("", "None", "UNKNOWN", "null"):
                all_cpt_values.add(str(line.hcpcs.value).strip())

        re_valid_cpt_format = re.compile(r"^\d{5}$|^[A-Z]\d{4}$")
        malformed_codes = [code for code in all_cpt_values if not re_valid_cpt_format.match(code)]
        if malformed_codes:
            validation_errors.append(
                f"MALFORMED_CPT_CODES: Code(s) {', '.join(sorted(malformed_codes))} do not conform to valid CPT/HCPCS format."
            )
            return AgentVerdict.INDETERMINATE, 0.50

        if self.code_catalog is not None:
            unsupported_codes = [
                code for code in all_cpt_values
                if not self.code_catalog.is_valid_code(code, CodingSystem.CPT)
                and not self.code_catalog.is_valid_code(code, CodingSystem.HCPCS)
            ]
        else:
            unsupported_codes = []

        if unsupported_codes:
            validation_errors.append(
                f"UNSUPPORTED_CPT_CODES: Code(s) {', '.join(sorted(unsupported_codes))} cannot be verified against the supported CPT catalog."
            )
            return AgentVerdict.INDETERMINATE, 0.50

        # Charge-bearing claim must have procedural codes
        has_charges = (raw_claim_amount is not None and raw_claim_amount > 0) or any(
            float(line.amount.value) > 0 for line in service_lines if line.amount and line.amount.value is not None
        )
        if has_charges and not all_cpt_values:
            validation_errors.append(
                "MISSING_PROCEDURAL_CODES: Claim with billed charges lacks verified CPT procedure codes."
            )
            return AgentVerdict.INDETERMINATE, 0.50

        if not schema_valid or raw_claim_amount is None or raw_npi is None:
            return AgentVerdict.INDETERMINATE, 0.45

        if line_items_sum_valid and schema_valid:
            if any(l.amount and l.amount.provenance is None for l in service_lines):
                return AgentVerdict.INDETERMINATE, 0.50

            # Calibrate confidence based on whether codes are confirmed in catalog
            if self.code_catalog is not None:
                all_in_catalog = bool(all_cpt_values) and all(
                    self.code_catalog.is_valid_code(code, CodingSystem.CPT) for code in all_cpt_values
                )
                confidence = 0.97 if all_in_catalog else 0.88
            else:
                confidence = 0.88
            return AgentVerdict.PASS, confidence


        return AgentVerdict.INDETERMINATE, 0.50

    def _indeterminate_reasons(
        self,
        verdict: AgentVerdict,
        validation_errors: list[str],
        schema_valid: bool,
        raw_claim_amount: float | None,
        raw_npi: str | None,
        service_lines: list[ServiceLine],
    ) -> list[IndeterminateReason]:
        """Maps the validation errors behind an INDETERMINATE verdict to structured reason codes."""
        if verdict != AgentVerdict.INDETERMINATE:
            return []
        by_prefix = {
            "UNRESOLVED_VISUAL_CANDIDATE": IndeterminateReason.UNRESOLVED_VISUAL_EVIDENCE,
            "UNVERIFIED_ICD10_CODES": IndeterminateReason.UNVERIFIED_DIAGNOSIS_CODE,
            "MALFORMED_CPT_CODES": IndeterminateReason.UNVERIFIED_PROCEDURE_CODE,
            "UNSUPPORTED_CPT_CODES": IndeterminateReason.UNVERIFIED_PROCEDURE_CODE,
            "MISSING_PROCEDURAL_CODES": IndeterminateReason.INCOMPLETE_CLAIM_DATA,
        }
        codes = [code for prefix, code in by_prefix.items() if any(prefix in err for err in validation_errors)]
        if not codes:
            if not schema_valid or raw_claim_amount is None or raw_npi is None:
                codes.append(IndeterminateReason.INCOMPLETE_CLAIM_DATA)
            if any(line.amount and line.amount.provenance is None for line in service_lines):
                codes.append(IndeterminateReason.MISSING_PROVENANCE)
        return list(dict.fromkeys(codes)) or [IndeterminateReason.OTHER]

    def _extract_with_groq_llm(
        self,
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
        document_payloads: list[OCRDocumentPayload],
    ) -> tuple[list[ProvenanceValue], list[ProvenanceValue], list[ServiceLine]]:
        """Invokes Groq LLM to extract candidate codes and service lines from unstructured documents."""
        if not self.llm_client or not self.llm_client.is_available():
            return [], [], []

        all_text = "\n".join(
            f"[{span.span_id}] {span.text}" for _, _, span in spans
        )

        system_prompt = (
            "You are a medical claims extraction assistant with comprehensive clinical and billing terminology understanding.\n"
            "Analyze the medical claim or clinical document text and extract:\n"
            "1. ICD-10 diagnosis codes (e.g., I21.9, E11.9, J45.909)\n"
            "2. CPT procedure codes (e.g., 99213, 93000, 71045)\n"
            "3. Itemized service line items with line_number, cpt_code, rev_code, description, service_date, quantity, unit_charge, total_charge.\n"
            "\n"
            "SEMANTIC BILLING INSTRUCTIONS:\n"
            "- Healthcare facilities worldwide use synonyms: e.g., 'Pharmacy', 'Medicines', 'Drugs', 'Consumables' represent Pharmacy services (Revenue Code 0250).\n"
            "- 'Bed charges', 'Room rent', 'Ward charges' represent Room and Board (Revenue Code 0100).\n"
            "- 'Lab tests', 'Pathology', 'Blood investigation' represent Laboratory (Revenue Code 0300).\n"
            "- 'Operation theater', 'OT charges', 'Surgery' represent Surgical services (Revenue Code 0360).\n"
            "- If a service line lacks a 5-digit CPT code, provide its UB-04 revenue code or category shorthand in 'rev_code' (e.g. '0250', '0100', '0300', 'PHARM', 'ROOM', 'LAB').\n"
            "- Do NOT include header summary fields (such as 'Total Claim Amount' or top-level procedure indication) as service line items.\n"
            "\n"
            "Respond in JSON format:\n"
            "{\n"
            '  "icd10_codes": ["I21.9"],\n'
            '  "cpt_codes": ["99213"],\n'
            '  "service_lines": [\n'
            '    {\n'
            '      "line_number": 1,\n'
            '      "cpt_code": "99213",\n'
            '      "rev_code": null,\n'
            '      "description": "Office visit",\n'
            '      "service_date": "2026-03-15",\n'
            '      "quantity": 1.0,\n'
            '      "unit_charge": 100.0,\n'
            '      "total_charge": 100.0\n'
            '    }\n'
            '  ]\n'
            "}"
        )
        user_prompt = f"Claim Document Text:\n{wrap_untrusted_content(all_text[:4000])}"

        try:
            res = self.llm_client.generate_json(prompt=user_prompt, system_prompt=system_prompt)
        except (GroqLLMError, ValueError):
            return [], [], []

        extracted_icds: list[ProvenanceValue] = []
        for code_str in res.get("icd10_codes", []):
            if isinstance(code_str, str) and code_str.strip():
                clean_code = code_str.strip()
                matched_pv: ProvenanceValue | None = None
                for doc, page_num, span in spans:
                    if clean_code in span.text:
                        _, prov = verify_extracted_span(clean_code, span.span_id, page_num, doc)
                        matched_pv = ProvenanceValue(value=clean_code, confidence=0.95, provenance=prov)
                        break
                if matched_pv:
                    extracted_icds.append(matched_pv)
                else:
                    extracted_icds.append(ProvenanceValue(value=clean_code, confidence=0.85, provenance=None))

        extracted_cpts: list[ProvenanceValue] = []
        for code_str in res.get("cpt_codes", []):
            if isinstance(code_str, str) and code_str.strip():
                clean_code = code_str.strip()
                matched_pv = None
                for doc, page_num, span in spans:
                    if clean_code in span.text:
                        _, prov = verify_extracted_span(clean_code, span.span_id, page_num, doc)
                        matched_pv = ProvenanceValue(value=clean_code, confidence=0.95, provenance=prov)
                        break
                if matched_pv:
                    extracted_cpts.append(matched_pv)
                else:
                    extracted_cpts.append(ProvenanceValue(value=clean_code, confidence=0.85, provenance=None))

        extracted_lines: list[ServiceLine] = []
        for raw_line in res.get("service_lines", []):
            if not isinstance(raw_line, dict):
                continue
            try:
                line_no = int(raw_line.get("line_number", len(extracted_lines) + 1))
                raw_cpt = raw_line.get("cpt_code")
                cpt_val = str(raw_cpt).strip() if raw_cpt and str(raw_cpt).strip() not in ("", "None", "null") else ""
                desc_val = str(raw_line.get("description", "")).strip()
                date_val = str(raw_line.get("service_date", "")).strip()
                qty_val = float(raw_line.get("quantity", 1.0))
                unit_val = float(raw_line.get("unit_charge", 0.0))
                tot_val = float(raw_line.get("total_charge", qty_val * unit_val))

                raw_rev = raw_line.get("rev_code")
                rev_val = "UNKNOWN"
                if raw_rev and str(raw_rev).strip() not in ("", "None", "null", "UNKNOWN"):
                    clean_rev = str(raw_rev).strip().upper()
                    if is_plausible_revenue_code(clean_rev, self.code_catalog):
                        rev_val = clean_rev

                prov_rev = None
                if rev_val != "UNKNOWN":
                    for doc, page_num, span in spans:
                        if (raw_rev and str(raw_rev) in span.text) or (desc_val and any(w in span.text for w in desc_val.split() if len(w) > 3)):
                            _, prov_rev = verify_extracted_span(span.text, span.span_id, page_num, doc)
                            break

                extracted_lines.append(
                    ServiceLine(
                        line_number=line_no,
                        rev_code=ProvenanceValue(value=rev_val, confidence=0.90 if prov_rev else (0.80 if rev_val != "UNKNOWN" else 0.0), provenance=prov_rev),
                        description=ProvenanceValue(value=desc_val, confidence=0.90, provenance=None),
                        hcpcs=ProvenanceValue(value=cpt_val, confidence=0.90, provenance=None),
                        service_date=ProvenanceValue(value=date_val, confidence=0.90, provenance=None),
                        units=ProvenanceValue(value=qty_val, confidence=0.90, provenance=None),
                        amount=ProvenanceValue(value=tot_val, confidence=0.90, provenance=None),
                    )
                )
            except (ValueError, TypeError):
                continue

        return extracted_icds, extracted_cpts, extracted_lines
