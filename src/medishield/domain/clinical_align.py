"""Deterministic Clinical Alignment Engine (Spec 01 § 4, Spec 02 § 3, Spec 06 § 5, ADR-015).

Verifies line-level clinical evidence between billed claim items (CPT/HCPCS, Revenue codes,
bed days) and clinical documentation (discharge summaries, operative reports, lab results).
Executes deterministic logic with zero generative LLM decisioning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, ClassVar, Protocol, runtime_checkable

from sqlalchemy.exc import OperationalError

from medishield.db.enums import AgentVerdict, CodingSystem
from medishield.domain.contracts import (
    BedDayBillingRuleContract,
    BedDayReconciliation,
    ClinicalAlignmentOutput,
    ClinicalIndicationStatus,
    CriterionEvaluationOutcome,
    FacilityBillingConvention,
    GuidelineCitation,
    LineClinicalEvidence,
    MandatoryGuidelineCriterion,
    MedicalNecessityStatus,
    PerformanceEvidenceStatus,
    PriorAuthRequirementStatus,
    PriorAuthVerificationStatus,
    RepeatServiceStatus,
    ServiceModality,
    build_clinical_alignment_output,
)
from medishield.domain.faithfulness import parse_medical_date
from medishield.domain.provenance import (
    ClinicalAssertionStatus,
    EvidenceSource,
    EvidenceVerificationStatus,
    FieldProvenance,
)
from medishield.ocr.multilingual import (
    extract_multilingual_clinical_indications,
    extract_multilingual_icd_codes,
    translate_devanagari_numerals,
)


@dataclass
class DischargeSummaryAnalysis:
    """Structured extraction of authoritative discharge summary sections (Spec 01 § 4, ADR-015)."""

    primary_diagnosis_text: str = ""
    primary_icd_codes: list[str] = field(default_factory=list)
    secondary_diagnosis_text: str = ""
    secondary_icd_codes: list[str] = field(default_factory=list)
    procedures_performed_text: str = ""
    procedures_cpt_codes: list[str] = field(default_factory=list)
    hospital_course_text: str = ""
    hospital_course_icd_codes: list[str] = field(default_factory=list)
    hospital_course_cpt_codes: list[str] = field(default_factory=list)
    all_icd_codes: list[str] = field(default_factory=list)
    all_cpt_codes: list[str] = field(default_factory=list)
    has_lab_evaluation: bool = False
    has_imaging_studies: bool = False
    has_inpatient_stay: bool = False
    admission_date: date | None = None
    discharge_date: date | None = None


class DischargeSummaryParser:
    """Authoritative deterministic parser for hospital discharge summaries (ADR-015, Spec 01 § 4)."""

    ICD_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\b([A-TV-Z][0-9]{2}(?:\.?[0-9A-Z]{1,4})?)\b")
    # OCR often glues the label to the code ("CPT59400"), so \b cannot be used; amounts are excluded.
    CPT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"(?<![0-9$.,])([0-9]{5})(?![0-9]|[.,][0-9])")

    @classmethod
    def parse(cls, text: str) -> DischargeSummaryAnalysis:
        if not text:
            return DischargeSummaryAnalysis()

        patterns = {
            "primary": (
                r"(?:FINAL\s*DIAGNOSES?|अंतिम\s*निदान|Diagnóstico\s*final[\s\S]*?)?"
                r"(?:(?:Primary(?:\s+Diagnosis)?|मुख्य\s*निदान|प्राथमिक\s*निदान|निदान|Diagnóstico(?:\s*Principal)?|Diagnostico(?:\s*Principal)?)\s*[:\-]?\s*)"
                r"([^\n]+(?:\n(?!\s*(?:Secondary|द्वितीयक|PROCEDURE|प्रक्रिया|HOSPITAL|अस्पताल|DISCHARGE|छुट्टी|Diagnóstico|Diagnostico|Procedimiento))[^\n]+)?)"
            ),
            "secondary": (
                r"(?:(?:Secondary(?:\s+Diagnosis)?|द्वितीयक\s*निदान|गौण\s*निदान|Diagnóstico\s*secundario|Diagnostico\s*secundario)\s*[:\-]?\s*)"
                r"([^\n]+(?:\n(?!\s*(?:PROCEDURE|प्रक्रिया|HOSPITAL|अस्पताल|DISCHARGE|छुट्टी|Primary|मुख्य|Diagnóstico|Diagnostico))[^\n]+)?)"
            ),
            "procedures": (
                r"(?:(?:PROCEDURES?\s*(?:PERFORMED)?|प्रक्रिया(?:\s*कोड|\s*विवरण)?|प्रक्रियाएं|Procedimientos?(?:\s*realizados?)?)\s*[:\-]?\s*)"
                r"([^\n]+(?:\n(?!\s*(?:HOSPITAL|अस्पताल|DISCHARGE|छुट्टी|FINAL|अंतिम|Primary|मुख्य|Diagnóstico|Diagnostico))[^\n]+)*)"
            ),
            "hospital_course": (
                r"(?:(?:HOSPITAL\s*COURSE|अस्पताल\s*(?:का\s*)?विवरण|इलाज\s*का\s*विवरण|Curso\s*hospitalario|Evolución\s*hospitalaria)\s*[:\-]?\s*)"
                r"([^\n]+(?:\n(?!\s*(?:DISCHARGE|छुट्टी|FINAL|अंतिम|PROCEDURE|प्रक्रिया|Procedimiento))[^\n]+)*)"
            ),
        }
        extracted: dict[str, str] = {}
        for k, pat in patterns.items():
            m = re.search(pat, text, re.IGNORECASE)
            extracted[k] = m.group(1).strip() if m else ""

        def _extract_icd(t: str) -> list[str]:
            if not t:
                return []
            translated_t = translate_devanagari_numerals(t)
            codes = cls.ICD_PATTERN.findall(translated_t)
            # Normalize OCR misreadings of ICD letter 'I' as digit '1' in e.g. ICD-10: 110 -> I10
            for m in re.finditer(r"ICD(?:-10)?\s*[:\-]?\s*1([0-9]{2}(?:\.[0-9]{1,4})?)", translated_t, re.IGNORECASE):
                cand = f"I{m.group(1)}"
                if cand not in codes:
                    codes.append(cand)

            # Multilingual clinical terminology entity linking (ADR-018)
            multi_codes = extract_multilingual_icd_codes(t)
            for mc in multi_codes:
                if mc not in codes:
                    codes.append(mc)

            code_repo = DeterministicClinicalAlignmentEngine._get_default_repo()
            normalize_code = getattr(code_repo, "normalize_diagnosis_code", None)
            normalized_codes = [normalize_code(code) or code for code in codes] if normalize_code else codes
            return list(dict.fromkeys(normalized_codes))

        p_icd = _extract_icd(extracted["primary"])
        s_icd = _extract_icd(extracted["secondary"])
        c_icd = _extract_icd(extracted["hospital_course"])
        all_icd = list(dict.fromkeys(p_icd + s_icd + c_icd))

        proc_cpt = cls.CPT_PATTERN.findall(translate_devanagari_numerals(extracted["procedures"]))
        course_cpt = cls.CPT_PATTERN.findall(translate_devanagari_numerals(extracted["hospital_course"]))
        all_cpt = list(dict.fromkeys(proc_cpt + course_cpt))

        h_course_lower = extracted["hospital_course"].lower()
        has_lab = any(w in h_course_lower for w in ("laboratory", "lab evaluation", "lab workup", "diagnostic workup", "metabolic panel", "प्रयोगशाला", "जांच", "laboratorio"))
        has_imaging = any(w in h_course_lower for w in ("imaging", "radiology", "x-ray", "mri", "ct scan", "ultrasound", "echocardiogram", "diagnostic workup", "रेडियोलॉजी", "एक्स-रे", "imagen"))
        has_stay = any(w in h_course_lower for w in ("admitted", "hospitalization", "inpatient", "discharge", "vital signs", "hospital stay", "दाखिला", "भर्ती", "hospitalización", "ingreso"))

        admit_match = re.search(
            r"(?:Admit\s*Date|Admission\s*Date|दाखिला\s*(?:दिनांक|तिथि)?|भर्ती\s*(?:दिनांक|तिथि)?|Fecha\s*de\s*ingreso)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})",
            text,
            re.IGNORECASE,
        )
        disch_match = re.search(
            r"(?:Discharge\s*Date|छुट्टी\s*(?:दिनांक|तिथि)?|डिस्चार्ज\s*(?:दिनांक|तिथि)?|Fecha\s*de\s*alta)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})",
            text,
            re.IGNORECASE,
        )
        parsed_admit = parse_medical_date(translate_devanagari_numerals(admit_match.group(1))) if admit_match else None
        parsed_disch = parse_medical_date(translate_devanagari_numerals(disch_match.group(1))) if disch_match else None

        return DischargeSummaryAnalysis(
            primary_diagnosis_text=extracted["primary"],
            primary_icd_codes=p_icd,
            secondary_diagnosis_text=extracted["secondary"],
            secondary_icd_codes=s_icd,
            procedures_performed_text=extracted["procedures"],
            procedures_cpt_codes=proc_cpt,
            hospital_course_text=extracted["hospital_course"],
            hospital_course_icd_codes=c_icd,
            hospital_course_cpt_codes=course_cpt,
            all_icd_codes=all_icd,
            all_cpt_codes=all_cpt,
            has_lab_evaluation=has_lab,
            has_imaging_studies=has_imaging,
            has_inpatient_stay=has_stay,
            admission_date=parsed_admit,
            discharge_date=parsed_disch,
        )


@runtime_checkable
class ClinicalGuidelineRepository(Protocol):
    """Protocol for dynamic clinical guideline and indication repository (ADR-015, ADR-016)."""

    def get_guideline(self, service_code: str, service_date: date | None = None) -> dict[str, Any] | None:
        ...

    def get_indication(self, service_code: str) -> dict[str, Any] | None:
        ...

    def register_guideline(self, service_code: str, data: dict[str, Any]) -> None:
        ...

    def register_indication(self, service_code: str, data: dict[str, Any]) -> None:
        ...


class _StandardGuidelineCatalogProxy(dict[str, Any]):
    """Dynamic catalog proxy delegating lookups to authoritative relational storage (ADR-016)."""

    def get(self, key: Any, default: Any = None) -> Any:
        repo = DeterministicClinicalAlignmentEngine._get_default_repo()
        res = repo.get_guideline(str(key))
        return res if res is not None else default

    def __getitem__(self, key: Any) -> Any:
        res = self.get(key)
        if res is None:
            raise KeyError(key)
        return res

    def __contains__(self, key: Any) -> bool:
        return self.get(key) is not None

    def __bool__(self) -> bool:
        return True


class _DiagnosisIndicationMapProxy(dict[str, Any]):
    """Dynamic diagnosis indication proxy delegating lookups to authoritative relational storage (ADR-016)."""

    def get(self, key: Any, default: Any = None) -> Any:
        repo = DeterministicClinicalAlignmentEngine._get_default_repo()
        res = repo.get_indication(str(key))
        return res if res is not None else default

    def __getitem__(self, key: Any) -> Any:
        res = self.get(key)
        if res is None:
            raise KeyError(key)
        return res

    def __contains__(self, key: Any) -> bool:
        return self.get(key) is not None

    def __bool__(self) -> bool:
        return True


class DeterministicClinicalAlignmentEngine:
    """
    Authoritative, deterministic clinical verification engine.
    Cross-verifies medical procedures, laboratory diagnostics, imaging, and bed-day
    units against verified clinical evidence and coverage guidelines.
    """

    _default_repo_instance: ClassVar[ClinicalGuidelineRepository | None] = None

    # Dynamic proxies to relational catalog (ADR-016)
    STANDARD_GUIDELINE_CATALOG: ClassVar[_StandardGuidelineCatalogProxy] = _StandardGuidelineCatalogProxy()
    DIAGNOSIS_INDICATION_MAP: ClassVar[_DiagnosisIndicationMapProxy] = _DiagnosisIndicationMapProxy()

    @classmethod
    def parse_discharge_summary(cls, text: str) -> DischargeSummaryAnalysis:
        """Parses structured sections (Primary, Secondary, Hospital Course, Procedures) from discharge summary."""
        return DischargeSummaryParser.parse(text)

    @classmethod
    def classify_modality(cls, service_code: str) -> ServiceModality:
        """Determines the clinical service modality from CPT, HCPCS, or Revenue code (Spec 01 § 4, ADR-021)."""
        code = service_code.strip().upper()
        clean_code = code.replace("REV_", "").strip()

        # UB-04 Revenue Code ranges (3-4 digits, < 1000)
        if clean_code.isdigit() and len(clean_code) in (3, 4) and int(clean_code) < 1000:
            rev_int = int(clean_code)
            if 100 <= rev_int <= 219:
                return ServiceModality.ROOM_AND_BOARD
            if 250 <= rev_int <= 259:
                return ServiceModality.PHARMACEUTICAL
            if 300 <= rev_int <= 319:
                return ServiceModality.LABORATORY_TEST
            if (320 <= rev_int <= 359) or (400 <= rev_int <= 409):
                return ServiceModality.DIAGNOSTIC_IMAGING
            if (360 <= rev_int <= 379) or rev_int in (710, 720):
                return ServiceModality.SURGICAL_PROCEDURE
            if (450 <= rev_int <= 459) or (510 <= rev_int <= 529):
                return ServiceModality.MEDICAL_VISIT_OR_EVAL

        if code.startswith("REV"):
            return ServiceModality.ROOM_AND_BOARD

        # Numeric CPT ranges (5 digits)
        if code.isdigit():
            cpt_num = int(code)
            if 10000 <= cpt_num <= 69999:
                return ServiceModality.SURGICAL_PROCEDURE
            if 70000 <= cpt_num <= 79999:
                return ServiceModality.DIAGNOSTIC_IMAGING
            if 80000 <= cpt_num <= 89999:
                return ServiceModality.LABORATORY_TEST
            if (99201 <= cpt_num <= 99499) or cpt_num == 93000:
                return ServiceModality.MEDICAL_VISIT_OR_EVAL

        # Standard institutional text revenue tokens (ADR-022)
        if clean_code in ("PHARM", "RX", "MED", "DRUGS", "MEDS", "PHARMACY"):
            return ServiceModality.PHARMACEUTICAL
        if clean_code in ("ROOM", "BED", "R&B", "BOARD"):
            return ServiceModality.ROOM_AND_BOARD
        if clean_code in ("DIAG", "LAB", "DIAGNOSTICS") or (clean_code.isdigit() and int(clean_code) in (800, 801, 802, 803, 804)):
            return ServiceModality.LABORATORY_TEST

        if code.startswith(("J", "Q")) or len(code) == 11:
            return ServiceModality.PHARMACEUTICAL

        return ServiceModality.OTHER_SERVICE

    @classmethod
    def _get_default_repo(cls) -> ClinicalGuidelineRepository:
        if cls._default_repo_instance is None:
            cls._default_repo_instance = DefaultClinicalGuidelineRepository()
        return cls._default_repo_instance

    @classmethod
    def get_guideline_for_code(
        cls,
        service_code: str,
        service_date: date | None = None,
        spans_override: list[FieldProvenance] | None = None,
        guideline_repo: ClinicalGuidelineRepository | None = None,
        payer_product: str | None = None,
        jurisdiction: str | None = None,
    ) -> GuidelineCitation | None:
        """Retrieves and populates a GuidelineCitation for an authoritative medical code."""
        code_key = service_code.strip().replace("REV_", "")
        repo = guideline_repo or cls._get_default_repo()
        template = repo.get_guideline(code_key, service_date=service_date) if hasattr(repo, "get_guideline") else cls.STANDARD_GUIDELINE_CATALOG.get(code_key)
        if not template:
            return None

        eff_date = template["effective_date"]
        exp_date = template.get("expiration_date")
        if service_date and service_date < eff_date:
            return None
        if service_date and exp_date and service_date > exp_date:
            return None

        source_verified = bool(template.get("source_verified"))
        source_reference = template.get("source_reference")
        source_chunk_id = template.get("source_chunk_id")
        configured_payer = template.get("payer_product")
        configured_jurisdiction = template.get("jurisdiction")
        criteria = []
        for c in template["mandatory_criteria"]:
            crit_id = c["criterion_id"]
            criteria.append(
                MandatoryGuidelineCriterion(
                    criterion_id=crit_id,
                    description=c["description"],
                    requirement_reference_id=crit_id,
                    requirement_source_reference=c.get("source_reference"),
                    requirement_source_chunk_id=c.get("source_chunk_id"),
                    requirement_source_verified=bool(c.get("source_verified")),
                    is_mandatory=True,
                    evaluation_outcome=CriterionEvaluationOutcome.UNASSESSED_EVIDENCE_UNAVAILABLE,
                    supporting_spans=[],
                    rationale="Patient-specific criterion evidence has not been independently verified.",
                )
            )

        return GuidelineCitation(
            guideline_id=template["guideline_id"],
            guideline_version=template["guideline_version"],
            guideline_title=template["guideline_title"],
            jurisdiction=configured_jurisdiction,
            payer_product=configured_payer,
            source_reference=source_reference,
            source_chunk_id=source_chunk_id,
            source_verified=source_verified,
            applicable_to_claim=bool(
                payer_product
                and jurisdiction
                and configured_payer
                and configured_jurisdiction
                and payer_product.casefold() == configured_payer.casefold()
                and jurisdiction.casefold() == configured_jurisdiction.casefold()
            ),
            effective_date=eff_date,
            expiration_date=exp_date,
            criteria_evaluations=criteria,
        )

    @classmethod
    def evaluate_guideline_criteria(
        cls,
        guideline: dict[str, Any],
        patient_evidence: list[FieldProvenance],
        validated_diagnosis_codes: list[str],
    ) -> list[MandatoryGuidelineCriterion]:
        """Evaluate database-configured criterion patterns against verified patient OCR evidence."""
        evaluated: list[MandatoryGuidelineCriterion] = []
        for definition in guideline.get("mandatory_criteria", []):
            parameters = definition.get("evaluation_parameters") or {}
            requirement_type = definition.get("requirement_type")
            relevant: list[FieldProvenance] = []
            outcome = CriterionEvaluationOutcome.UNASSESSED_EVIDENCE_UNAVAILABLE
            interpretation: str | None = None
            rationale = "Required patient evidence is unavailable or insufficient."

            if requirement_type == "DIAGNOSIS_CODE_PREFIX":
                prefixes = tuple(str(p).replace(".", "").upper() for p in parameters.get("valid_prefixes", ()))
                matching_codes = [
                    code
                    for code in validated_diagnosis_codes
                    if str(code).replace(".", "").upper().startswith(prefixes)
                ]
                relevant = [
                    item
                    for item in patient_evidence
                    if any(code.casefold() in item.text_content.casefold() for code in matching_codes)
                ]
                if matching_codes and relevant:
                    outcome = CriterionEvaluationOutcome.SATISFIED
                    interpretation = ", ".join(matching_codes)
                    rationale = "Catalog-validated diagnosis code is present in verified patient OCR evidence."
            elif requirement_type == "EVIDENCE_REGEX":
                pattern = parameters.get("pattern")
                if isinstance(pattern, str) and pattern:
                    matches = [item for item in patient_evidence if re.search(pattern, item.text_content, re.IGNORECASE)]
                    negative_patterns = parameters.get("negation_patterns", [])
                    uncertain_patterns = parameters.get("uncertainty_patterns", [])
                    positive = [
                        item for item in matches
                        if not any(re.search(p, item.text_content, re.IGNORECASE) for p in negative_patterns)
                        and not any(re.search(p, item.text_content, re.IGNORECASE) for p in uncertain_patterns)
                    ]
                    negative = [
                        item for item in matches
                        if any(re.search(p, item.text_content, re.IGNORECASE) for p in negative_patterns)
                    ]
                    uncertain = [
                        item for item in matches
                        if any(re.search(p, item.text_content, re.IGNORECASE) for p in uncertain_patterns)
                    ]
                    if positive and negative:
                        relevant = positive + negative
                        outcome = CriterionEvaluationOutcome.CONTRADICTORY
                        rationale = "Verified patient evidence contains both affirmative and negated findings."
                    elif negative:
                        relevant = negative
                        outcome = CriterionEvaluationOutcome.NOT_SATISFIED
                        rationale = "Verified patient evidence explicitly negates the required finding."
                    elif positive:
                        relevant = positive
                        outcome = CriterionEvaluationOutcome.SATISFIED
                        rationale = "Verified patient evidence matches the configured criterion pattern."
                    elif uncertain:
                        relevant = uncertain
                        rationale = "Evidence is explicitly uncertain and cannot establish the criterion."

                    if relevant:
                        match = re.search(pattern, relevant[0].text_content, re.IGNORECASE)
                        if match:
                            interpretation = match.group(0)

            evidence_relevance_verified = bool(relevant) and outcome in (
                CriterionEvaluationOutcome.SATISFIED,
                CriterionEvaluationOutcome.NOT_SATISFIED,
                CriterionEvaluationOutcome.CONTRADICTORY,
            )
            annotated = [
                item.model_copy(update={
                    "normalized_interpretation": interpretation,
                    "assertion_status": (
                        ClinicalAssertionStatus.AFFIRMED
                        if outcome == CriterionEvaluationOutcome.SATISFIED
                        else ClinicalAssertionStatus.NEGATED
                        if outcome == CriterionEvaluationOutcome.NOT_SATISFIED
                        else ClinicalAssertionStatus.CONTRADICTORY
                        if outcome == CriterionEvaluationOutcome.CONTRADICTORY
                        else ClinicalAssertionStatus.UNCERTAIN
                    ),
                    "evidence_relevance_verified": evidence_relevance_verified,
                })
                for item in relevant
            ]
            evaluated.append(MandatoryGuidelineCriterion(
                criterion_id=str(definition["criterion_id"]),
                description=str(definition["description"]),
                requirement_reference_id=str(definition["criterion_id"]),
                requirement_source_reference=definition.get("source_reference"),
                requirement_source_chunk_id=definition.get("source_chunk_id"),
                requirement_source_verified=bool(definition.get("source_verified")),
                is_mandatory=bool(definition.get("is_mandatory", True)),
                evaluation_outcome=outcome,
                supporting_spans=annotated,
                evidence_interpretation=interpretation,
                evidence_relevance_verified=evidence_relevance_verified,
                rationale=rationale,
            ))
        return evaluated

    @classmethod
    def reconcile_bed_days(
        cls,
        billed_bed_days: float,
        admission_datetime: datetime | None = None,
        discharge_datetime: datetime | None = None,
        admission_date: date | None = None,
        discharge_date: date | None = None,
        billing_rule: BedDayBillingRuleContract | None = None,
        claim_facility_type: str | None = None,
        claim_type: str | None = None,
    ) -> BedDayReconciliation:
        """
        Reconciles billed facility bed-day units against documented encounter stay.
        Enforces approved billing rules, midnight census or hourly formulas, and fails
        closed on unapproved rules, missing dates, or ambiguous date-only durations.
        """
        has_admission = admission_datetime is not None or admission_date is not None
        has_discharge = discharge_datetime is not None or discharge_date is not None

        # 1. Missing admission or discharge evidence strictly fails closed to INDETERMINATE
        if not has_admission or not has_discharge:
            return BedDayReconciliation(
                billing_rule=billing_rule,
                claim_facility_type=claim_facility_type,
                claim_type=claim_type,
                billed_bed_days=billed_bed_days,
                is_reconciled=False,
                uncertainty_flag=True,
                reconciliation_verdict=AgentVerdict.INDETERMINATE,
                remarks="Missing admission or discharge evidence in submitted hospital records.",
            )

        # 2. Rule presence and formal approval
        if billing_rule is None or not billing_rule.is_approved:
            return BedDayReconciliation(
                billing_rule=billing_rule,
                claim_facility_type=claim_facility_type,
                claim_type=claim_type,
                admission_datetime=admission_datetime,
                discharge_datetime=discharge_datetime,
                admission_date=admission_date,
                discharge_date=discharge_date,
                billed_bed_days=billed_bed_days,
                is_reconciled=False,
                uncertainty_flag=True,
                reconciliation_verdict=AgentVerdict.INDETERMINATE,
                remarks="Governing facility BedDayBillingRuleContract is missing or unapproved.",
            )

        # 3. Rule applicability verification (facility type and claim type)
        if claim_facility_type and billing_rule.facility_type != claim_facility_type:
            return BedDayReconciliation(
                billing_rule=billing_rule,
                claim_facility_type=claim_facility_type,
                claim_type=claim_type,
                admission_datetime=admission_datetime,
                discharge_datetime=discharge_datetime,
                admission_date=admission_date,
                discharge_date=discharge_date,
                billed_bed_days=billed_bed_days,
                is_reconciled=False,
                uncertainty_flag=True,
                reconciliation_verdict=AgentVerdict.INDETERMINATE,
                remarks=f"Billing rule facility '{billing_rule.facility_type}' does not match claim facility '{claim_facility_type}'.",
            )

        if claim_type and billing_rule.claim_type != claim_type:
            return BedDayReconciliation(
                billing_rule=billing_rule,
                claim_facility_type=claim_facility_type,
                claim_type=claim_type,
                admission_datetime=admission_datetime,
                discharge_datetime=discharge_datetime,
                admission_date=admission_date,
                discharge_date=discharge_date,
                billed_bed_days=billed_bed_days,
                is_reconciled=False,
                uncertainty_flag=True,
                reconciliation_verdict=AgentVerdict.INDETERMINATE,
                remarks=f"Billing rule claim_type '{billing_rule.claim_type}' does not match claim type '{claim_type}'.",
            )

        # Effective date range verification
        service_d = discharge_date or (discharge_datetime.date() if discharge_datetime else None)
        if service_d:
            if service_d < billing_rule.effective_date:
                return BedDayReconciliation(
                    billing_rule=billing_rule,
                    claim_facility_type=claim_facility_type,
                    claim_type=claim_type,
                    admission_datetime=admission_datetime,
                    discharge_datetime=discharge_datetime,
                    admission_date=admission_date,
                    discharge_date=discharge_date,
                    billed_bed_days=billed_bed_days,
                    is_reconciled=False,
                    uncertainty_flag=True,
                    reconciliation_verdict=AgentVerdict.INDETERMINATE,
                    remarks=f"Billing rule is not effective until {billing_rule.effective_date}.",
                )
            if billing_rule.expiration_date and service_d > billing_rule.expiration_date:
                return BedDayReconciliation(
                    billing_rule=billing_rule,
                    claim_facility_type=claim_facility_type,
                    claim_type=claim_type,
                    admission_datetime=admission_datetime,
                    discharge_datetime=discharge_datetime,
                    admission_date=admission_date,
                    discharge_date=discharge_date,
                    billed_bed_days=billed_bed_days,
                    is_reconciled=False,
                    uncertainty_flag=True,
                    reconciliation_verdict=AgentVerdict.INDETERMINATE,
                    remarks=f"Billing rule expired on {billing_rule.expiration_date}.",
                )

        a_d = admission_date or (admission_datetime.date() if admission_datetime else None)
        d_d = discharge_date or (discharge_datetime.date() if discharge_datetime else None)
        calendar_days = (d_d - a_d).days if (a_d and d_d) else None

        timestamps_complete = admission_datetime is not None and discharge_datetime is not None

        # 4. Date-Only Stay Reconciliation (ADR-022, EVAL-CLIN-14)
        if not timestamps_complete:
            days_span = calendar_days if calendar_days is not None else 1
            min_u = float(max(0, days_span))
            max_u = float(max(1, days_span + 1))

            if billed_bed_days == min_u:
                return BedDayReconciliation(
                    billing_rule=billing_rule,
                    claim_facility_type=claim_facility_type,
                    claim_type=claim_type,
                    admission_date=a_d,
                    discharge_date=d_d,
                    timestamps_complete=False,
                    calendar_days_stayed=calendar_days,
                    expected_billable_units_min=min_u,
                    expected_billable_units_max=min_u,
                    billed_bed_days=billed_bed_days,
                    is_reconciled=True,
                    discrepancy_units=0.0,
                    uncertainty_flag=False,
                    reconciliation_verdict=AgentVerdict.PASS,
                    remarks=f"Date-only stay reconciled affirmatively: billed {billed_bed_days} matches expected midnight census {min_u}.",
                )
            elif billed_bed_days == max_u and billing_rule.convention == FacilityBillingConvention.CALENDAR_DAY_SPAN:
                return BedDayReconciliation(
                    billing_rule=billing_rule,
                    claim_facility_type=claim_facility_type,
                    claim_type=claim_type,
                    admission_date=a_d,
                    discharge_date=d_d,
                    timestamps_complete=False,
                    calendar_days_stayed=calendar_days,
                    expected_billable_units_min=max_u,
                    expected_billable_units_max=max_u,
                    billed_bed_days=billed_bed_days,
                    is_reconciled=True,
                    discrepancy_units=0.0,
                    uncertainty_flag=False,
                    reconciliation_verdict=AgentVerdict.PASS,
                    remarks=f"Date-only stay reconciled affirmatively: billed {billed_bed_days} matches calendar day span {max_u}.",
                )
            elif billed_bed_days == max_u and billing_rule.convention == FacilityBillingConvention.MIDNIGHT_CENSUS:
                # Ambiguous boundary: facility billed discharge day as full bed day under midnight census without hourly timestamps (EVAL-CLIN-14)
                return BedDayReconciliation(
                    billing_rule=billing_rule,
                    claim_facility_type=claim_facility_type,
                    claim_type=claim_type,
                    admission_date=a_d,
                    discharge_date=d_d,
                    timestamps_complete=False,
                    calendar_days_stayed=calendar_days,
                    expected_billable_units_min=min_u,
                    expected_billable_units_max=max_u,
                    billed_bed_days=billed_bed_days,
                    is_reconciled=False,
                    uncertainty_flag=True,
                    reconciliation_verdict=AgentVerdict.INDETERMINATE,
                    remarks=f"Date-only stay without timestamps produces ambiguous expected unit range [{min_u}, {max_u}].",
                )
            else:
                discrepancy = billed_bed_days - min_u
                return BedDayReconciliation(
                    billing_rule=billing_rule,
                    claim_facility_type=claim_facility_type,
                    claim_type=claim_type,
                    admission_date=a_d,
                    discharge_date=d_d,
                    timestamps_complete=False,
                    calendar_days_stayed=calendar_days,
                    expected_billable_units_min=min_u,
                    expected_billable_units_max=max_u,
                    billed_bed_days=billed_bed_days,
                    is_reconciled=False,
                    discrepancy_units=discrepancy,
                    uncertainty_flag=True,
                    reconciliation_verdict=AgentVerdict.CONFLICT,
                    remarks=f"Billed bed days ({billed_bed_days}) exceeds documented stay window [{min_u}, {max_u}].",
                )

        # 5. Calculate Expected Units from Complete Timestamps
        assert admission_datetime is not None
        assert discharge_datetime is not None

        if billing_rule.convention == FacilityBillingConvention.MIDNIGHT_CENSUS:
            midnights = (discharge_datetime.date() - admission_datetime.date()).days
            expected_units = float(max(0, midnights))
        elif billing_rule.convention == FacilityBillingConvention.CALENDAR_DAY_SPAN:
            expected_units = float((calendar_days or 0) + 1)
        elif billing_rule.convention == FacilityBillingConvention.SAME_DAY_OBSERVATION:
            expected_units = 1.0 if admission_datetime.date() == discharge_datetime.date() else float(calendar_days or 1)
        elif billing_rule.convention == FacilityBillingConvention.FRACTIONAL_HOURLY:
            hours = (discharge_datetime - admission_datetime).total_seconds() / 3600.0
            expected_units = round(hours / 24.0, 2)
        else:
            expected_units = float(calendar_days or 1)

        # 6. Reconcile Units
        if billed_bed_days == expected_units:
            return BedDayReconciliation(
                billing_rule=billing_rule,
                claim_facility_type=claim_facility_type,
                claim_type=claim_type,
                admission_datetime=admission_datetime,
                discharge_datetime=discharge_datetime,
                admission_date=a_d,
                discharge_date=d_d,
                timestamps_complete=True,
                calendar_days_stayed=calendar_days,
                expected_billable_units_min=expected_units,
                expected_billable_units_max=expected_units,
                billed_bed_days=billed_bed_days,
                is_reconciled=True,
                discrepancy_units=0.0,
                uncertainty_flag=False,
                reconciliation_verdict=AgentVerdict.PASS,
                remarks=f"Bed days reconciled unambiguously: billed {billed_bed_days} matches expected {expected_units}.",
            )

        # Discrepancy detected (e.g. EVAL-CLIN-16)
        discrepancy = billed_bed_days - expected_units
        return BedDayReconciliation(
            billing_rule=billing_rule,
            claim_facility_type=claim_facility_type,
            claim_type=claim_type,
            admission_datetime=admission_datetime,
            discharge_datetime=discharge_datetime,
            admission_date=a_d,
            discharge_date=d_d,
            timestamps_complete=True,
            calendar_days_stayed=calendar_days,
            expected_billable_units_min=expected_units,
            expected_billable_units_max=expected_units,
            billed_bed_days=billed_bed_days,
            is_reconciled=False,
            discrepancy_units=discrepancy,
            uncertainty_flag=False,
            reconciliation_verdict=AgentVerdict.CONFLICT,
            remarks=f"Bed-day discrepancy: billed {billed_bed_days} exceeds expected {expected_units} by {discrepancy} units.",
        )

    @classmethod
    def evaluate_line_item(
        cls,
        line_number: int,
        service_code: str,
        billed_units: float,
        billed_amount: float,
        service_date: date | None = None,
        modality: ServiceModality | None = None,
        performance_status: PerformanceEvidenceStatus | None = None,
        indication_status: ClinicalIndicationStatus | None = None,
        necessity_status: MedicalNecessityStatus | None = None,
        guideline_citation: GuidelineCitation | None = None,
        prior_auth_requirement: PriorAuthRequirementStatus | None = None,
        prior_auth_verification: PriorAuthVerificationStatus | None = None,
        repeat_service_status: RepeatServiceStatus | None = None,
        supporting_spans: list[FieldProvenance] | None = None,
        clinical_finding_summary: str = "",
        discrepancy_description: str | None = None,
        # Contextual evaluation inputs
        encounter_admission_datetime: datetime | None = None,
        encounter_discharge_datetime: datetime | None = None,
        service_timestamp: datetime | None = None,
        clinical_notes_text: str | None = None,
        clinical_notes_verified: bool = False,
        encounter_dates_verified: bool = False,
        diagnosis_codes: list[str] | None = None,
        guideline_repo: ClinicalGuidelineRepository | None = None,
        payer_product: str | None = None,
        jurisdiction: str | None = None,
        performance_evidence: list[FieldProvenance] | None = None,
        indication_evidence: list[FieldProvenance] | None = None,
        criterion_evaluations: list[MandatoryGuidelineCriterion] | None = None,
        indication_conflict: bool = False,
        # Authoritative discharge summary section inputs (Spec 01 § 4, ADR-015)
        primary_icd10_codes: list[str] | None = None,
        secondary_icd10_codes: list[str] | None = None,
        primary_diagnosis_text: str | None = None,
        secondary_diagnosis_text: str | None = None,
        hospital_course_text: str | None = None,
    ) -> LineClinicalEvidence:
        """
        Deterministically evaluates clinical alignment for an individual claim line.
        Resolves modality, date boundaries, performance evidence, indication, necessity,
        prior authorization, and repeat service status.
        """
        repo = guideline_repo or cls._get_default_repo()
        resolved_modality = modality or cls.classify_modality(service_code)
        spans = list(supporting_spans or [])
        diag_rule = (
            repo.get_indication(service_code)
            if hasattr(repo, "get_indication")
            else cls.DIAGNOSIS_INDICATION_MAP.get(service_code.strip().replace("REV_", ""))
        )
        assertion_patterns = (diag_rule or {}).get("assertion_patterns") or {}

        # 1. Date Boundary & Post-Discharge Non-Performance Check (EVAL-CLIN-10)
        is_post_discharge = False
        resolved_necessity: MedicalNecessityStatus
        if encounter_dates_verified and encounter_discharge_datetime and (
            (service_timestamp and service_timestamp > encounter_discharge_datetime)
            or (service_date and service_date > encounter_discharge_datetime.date())
        ):
            is_post_discharge = True

        if is_post_discharge:
            resolved_performance = PerformanceEvidenceStatus.CONFIRMED_NON_PERFORMANCE
            resolved_indication = indication_status or ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION
            resolved_necessity = MedicalNecessityStatus.NOT_MET
            resolved_prior_auth_req = prior_auth_requirement or PriorAuthRequirementStatus.NOT_REQUIRED
            resolved_prior_auth_ver = prior_auth_verification or PriorAuthVerificationStatus.NOT_APPLICABLE
            resolved_repeat = repeat_service_status or RepeatServiceStatus.NOT_A_REPEATED_SERVICE
            return LineClinicalEvidence(
                line_number=line_number,
                service_code=service_code,
                modality=resolved_modality,
                billed_units=billed_units,
                billed_amount=billed_amount,
                service_date=service_date,
                performance_status=resolved_performance,
                indication_status=resolved_indication,
                necessity_status=resolved_necessity,
                guideline_citation=None,
                prior_auth_requirement=resolved_prior_auth_req,
                prior_auth_verification=resolved_prior_auth_ver,
                repeat_service_status=resolved_repeat,
                supporting_spans=[],
                line_verdict=AgentVerdict.CONFLICT,
                clinical_finding_summary=clinical_finding_summary or "Service ordered/performed after verified hospital discharge.",
                discrepancy_flag=True,
                discrepancy_description=discrepancy_description or "Confirmed non-performance: service occurs post-discharge.",
            )

        # 2. Clinical Explicit Contradiction Check (EVAL-CLIN-09)
        text_lower = (clinical_notes_text or "").lower()
        contradiction_patterns = assertion_patterns.get("contradicted_performance_patterns", [])
        verified_contradiction = clinical_notes_verified and any(
            re.search(pattern, text_lower, re.IGNORECASE)
            for pattern in contradiction_patterns
        )
        verified_conflict_spans = [
            span for span in spans
            if span.evidence_source == EvidenceSource.OCR
            and span.verification_status == EvidenceVerificationStatus.VERIFIED
            and span.evidence_relevance_verified
            and span.assertion_status in (ClinicalAssertionStatus.NEGATED, ClinicalAssertionStatus.CONTRADICTORY)
        ]
        if verified_contradiction and verified_conflict_spans:
            resolved_performance = PerformanceEvidenceStatus.EXPLICITLY_CONTRADICTED
            resolved_indication = indication_status or ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION
            resolved_necessity = MedicalNecessityStatus.NOT_MET
            resolved_prior_auth_req = prior_auth_requirement or PriorAuthRequirementStatus.NOT_REQUIRED
            resolved_prior_auth_ver = prior_auth_verification or PriorAuthVerificationStatus.NOT_APPLICABLE
            resolved_repeat = repeat_service_status or RepeatServiceStatus.NOT_A_REPEATED_SERVICE
            return LineClinicalEvidence(
                line_number=line_number,
                service_code=service_code,
                modality=resolved_modality,
                billed_units=billed_units,
                billed_amount=billed_amount,
                service_date=service_date,
                performance_status=resolved_performance,
                indication_status=resolved_indication,
                necessity_status=resolved_necessity,
                guideline_citation=None,
                prior_auth_requirement=resolved_prior_auth_req,
                prior_auth_verification=resolved_prior_auth_ver,
                repeat_service_status=resolved_repeat,
                supporting_spans=verified_conflict_spans,
                performance_evidence=verified_conflict_spans,
                indication_evidence=verified_conflict_spans,
                line_verdict=AgentVerdict.CONFLICT,
                clinical_finding_summary=clinical_finding_summary or "Clinical documentation explicitly contradicts performance.",
                discrepancy_flag=True,
                discrepancy_description=discrepancy_description or "Documented refusal or procedure cancellation contradicts billed line.",
            )

        # 3. Prior Authorization Requirement Resolution
        template = (
            repo.get_guideline(service_code, service_date=service_date)
            if hasattr(repo, "get_guideline")
            else cls.STANDARD_GUIDELINE_CATALOG.get(service_code.strip().replace("REV_", ""))
        )
        requires_pa_catalog = template.get("requires_prior_auth", False) if template else False

        if prior_auth_requirement is not None:
            resolved_prior_auth_req = prior_auth_requirement
        else:
            resolved_prior_auth_req = (
                PriorAuthRequirementStatus.REQUIRED
                if requires_pa_catalog
                else PriorAuthRequirementStatus.NOT_REQUIRED
            )

        if prior_auth_verification is not None:
            resolved_prior_auth_ver = prior_auth_verification
        elif resolved_prior_auth_req == PriorAuthRequirementStatus.REQUIRED:
            resolved_prior_auth_ver = PriorAuthVerificationStatus.MISSING_OR_UNVERIFIED
        elif resolved_prior_auth_req == PriorAuthRequirementStatus.NOT_REQUIRED:
            resolved_prior_auth_ver = PriorAuthVerificationStatus.NOT_APPLICABLE
        else:
            resolved_prior_auth_ver = PriorAuthVerificationStatus.MISSING_OR_UNVERIFIED

        # 4. Repeat Service Status Resolution
        resolved_repeat = repeat_service_status or RepeatServiceStatus.NOT_A_REPEATED_SERVICE

        # 5. Performance Evidence Status Resolution
        resolved_performance = performance_status or PerformanceEvidenceStatus.NOT_DOCUMENTED

        # 6. Clinical Indication Status Resolution
        resolved_indication = indication_status or ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION
        if diag_rule and indication_status is None:
            proc_name = diag_rule["name"]
            all_eval_diags: list[str] = []
            unvalidated_diags: list[str] = []
            code_validator = getattr(repo, "is_valid_diagnosis_code", None)
            documented_diags = list(dict.fromkeys(
                str(code)
                for code in (diagnosis_codes or []) + (primary_icd10_codes or []) + (secondary_icd10_codes or [])
                if code
            ))
            if code_validator:
                for code in documented_diags:
                    (all_eval_diags if code_validator(code, service_date) else unvalidated_diags).append(code)
            else:
                unvalidated_diags.extend(documented_diags)

            has_matching_diag = False
            has_contraindicated_diag = False
            if all_eval_diags:
                clean_diag = [str(d).upper().replace(".", "").strip() for d in all_eval_diags if d]
                valid_prefixes = tuple(p.replace(".", "").upper() for p in diag_rule.get("valid_icd_prefixes", ()))
                contraindicated_prefixes = tuple(
                    p.replace(".", "").upper()
                    for p in diag_rule.get("contraindicated_incompatible_prefixes", ())
                )
                has_matching_diag = any(any(d.startswith(p) for p in valid_prefixes) for d in clean_diag)
                has_contraindicated_diag = any(
                    d.startswith(prefix)
                    for d in clean_diag
                    for prefix in contraindicated_prefixes
                )

            diagnosis_text = f"{primary_diagnosis_text or ''} {secondary_diagnosis_text or ''}"
            if not has_matching_diag:
                for ind in extract_multilingual_clinical_indications(
                    diagnosis_text,
                    enable_llm_fallback=False,
                ):
                    candidate_code = ind.get("icd10_code")
                    if not candidate_code or not code_validator or not code_validator(candidate_code, service_date):
                        continue
                    ind_code = candidate_code.upper().replace(".", "").strip()
                    has_matching_diag = any(
                        ind_code.startswith(prefix.replace(".", "").upper())
                        for prefix in diag_rule.get("valid_icd_prefixes", ())
                    )
                    if has_matching_diag:
                        break

            if has_contraindicated_diag:
                resolved_indication = ClinicalIndicationStatus.CLINICALLY_CONTRAINDICATED
                contraindicated_codes = [
                    code for code in all_eval_diags
                    if any(
                        code.upper().replace(".", "").startswith(prefix)
                        for prefix in contraindicated_prefixes
                    )
                ]
                discrepancy_description = (
                    f"Configured diagnosis-procedure contraindication: validated diagnosis code(s) "
                    f"{', '.join(contraindicated_codes)} are contraindicated for CPT {service_code} "
                    f"({proc_name}) by the active diagnosis-indication catalog."
                )
                clinical_finding_summary = discrepancy_description
            elif has_matching_diag:
                resolved_indication = ClinicalIndicationStatus.EXPLICITLY_INDICATED
                discrepancy_description = None
            elif unvalidated_diags and not all_eval_diags:
                # Documented but unverifiable codes are uncertainty, not evidence of misalignment.
                resolved_indication = ClinicalIndicationStatus.POTENTIALLY_RELATED
                discrepancy_description = (
                    f"Diagnosis validation unavailable: documented code(s) {', '.join(unvalidated_diags)} "
                    f"could not be validated against the ICD-10-CM catalog, so the indication for CPT "
                    f"{service_code} ({proc_name}) cannot be confirmed or refuted."
                )
                clinical_finding_summary = discrepancy_description
            else:
                resolved_indication = ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION
                diag_str = ", ".join(all_eval_diags) if all_eval_diags else "None documented"
                discrepancy_description = (
                    f"Diagnosis-procedure clinical misalignment: Billed procedure CPT {service_code} "
                    f"({proc_name}) is unindicated for documented diagnosis code(s) {diag_str}; "
                    f"no supporting clinical indication for {diag_rule['anatomy']} exists in clinical documentation."
                )
                clinical_finding_summary = discrepancy_description

        # 7. Guideline Citation & Medical Necessity Resolution
        resolved_guideline = guideline_citation
        if resolved_guideline is None and necessity_status is None and resolved_performance in (
            PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
            PerformanceEvidenceStatus.RESULTED,
        ):
            resolved_guideline = cls.get_guideline_for_code(
                service_code=service_code,
                service_date=service_date,
                spans_override=spans,
                guideline_repo=guideline_repo,
                payer_product=payer_product,
                jurisdiction=jurisdiction,
            )
        if resolved_guideline is not None and criterion_evaluations is not None:
            resolved_guideline = resolved_guideline.model_copy(
                update={"criteria_evaluations": criterion_evaluations}
            )

        if necessity_status is not None:
            resolved_necessity = necessity_status
        elif resolved_guideline is None:
            # Ancillary facility revenue codes (Room & Board, Inpatient Pharmacy, Operating Room) substantiated by
            # a verified inpatient hospital stay or surgery do not require a standalone outpatient CPT guideline (ADR-021).
            if (
                resolved_modality in (
                    ServiceModality.ROOM_AND_BOARD,
                    ServiceModality.PHARMACEUTICAL,
                    ServiceModality.SURGICAL_PROCEDURE,
                    ServiceModality.LABORATORY_TEST,
                    ServiceModality.DIAGNOSTIC_IMAGING,
                )
                and resolved_performance in (
                    PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
                    PerformanceEvidenceStatus.RESULTED,
                )
                and resolved_indication == ClinicalIndicationStatus.EXPLICITLY_INDICATED
            ):
                resolved_necessity = MedicalNecessityStatus.MET
            else:
                resolved_necessity = MedicalNecessityStatus.GUIDELINE_UNAVAILABLE
        elif resolved_guideline.all_mandatory_criteria_satisfied:
            resolved_necessity = MedicalNecessityStatus.MET
        elif any(
            criterion.is_mandatory
            and criterion.evaluation_outcome == CriterionEvaluationOutcome.NOT_SATISFIED
            for criterion in resolved_guideline.criteria_evaluations
        ):
            resolved_necessity = MedicalNecessityStatus.NOT_MET
        else:
            resolved_necessity = MedicalNecessityStatus.CRITERIA_INCOMPLETE

        # 8. Deterministic Line Verdict Derivation
        # CONFLICT cases
        if (
            indication_conflict
            or resolved_performance in (
                PerformanceEvidenceStatus.EXPLICITLY_CONTRADICTED,
                PerformanceEvidenceStatus.CONFIRMED_NON_PERFORMANCE,
            )
            or (
                resolved_guideline is not None
                and any(
                    criterion.is_mandatory
                    and criterion.evaluation_outcome == CriterionEvaluationOutcome.CONTRADICTORY
                    for criterion in resolved_guideline.criteria_evaluations
                )
            )
        ):
            line_verdict = AgentVerdict.CONFLICT
        # FAIL cases (e.g. EVAL-CLIN-05, EVAL-CLIN-06)
        elif (
            resolved_prior_auth_ver == PriorAuthVerificationStatus.VERIFIED_DENIED
            or resolved_necessity == MedicalNecessityStatus.NOT_MET
            or resolved_indication == ClinicalIndicationStatus.CLINICALLY_CONTRAINDICATED
        ):
            line_verdict = AgentVerdict.FAIL
        # INDETERMINATE cases (e.g. EVAL-CLIN-04, EVAL-CLIN-08, EVAL-CLIN-13)
        elif (
            resolved_performance in (PerformanceEvidenceStatus.NOT_DOCUMENTED, PerformanceEvidenceStatus.ORDERED_ONLY)
            or resolved_indication in (
                ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION,
                ClinicalIndicationStatus.POTENTIALLY_RELATED,
            )
            or resolved_necessity in (
                MedicalNecessityStatus.CRITERIA_INCOMPLETE,
                MedicalNecessityStatus.GUIDELINE_UNAVAILABLE,
            )
            or resolved_prior_auth_req == PriorAuthRequirementStatus.REQUIREMENT_UNKNOWN
            or resolved_prior_auth_ver in (
                PriorAuthVerificationStatus.MISSING_OR_UNVERIFIED,
                PriorAuthVerificationStatus.AUTH_SERVICE_UNAVAILABLE,
            )
            or resolved_repeat in (
                RepeatServiceStatus.UNASSESSED,
                RepeatServiceStatus.REPEATED_AND_UNJUSTIFIED,
            )
            or not spans
            or (necessity_status is None and resolved_guideline is None)
            or (resolved_guideline is not None and not resolved_guideline.all_mandatory_criteria_satisfied)
        ):
            line_verdict = AgentVerdict.INDETERMINATE
        else:
            line_verdict = AgentVerdict.PASS

        discrepancy_flag = line_verdict in (
            AgentVerdict.CONFLICT,
            AgentVerdict.FAIL,
            AgentVerdict.INDETERMINATE,
            AgentVerdict.UNAVAILABLE,
        )

        return LineClinicalEvidence(
            line_number=line_number,
            service_code=service_code,
            modality=resolved_modality,
            billed_units=billed_units,
            billed_amount=billed_amount,
            service_date=service_date,
            performance_status=resolved_performance,
            indication_status=resolved_indication,
            necessity_status=resolved_necessity,
            guideline_citation=resolved_guideline,
            performance_evidence=list(performance_evidence or []),
            indication_evidence=list(indication_evidence or []),
            prior_auth_requirement=resolved_prior_auth_req,
            prior_auth_verification=resolved_prior_auth_ver,
            repeat_service_status=resolved_repeat,
            supporting_spans=spans,
            primary_icd10_codes=list(primary_icd10_codes or []),
            secondary_icd10_codes=list(secondary_icd10_codes or []),
            line_verdict=line_verdict,
            clinical_finding_summary=clinical_finding_summary or f"Clinical evaluation completed: verdict {line_verdict.value}.",
            discrepancy_flag=discrepancy_flag,
            discrepancy_description=discrepancy_description if discrepancy_flag else None,
        )

    @classmethod
    def align_claim(
        cls,
        expected_line_numbers: list[int],
        line_assessments: list[LineClinicalEvidence],
        bed_day_reconciliation: BedDayReconciliation | None = None,
        clinical_documents_present: list[str] | None = None,
        low_ocr_quality_pages: list[int] | None = None,
        latency_ms: int = 25,
    ) -> ClinicalAlignmentOutput:
        """
        Executes claim-level clinical alignment aggregation with fail-closed error propagation.
        """
        return build_clinical_alignment_output(
            expected_line_numbers=expected_line_numbers,
            line_assessments=line_assessments,
            bed_day_reconciliation=bed_day_reconciliation,
            clinical_documents_present=clinical_documents_present or [],
            low_ocr_quality_pages=low_ocr_quality_pages or [],
            latency_ms=latency_ms,
        )


class DefaultClinicalGuidelineRepository:
    """Authoritative clinical guideline repository backed by database storage (ADR-015, ADR-016)."""

    def __init__(
        self,
        guidelines: dict[str, dict[str, Any]] | None = None,
        indications: dict[str, dict[str, Any]] | None = None,
        db: Any = None,
    ) -> None:
        self._guidelines = dict(guidelines) if guidelines is not None else {}
        self._indications = dict(indications) if indications is not None else {}
        self._sql_repo = None
        self._code_repo = None
        if not guidelines and not indications:
            try:
                from medishield.db.catalog import (
                    SqlClinicalGuidelineRepository,
                    SqlCodeCatalogRepository,
                )
                from medishield.db.session import sync_engine
                self._sql_repo = SqlClinicalGuidelineRepository(db or sync_engine)
                self._code_repo = SqlCodeCatalogRepository(db or sync_engine)
            except Exception:  # noqa: BLE001
                self._sql_repo = None
                self._code_repo = None

    def get_guideline(self, service_code: str, service_date: date | None = None) -> dict[str, Any] | None:
        code_key = service_code.strip().replace("REV_", "").upper()
        if code_key in self._guidelines:
            entry = self._guidelines[code_key]
            if service_date:
                eff = entry.get("effective_date")
                exp = entry.get("expiration_date")
                if eff and service_date < eff:
                    return None
                if exp and service_date > exp:
                    return None
            return entry
        if self._sql_repo is not None:
            return self._sql_repo.get_guideline(code_key, service_date=service_date)
        return None

    def get_indication(self, service_code: str) -> dict[str, Any] | None:
        code_key = service_code.strip().replace("REV_", "").upper()
        if code_key in self._indications:
            return self._indications[code_key]
        if self._sql_repo is not None:
            return self._sql_repo.get_indication(code_key)
        return None

    def is_valid_diagnosis_code(self, code: str, service_date: date | None = None) -> bool:
        try:
            return bool(
                self._code_repo
                and self._code_repo.is_valid_code(code, CodingSystem.ICD10_CM, service_date=service_date)
            )
        except OperationalError:
            return False

    def normalize_diagnosis_code(self, raw_code: str, service_date: date | None = None) -> str | None:
        clean = re.sub(r"[^A-Z0-9]", "", raw_code.upper())
        if not clean or self._code_repo is None:
            return None
        try:
            active_codes = self._code_repo.get_active_codes(CodingSystem.ICD10_CM)
        except OperationalError:
            return None
        matches = [
            code for code in active_codes
            if re.sub(r"[^A-Z0-9]", "", code.upper()) == clean
            and self.is_valid_diagnosis_code(code, service_date)
        ]
        return matches[0] if len(matches) == 1 else None

    def get_active_code_details(
        self,
        code: str,
        coding_system: CodingSystem,
        service_date: date | None = None,
    ) -> dict[str, str] | None:
        try:
            if not self._code_repo or not self._code_repo.is_valid_code(code, coding_system, service_date):
                return None
            record = self._code_repo.get_code_details(code, coding_system)
        except OperationalError:
            return None
        if record is None:
            return None
        return {
            "code": record.code,
            "description": record.long_description or record.short_description,
            "coding_system": record.coding_system.value,
        }

    def register_guideline(self, service_code: str, data: dict[str, Any]) -> None:
        code_key = service_code.strip().replace("REV_", "").upper()
        self._guidelines[code_key] = data

    def register_indication(self, service_code: str, data: dict[str, Any]) -> None:
        code_key = service_code.strip().replace("REV_", "").upper()
        self._indications[code_key] = data

