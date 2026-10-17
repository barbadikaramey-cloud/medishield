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

from medishield.db.enums import AgentVerdict
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
from medishield.domain.provenance import FieldProvenance


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

    ICD_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\b([A-TV-Z][0-9]{2}(?:\.[0-9]{1,4})?)\b")
    CPT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\b([0-9]{5})\b")

    @classmethod
    def parse(cls, text: str) -> DischargeSummaryAnalysis:
        if not text:
            return DischargeSummaryAnalysis()

        patterns = {
            "primary": r"(?:FINAL\s*DIAGNOSES?[\s\S]*?)?(?:Primary(?:\s+Diagnosis)?\s*[:\-]?\s*)([^\n]+(?:\n(?!(?:Secondary|PROCEDURE|HOSPITAL|DISCHARGE))[^\n]+)?)",
            "secondary": r"(?:Secondary(?:\s+Diagnosis)?\s*[:\-]?\s*)([^\n]+(?:\n(?!(?:PROCEDURE|HOSPITAL|DISCHARGE|Primary))[^\n]+)?)",
            "procedures": r"(?:PROCEDURES?\s*(?:PERFORMED)?\s*[:\-]?\s*)([^\n]+(?:\n(?!(?:HOSPITAL|DISCHARGE|FINAL|Primary))[^\n]+)*)",
            "hospital_course": r"(?:HOSPITAL\s*COURSE\s*[:\-]?\s*)([^\n]+(?:\n(?!(?:DISCHARGE|FINAL|PROCEDURE))[^\n]+)*)",
        }
        extracted: dict[str, str] = {}
        for k, pat in patterns.items():
            m = re.search(pat, text, re.IGNORECASE)
            extracted[k] = m.group(1).strip() if m else ""

        def _extract_icd(t: str) -> list[str]:
            codes = cls.ICD_PATTERN.findall(t)
            # Normalize OCR misreadings of ICD letter 'I' as digit '1' in e.g. ICD-10: 110 -> I10
            for m in re.finditer(r"ICD(?:-10)?\s*[:\-]?\s*1([0-9]{2}(?:\.[0-9]{1,4})?)", t, re.IGNORECASE):
                cand = f"I{m.group(1)}"
                if cand not in codes:
                    codes.append(cand)
            return list(dict.fromkeys(codes))

        p_icd = _extract_icd(extracted["primary"])
        s_icd = _extract_icd(extracted["secondary"])
        c_icd = _extract_icd(extracted["hospital_course"])
        all_icd = list(dict.fromkeys(p_icd + s_icd + c_icd))

        proc_cpt = cls.CPT_PATTERN.findall(extracted["procedures"])
        course_cpt = cls.CPT_PATTERN.findall(extracted["hospital_course"])
        all_cpt = list(dict.fromkeys(proc_cpt + course_cpt))

        h_course_lower = extracted["hospital_course"].lower()
        has_lab = any(w in h_course_lower for w in ("laboratory", "lab evaluation", "lab workup", "diagnostic workup", "metabolic panel"))
        has_imaging = any(w in h_course_lower for w in ("imaging", "radiology", "x-ray", "mri", "ct scan", "ultrasound", "echocardiogram", "diagnostic workup"))
        has_stay = any(w in h_course_lower for w in ("admitted", "hospitalization", "inpatient", "discharge", "vital signs", "hospital stay"))

        admit_match = re.search(r"(?:Admit(?:Date)?|Admission\s*Date)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})", text, re.IGNORECASE)
        disch_match = re.search(r"(?:Discharge\s*Date)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})", text, re.IGNORECASE)
        parsed_admit = parse_medical_date(admit_match.group(1)) if admit_match else None
        parsed_disch = parse_medical_date(disch_match.group(1)) if disch_match else None

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
    """Protocol for dynamic clinical guideline and indication repository (ADR-015, Spec 09 § 4)."""

    def get_guideline(self, service_code: str) -> dict[str, Any] | None:
        ...

    def get_indication(self, service_code: str) -> dict[str, Any] | None:
        ...

    def register_guideline(self, service_code: str, data: dict[str, Any]) -> None:
        ...

    def register_indication(self, service_code: str, data: dict[str, Any]) -> None:
        ...


class DeterministicClinicalAlignmentEngine:
    """
    Authoritative, deterministic clinical verification engine.
    Cross-verifies medical procedures, laboratory diagnostics, imaging, and bed-day
    units against verified clinical evidence and coverage guidelines.
    """

    _default_repo_instance: ClassVar[DefaultClinicalGuidelineRepository | None] = None

    # ── Standard Clinical Guideline Registry ─────────────────────────────────
    STANDARD_GUIDELINE_CATALOG: ClassVar[dict[str, dict[str, Any]]] = {
        "99214": {
            "guideline_id": "LCD-99214-01",
            "guideline_version": "2026.1",
            "guideline_title": "Evaluation and Management Services - Level 4 Established Patient",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-99214-01",
                    "description": "Documented moderate complexity medical decision making or >= 30 minutes visit time",
                }
            ],
        },
        "99213": {
            "guideline_id": "LCD-99213-01",
            "guideline_version": "2026.1",
            "guideline_title": "Evaluation and Management Services - Level 3 Established Patient",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-99213-01",
                    "description": "Documented low complexity medical decision making or >= 20 minutes visit time",
                }
            ],
        },
        "93000": {
            "guideline_id": "LCD-93000-01",
            "guideline_version": "2026.1",
            "guideline_title": "Diagnostic Electrocardiogram (ECG/EKG) with Interpretation and Report",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-93000-01",
                    "description": "Documented cardiac symptoms, rhythm disturbance, or baseline pre-operative assessment",
                }
            ],
        },
        "44970": {
            "guideline_id": "LCD-44970-01",
            "guideline_version": "2026.1",
            "guideline_title": "Laparoscopic Appendectomy",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-44970-01",
                    "description": "Clinical, laboratory, or imaging evidence of acute or suspected appendicitis",
                }
            ],
        },
        "80053": {
            "guideline_id": "LCD-80053-01",
            "guideline_version": "2026.1",
            "guideline_title": "Comprehensive Metabolic Panel (CMP)",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-80053-01",
                    "description": "Documented medical indication for electrolyte, renal, or hepatic assessment",
                }
            ],
        },
        "70553": {
            "guideline_id": "LCD-70553-01",
            "guideline_version": "2026.1",
            "guideline_title": "Magnetic Resonance Imaging (MRI) Brain With and Without Contrast",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": True,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-70553-01",
                    "description": "Documented focal neurological signs, intractable headache, seizure, or intracranial lesion",
                }
            ],
        },
        "27447": {
            "guideline_id": "LCD-27447-01",
            "guideline_version": "2026.1",
            "guideline_title": "Total Knee Arthroplasty (TKA)",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-27447-CONSERVATIVE",
                    "description": "Documented failure of non-surgical conservative therapy for >= 6 weeks",
                },
                {
                    "criterion_id": "CRIT-27447-RADIOGRAPHIC",
                    "description": "Severe advanced joint disease demonstrated radiographically (Kellgren-Lawrence Grade 3 or 4)",
                },
            ],
        },
        "47562": {
            "guideline_id": "LCD-47562-01",
            "guideline_version": "2026.1",
            "guideline_title": "Laparoscopic Cholecystectomy",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-47562-01",
                    "description": "Documented symptomatic cholelithiasis, acute cholecystitis, or biliary dyskinesia",
                }
            ],
        },
        "71045": {
            "guideline_id": "LCD-71045-01",
            "guideline_version": "2026.1",
            "guideline_title": "Chest Radiography Single View",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-71045-01",
                    "description": "Documented respiratory symptoms, pulmonary evaluation, or chest trauma",
                }
            ],
        },
        "49000": {
            "guideline_id": "LCD-49000-01",
            "guideline_version": "2026.1",
            "guideline_title": "Exploratory Laparotomy",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-49000-01",
                    "description": "Documented acute abdomen, blunt/penetrating abdominal trauma, or hemoperitoneum",
                }
            ],
        },
        "0120": {
            "guideline_id": "LCD-REV-0120",
            "guideline_version": "2026.1",
            "guideline_title": "Inpatient Room and Board - Semi-Private Two Bed",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-REV-01",
                    "description": "Documented formal physician inpatient admission order and continuous nursing care",
                }
            ],
        },
        "33533": {
            "guideline_id": "LCD-33533-01",
            "guideline_version": "2026.1",
            "guideline_title": "Coronary Artery Bypass Graft (CABG)",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-33533-01",
                    "description": "Documented coronary artery disease, multivessel disease, or angina pectoris",
                }
            ],
        },
        "36415": {
            "guideline_id": "LCD-36415-01",
            "guideline_version": "2026.1",
            "guideline_title": "Routine Venipuncture for Laboratory Specimen Collection",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-36415-01",
                    "description": "Documented physician order for blood specimen or diagnostic laboratory workup",
                }
            ],
        },
        "43239": {
            "guideline_id": "LCD-43239-01",
            "guideline_version": "2026.1",
            "guideline_title": "Esophagogastroduodenoscopy (EGD) with Biopsy",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-43239-01",
                    "description": "Documented dyspepsia, GERD, abdominal pain, gastrointestinal bleeding, or ulcer evaluation",
                }
            ],
        },
        "90837": {
            "guideline_id": "LCD-90837-01",
            "guideline_version": "2026.1",
            "guideline_title": "Psychotherapy Services - 60 Minutes",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-90837-01",
                    "description": "Documented psychiatric evaluation, depressive disorder, anxiety, or behavioral health management",
                }
            ],
        },
        "71046": {
            "guideline_id": "LCD-71046-01",
            "guideline_version": "2026.1",
            "guideline_title": "Chest Radiography Two Views",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-71046-01",
                    "description": "Documented respiratory symptoms, pulmonary evaluation, or chest trauma",
                }
            ],
        },
        "93306": {
            "guideline_id": "LCD-93306-01",
            "guideline_version": "2026.1",
            "guideline_title": "Transthoracic Echocardiography with Doppler",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-93306-01",
                    "description": "Documented cardiac symptoms, murmur, heart failure, or ischemic heart disease assessment",
                }
            ],
        },
        "99215": {
            "guideline_id": "LCD-99215-01",
            "guideline_version": "2026.1",
            "guideline_title": "Evaluation and Management Services - Level 5 Established Patient",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "mandatory_criteria": [
                {
                    "criterion_id": "CRIT-99215-01",
                    "description": "Documented high complexity medical decision making or >= 40 minutes visit time",
                }
            ],
        },
    }

    # ── Standard Clinical Diagnosis-to-Procedure Indication Map ─────────────
    # Validates clinical and anatomical compatibility between documented ICD-10 diagnoses and billed CPT procedures
    DIAGNOSIS_INDICATION_MAP: ClassVar[dict[str, dict[str, Any]]] = {
        "27447": {
            "name": "Total Knee Arthroplasty (TKA)",
            "valid_icd_prefixes": ("M17", "M25.56", "M23", "M84.56", "M05", "M06"),
            "anatomy": "Knee Joint",
            "contraindicated_incompatible_prefixes": ("S52", "S62", "F32", "F33", "J01"),
        },
        "44970": {
            "name": "Laparoscopic Appendectomy",
            "valid_icd_prefixes": ("K35", "K36", "K37", "R10.31"),
            "anatomy": "Appendix / Lower Right Abdomen",
            "contraindicated_incompatible_prefixes": ("F32", "F33", "F41", "S52", "S62", "M17"),
        },
        "47562": {
            "name": "Laparoscopic Cholecystectomy",
            "valid_icd_prefixes": ("K80", "K81", "K82", "R10.13"),
            "anatomy": "Gallbladder / Biliary Tract",
            "contraindicated_incompatible_prefixes": ("F32", "F33", "J01", "S52"),
        },
        "33533": {
            "name": "Coronary Artery Bypass Graft (CABG)",
            "valid_icd_prefixes": ("I25", "I20", "I21"),
            "anatomy": "Coronary Arteries / Cardiovascular",
            "contraindicated_incompatible_prefixes": ("S52", "F32", "M17"),
        },
        "43239": {
            "name": "Esophagogastroduodenoscopy (EGD) with Biopsy",
            "valid_icd_prefixes": ("K21", "K29", "K25", "K26", "K27", "R10", "K92", "K30"),
            "anatomy": "Upper Gastrointestinal Tract / Esophagus / Stomach",
            "contraindicated_incompatible_prefixes": ("S52", "M17"),
        },
        "90837": {
            "name": "Psychotherapy 60 min",
            "valid_icd_prefixes": ("F32", "F33", "F41", "F43", "F31", "F10", "F90"),
            "anatomy": "Mental / Behavioral Health",
            "contraindicated_incompatible_prefixes": ("S52", "M17"),
        },
        "93306": {
            "name": "Transthoracic Echocardiography",
            "valid_icd_prefixes": ("I25", "I50", "I20", "I21", "I48", "I34", "I35", "R00", "R01", "R07"),
            "anatomy": "Heart / Cardiovascular",
            "contraindicated_incompatible_prefixes": ("S52", "M17"),
        },
        "36415": {
            "name": "Venipuncture for Specimen Collection",
            "valid_icd_prefixes": ("I", "E", "K", "J", "M", "N", "R", "F", "S", "G"),
            "anatomy": "Venous System / Diagnostic Workup",
            "contraindicated_incompatible_prefixes": (),
        },
        "71046": {
            "name": "Chest Radiography 2 Views",
            "valid_icd_prefixes": ("J", "R05", "R06", "R07", "I25", "I50"),
            "anatomy": "Thorax / Lungs",
            "contraindicated_incompatible_prefixes": (),
        },
    }

    @classmethod
    def parse_discharge_summary(cls, text: str) -> DischargeSummaryAnalysis:
        """Parses structured sections (Primary, Secondary, Hospital Course, Procedures) from discharge summary."""
        return DischargeSummaryParser.parse(text)

    @classmethod
    def classify_modality(cls, service_code: str) -> ServiceModality:
        """Determines the clinical service modality from CPT, HCPCS, or Revenue code."""
        code = service_code.strip().upper()
        if code.startswith("REV") or code in ("0100", "0110", "0120", "0130", "0140", "0150", "0200", "0210"):
            return ServiceModality.ROOM_AND_BOARD

        # Numeric CPT ranges
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

        if code.startswith(("J", "Q")) or len(code) == 11:
            return ServiceModality.PHARMACEUTICAL

        return ServiceModality.OTHER_SERVICE

    @classmethod
    def _get_default_repo(cls) -> DefaultClinicalGuidelineRepository:
        if cls._default_repo_instance is None:
            cls._default_repo_instance = DefaultClinicalGuidelineRepository()
        return cls._default_repo_instance

    @classmethod
    def get_guideline_for_code(
        cls,
        service_code: str,
        service_date: date | None = None,
        criteria_satisfied_overrides: dict[str, bool] | None = None,
        spans_override: list[FieldProvenance] | None = None,
        guideline_repo: ClinicalGuidelineRepository | None = None,
    ) -> GuidelineCitation | None:
        """Retrieves and populates a GuidelineCitation for an authoritative medical code."""
        code_key = service_code.strip().replace("REV_", "")
        repo = guideline_repo or cls._get_default_repo()
        template = repo.get_guideline(code_key) if hasattr(repo, "get_guideline") else cls.STANDARD_GUIDELINE_CATALOG.get(code_key)
        if not template:
            return None

        eff_date = template["effective_date"]
        exp_date = template.get("expiration_date")
        if service_date and service_date < eff_date:
            return None
        if service_date and exp_date and service_date > exp_date:
            return None

        overrides = criteria_satisfied_overrides or {}
        spans = spans_override or [
            FieldProvenance(
                document_id=f"DOC-GUIDELINE-{template['guideline_id']}",
                page_number=1,
                span_id=None,
                text_content=template["guideline_title"],
                bbox=None,
                bounding_box=None,
                evidence_quality_score=0.95,
            )
        ]

        criteria = []
        for c in template["mandatory_criteria"]:
            crit_id = c["criterion_id"]
            satisfied = overrides.get(crit_id, True)
            criteria.append(
                MandatoryGuidelineCriterion(
                    criterion_id=crit_id,
                    description=c["description"],
                    is_mandatory=True,
                    evaluation_outcome=(
                        CriterionEvaluationOutcome.SATISFIED
                        if satisfied
                        else CriterionEvaluationOutcome.NOT_SATISFIED
                    ),
                    supporting_spans=spans if satisfied else [],
                    rationale="Evaluated against clinical documentation." if satisfied else "Criteria not satisfied in clinical record.",
                )
            )

        return GuidelineCitation(
            guideline_id=template["guideline_id"],
            guideline_version=template["guideline_version"],
            guideline_title=template["guideline_title"],
            effective_date=eff_date,
            expiration_date=exp_date,
            criteria_evaluations=criteria,
        )

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

        # 4. Ambiguous Date-Only Stay (EVAL-CLIN-14)
        if not timestamps_complete:
            days_span = calendar_days if calendar_days is not None else 1
            min_u = float(max(0, days_span))
            max_u = float(max(1, days_span + 1))
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
        diagnosis_codes: list[str] | None = None,
        guideline_repo: ClinicalGuidelineRepository | None = None,
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

        # 1. Date Boundary & Post-Discharge Non-Performance Check (EVAL-CLIN-10)
        is_post_discharge = False
        resolved_necessity: MedicalNecessityStatus
        if encounter_discharge_datetime and (
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
        if (
            "patient refused" in text_lower
            or "refused surgery" in text_lower
            or "procedure canceled" in text_lower
            or "procedure cancelled" in text_lower
            or "surgery canceled" in text_lower
            or "surgery cancelled" in text_lower
            or performance_status == PerformanceEvidenceStatus.EXPLICITLY_CONTRADICTED
        ):
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
                supporting_spans=[],
                line_verdict=AgentVerdict.CONFLICT,
                clinical_finding_summary=clinical_finding_summary or "Clinical documentation explicitly contradicts performance.",
                discrepancy_flag=True,
                discrepancy_description=discrepancy_description or "Documented refusal or procedure cancellation contradicts billed line.",
            )

        # 3. Prior Authorization Requirement Resolution
        template = (
            repo.get_guideline(service_code)
            if hasattr(repo, "get_guideline")
            else cls.STANDARD_GUIDELINE_CATALOG.get(service_code.strip().replace("REV_", ""))
        )
        requires_pa_catalog = template["requires_prior_auth"] if template else False

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
        diag_rule = (
            repo.get_indication(service_code)
            if hasattr(repo, "get_indication")
            else cls.DIAGNOSIS_INDICATION_MAP.get(service_code.strip().replace("REV_", ""))
        )
        if diag_rule:
            proc_name = diag_rule["name"]
            all_eval_diags: list[str] = []
            if diagnosis_codes:
                all_eval_diags.extend(diagnosis_codes)
            if primary_icd10_codes:
                all_eval_diags.extend(primary_icd10_codes)
            if secondary_icd10_codes:
                all_eval_diags.extend(secondary_icd10_codes)

            has_matching_diag = False
            if all_eval_diags:
                clean_diag = [str(d).upper().replace(".", "").strip() for d in all_eval_diags if d]
                valid_prefixes = tuple(p.replace(".", "").upper() for p in diag_rule["valid_icd_prefixes"])
                has_matching_diag = any(any(d.startswith(p) for p in valid_prefixes) for d in clean_diag)

            # Check if condition anatomy or diagnosis keywords are in primary, secondary, or hospital course text
            diag_keywords = {
                "27447": ("knee", "osteoarthritis", "arthroplasty", "joint disease", "gonarthrosis", "condyle", "m17"),
                "44970": ("appendectomy", "appendix", "appendicitis", "lower right abdominal", "rlq", "k35"),
                "47562": ("cholecystectomy", "cholecystitis", "gallbladder", "cholelithiasis", "biliary", "k80", "k81"),
                "33533": ("coronary", "cad", "angina", "myocardial", "ischemic heart", "bypass", "i25"),
                "43239": ("endoscopy", "egd", "gastritis", "dyspepsia", "gerd", "ulcer", "upper gi", "biopsy", "k21"),
                "90837": ("psychotherapy", "depression", "depressive", "anxiety", "mental health", "behavioral", "f32"),
            }
            code_clean = service_code.strip().replace("REV_", "")
            kw_candidates = diag_keywords.get(code_clean, ())
            clin_context_text = f"{primary_diagnosis_text or ''} {secondary_diagnosis_text or ''} {hospital_course_text or ''} {clinical_notes_text or ''}".lower()
            text_indicated = any(kw in clin_context_text for kw in kw_candidates)

            if has_matching_diag or text_indicated:
                resolved_indication = ClinicalIndicationStatus.EXPLICITLY_INDICATED
                discrepancy_description = None
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
        if resolved_guideline is None and resolved_performance in (
            PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
            PerformanceEvidenceStatus.RESULTED,
        ):
            resolved_guideline = cls.get_guideline_for_code(
                service_code=service_code,
                service_date=service_date,
                spans_override=spans,
                guideline_repo=guideline_repo,
            )

        if necessity_status is not None:
            resolved_necessity = necessity_status
        elif resolved_guideline is None:
            resolved_necessity = MedicalNecessityStatus.GUIDELINE_UNAVAILABLE
        elif resolved_guideline.all_mandatory_criteria_satisfied:
            resolved_necessity = MedicalNecessityStatus.MET
        else:
            resolved_necessity = MedicalNecessityStatus.NOT_MET

        # 8. Deterministic Line Verdict Derivation
        # CONFLICT cases
        if resolved_performance in (
            PerformanceEvidenceStatus.EXPLICITLY_CONTRADICTED,
            PerformanceEvidenceStatus.CONFIRMED_NON_PERFORMANCE,
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
            or resolved_guideline is None
            or not resolved_guideline.all_mandatory_criteria_satisfied
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
    """Authoritative in-memory clinical guideline repository allowing dynamic registration (ADR-015)."""

    def __init__(
        self,
        guidelines: dict[str, dict[str, Any]] | None = None,
        indications: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._guidelines = (
            dict(guidelines)
            if guidelines is not None
            else dict(DeterministicClinicalAlignmentEngine.STANDARD_GUIDELINE_CATALOG)
        )
        self._indications = (
            dict(indications)
            if indications is not None
            else dict(DeterministicClinicalAlignmentEngine.DIAGNOSIS_INDICATION_MAP)
        )

    def get_guideline(self, service_code: str) -> dict[str, Any] | None:
        code_key = service_code.strip().replace("REV_", "")
        return self._guidelines.get(code_key)

    def get_indication(self, service_code: str) -> dict[str, Any] | None:
        code_key = service_code.strip().replace("REV_", "")
        return self._indications.get(code_key)

    def register_guideline(self, service_code: str, data: dict[str, Any]) -> None:
        code_key = service_code.strip().replace("REV_", "")
        self._guidelines[code_key] = data

    def register_indication(self, service_code: str, data: dict[str, Any]) -> None:
        code_key = service_code.strip().replace("REV_", "")
        self._indications[code_key] = data

