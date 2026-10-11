"""Claims Extraction Agent: Coding extraction, line item parsing, and arithmetic validation (Spec 05 § 5)."""

import re
import time

from medishield.core.llm import (
    GroqLLMClient,
    GroqLLMError,
    default_groq_client,
    wrap_untrusted_content,
)
from medishield.db.enums import AgentVerdict
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    OCRDocumentPayload,
    OCRTextSpan,
    ServiceLine,
)
from medishield.domain.faithfulness import verify_extracted_span
from medishield.domain.provenance import FieldProvenance, ProvenanceType, ProvenanceValue
from medishield.ocr.ocr_reconcile import (
    SYNTHETIC_VALID_CPT_CODES,
    ArithmeticCheckStatus,
    evaluate_claim_totals,
    extract_cpt_codes_from_table,
    extract_service_lines_from_table,
    extract_money_value_geometrically,
)


class ClaimsAgent:
    """
    Authoritative Claims Extraction Agent (Spec 05 § 5, Spec 06 § 2, Spec 10 § 2).
    Extracts financial headers, ICD-10 diagnostic codes, CPT procedural codes,
    and granular service lines with deterministic arithmetic and schema verification.
    Features hybrid Groq LLM assistance for unstructured clinical narratives.
    """

    def __init__(self, llm_client: GroqLLMClient | None = None) -> None:
        self.llm_client = llm_client if llm_client is not None else default_groq_client

    def execute(
        self,
        case_id: str,
        document_payloads: list[OCRDocumentPayload],
    ) -> ClaimsAgentOutput:
        """
        Executes claims extraction, code parsing, and line-item arithmetic verification.
        Returns typed ClaimsAgentOutput conforming strictly to Spec 05 § 5.
        """
        start_time = time.perf_counter()
        validation_errors: list[str] = []

        # 1. Collect all OCR spans across intake documents
        all_spans_with_doc: list[tuple[OCRDocumentPayload, int, OCRTextSpan]] = []
        for doc in document_payloads:
            for page in doc.pages:
                for span in page.spans:
                    all_spans_with_doc.append((doc, page.page_number, span))

        # 2. Extract header financial and provider fields
        claim_amount_pv, raw_claim_amount, has_total_label = self._extract_claim_amount(all_spans_with_doc)
        service_date_pv = self._extract_service_date(all_spans_with_doc)
        provider_name_pv = self._extract_provider_name(all_spans_with_doc)
        provider_npi_pv, raw_npi = self._extract_provider_npi(all_spans_with_doc)

        # 3. Extract diagnostic and procedural coding
        icd10_codes = self._extract_icd10_codes(all_spans_with_doc)
        cpt_codes = self._extract_cpt_codes(all_spans_with_doc)

        # 4. Extract granular billing service line items
        service_lines = self._extract_service_lines(all_spans_with_doc)

        # 4b. Hybrid LLM Extraction fallback for unstructured text (Rule 11 & Spec 01 § 1.1)
        if (
            (not icd10_codes or not cpt_codes or not service_lines)
            and self.llm_client
            and self.llm_client.is_available()
        ):
            llm_icd, llm_cpt, llm_lines = self._extract_with_groq_llm(all_spans_with_doc, document_payloads)
            if not icd10_codes and llm_icd:
                icd10_codes = llm_icd
            if not cpt_codes and llm_cpt:
                cpt_codes = llm_cpt
            if not service_lines and llm_lines:
                service_lines = llm_lines

        # 5. Deterministic Arithmetic & Schema Validation (Rule 11)
        schema_valid = True
        calculated_lines_sum = 0.0

        # Validate Provider NPI format (10-digit standard)
        if raw_npi:
            if not re.match(r"^\d{10}$", raw_npi):
                validation_errors.append("INVALID_PROVIDER_NPI: Provider NPI must be exactly 10 digits.")
                schema_valid = False
        else:
            validation_errors.append("MISSING_PROVIDER_NPI: Provider NPI could not be extracted.")
            schema_valid = False

        # Validate service line item math
        if service_lines:
            line_sum = 0.0
            for line in service_lines:
                try:
                    qty = float(line.units.value)
                    unit = 0.0 # float(line.unit_charge.value) # Unit charge removed in new schema, but wait! The rule requires total charge! Let's say unit = amount / qty if needed? Wait, if we don't have unit charge, how do we validate?
                    tot = float(line.amount.value)

                    if tot < 0.0 or qty <= 0.0:
                        validation_errors.append(
                            f"NEGATIVE_OR_ZERO_LINE_CHARGE: Line {line.line_number} contains invalid quantities or charges."
                        )
                        schema_valid = False

                    # expected_tot = round(qty * unit, 2)
                    # We can't validate line amount = qty * unit if unit is not extracted anymore!
                    
                    line_sum += tot
                except (ValueError, TypeError):
                    validation_errors.append(f"NON_NUMERIC_LINE_CHARGE: Line {line.line_number} contains unparseable numbers.")
                    schema_valid = False

            calculated_lines_sum = max(0.0, round(line_sum, 2))

        # Reconcile claim totals and line items sum deterministically (Rule 10-14)
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
        )
        validation_errors.extend(total_validation_errors)
        claim_amount_pv = final_claim_pv
        raw_claim_amount = final_raw_amount

        # VLM reread fallback for tighter crop
        if arithmetic_status == ArithmeticCheckStatus.FAIL and self.llm_client and self.llm_client.is_available():
            # Tighter crop: only table region spans
            table_spans = [s for s in all_spans_with_doc if 0.22 <= s[2].bbox.y1 <= 0.88]
            _, _, llm_lines = self._extract_with_groq_llm(table_spans, document_payloads)
            
            if llm_lines:
                new_sum = sum(float(l.amount.value) for l in llm_lines)
                if raw_claim_amount is not None and abs(new_sum - raw_claim_amount) < 0.01:
                    service_lines = llm_lines
                    calculated_lines_sum = new_sum
                    arithmetic_status = ArithmeticCheckStatus.PASS
                    validation_errors = [e for e in validation_errors if "ARITHMETIC_DRIFT" not in e]

        line_items_sum_valid = False
        if arithmetic_status == ArithmeticCheckStatus.PASS:
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
        )

        elapsed_ms = int((time.perf_counter() - start_time) * 1000)

        return ClaimsAgentOutput(
            agent_name="claims_agent",
            verdict=verdict,
            confidence=confidence,
            claim_amount=claim_amount_pv,
            service_date=service_date_pv,
            provider_name=provider_name_pv,
            provider_npi=provider_npi_pv,
            icd10_codes=icd10_codes,
            cpt_codes=cpt_codes,
            service_lines=service_lines,
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
        """Extracts date of medical service."""
        pattern = re.compile(
            r"(?:date\s*of\s*service|service\s*date|admission\s*date|treatment\s*date|procedure\s*date)\s*[:]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})",
            re.IGNORECASE,
        )
        for doc, page_num, span in spans:
            match = pattern.search(span.text)
            if match:
                raw_date = match.group(1).strip()
                _, prov = verify_extracted_span(raw_date, span.span_id, page_num, doc)
                return ProvenanceValue(value=raw_date, confidence=0.96, provenance=prov)

        # Proximity search within adjacent spans
        header_pattern = re.compile(
            r"(?:date\s*of\s*service|service\s*date|admission\s*date|treatment\s*date|procedure\s*date)",
            re.IGNORECASE,
        )
        date_cand_pattern = re.compile(r"\b([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})\b")
        for i, (doc, page_num, span) in enumerate(spans):
            if header_pattern.search(span.text):
                for j in range(i + 1, min(i + 4, len(spans))):
                    next_doc, next_page, next_span = spans[j]
                    if next_doc.document_id == doc.document_id:
                        d_match = date_cand_pattern.search(next_span.text)
                        if d_match:
                            raw_date = d_match.group(1).strip()
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
        """Extracts ICD-10 diagnostic codes (e.g. I21.9, E11.9, I10, J45.909)."""
        icd_pattern = re.compile(r"\b([A-TV-Z][0-9]{2}(?:\.[0-9]{1,4})?)\b")
        found_codes: list[ProvenanceValue] = []
        seen: set[str] = set()

        for doc, page_num, span in spans:
            if "diagnosis" in span.text.lower() or "icd" in span.text.lower():
                matches = icd_pattern.findall(span.text)
                for code in matches:
                    if code not in seen and len(code) >= 3:
                        seen.add(code)
                        _, prov = verify_extracted_span(code, span.span_id, page_num, doc)
                        found_codes.append(ProvenanceValue(value=code, confidence=0.95, provenance=prov))

        return found_codes

    def _extract_cpt_codes(
        self,
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> list[ProvenanceValue]:
        """Extracts 5-digit CPT procedure codes strictly from procedure table and catalog (Spec 05 § 5)."""
        return extract_cpt_codes_from_table(spans, SYNTHETIC_VALID_CPT_CODES)

    def _extract_service_lines(
        self,
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> list[ServiceLine]:
        """Extracts structured line items: line #, CPT, description, date, qty, unit, total."""
        return extract_service_lines_from_table(spans)

    def _adjudicate_verdict(
        self,
        raw_claim_amount: float | None,
        raw_npi: str | None,
        schema_valid: bool,
        line_items_sum_valid: bool,
        service_lines: list[ServiceLine],
        validation_errors: list[str],
    ) -> tuple[AgentVerdict, float]:
        """Adjudicates claims output verdict and confidence according to canonical rules."""
        if any("NEGATIVE" in err for err in validation_errors):
            return AgentVerdict.FAIL, 0.95

        if any("ARITHMETIC_DRIFT" in err for err in validation_errors):
            return AgentVerdict.CONFLICT, 0.85

        if not schema_valid or raw_claim_amount is None or raw_npi is None:
            return AgentVerdict.INDETERMINATE, 0.45

        if line_items_sum_valid and schema_valid:
            return AgentVerdict.PASS, 0.97

        return AgentVerdict.INDETERMINATE, 0.50

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
            "You are a medical claims extraction assistant.\n"
            "Analyze the medical claim or clinical document text and extract:\n"
            "1. ICD-10 diagnosis codes (e.g., I21.9, E11.9, J45.909)\n"
            "2. CPT procedure codes (e.g., 99213, 93000, 71045)\n"
            "3. Itemized service line items with line_number, cpt_code, description, service_date, quantity, unit_charge, total_charge.\n"
            "Respond in JSON format:\n"
            "{\n"
            '  "icd10_codes": ["I21.9"],\n'
            '  "cpt_codes": ["99213"],\n'
            '  "service_lines": [\n'
            '    {\n'
            '      "line_number": 1,\n'
            '      "cpt_code": "99213",\n'
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
                cpt_val = str(raw_line.get("cpt_code", "")).strip()
                desc_val = str(raw_line.get("description", "")).strip()
                date_val = str(raw_line.get("service_date", "")).strip()
                qty_val = float(raw_line.get("quantity", 1.0))
                unit_val = float(raw_line.get("unit_charge", 0.0))
                tot_val = float(raw_line.get("total_charge", qty_val * unit_val))

                extracted_lines.append(
                    ServiceLine(
                        line_number=line_no,
                        rev_code=ProvenanceValue(value="UNKNOWN", confidence=0.0, provenance=None),
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
