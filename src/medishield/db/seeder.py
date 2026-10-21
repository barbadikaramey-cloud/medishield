"""Authoritative Dataset & Master Entity Seeder for SQLite & PostgreSQL (ADR-008).

Seeds master Member records, Policies, Policy Versions, Enrollments, and Policy Chunks
for all 30 benchmark patient clusters (including PT_16658 / Cluster C_012).
"""

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from medishield.db.enums import CodingSystem
from medishield.db.models import (
    ClinicalGuideline,
    DiagnosisIndication,
    Enrollment,
    EnrollmentRiderApplication,
    GuidelineCriterion,
    MedicalCode,
    Member,
    Policy,
    PolicyChunk,
    PolicyRider,
    PolicyVersion,
)
from medishield.db.session import get_sqlite_session_factory

logger = logging.getLogger(__name__)


def seed_medical_codes(session: Session) -> int:
    """
    Seeds comprehensive baseline medical terminology (CPT, HCPCS, Revenue, ICD-10)
    into the medical_codes table (ADR-012, Spec 07 § 5).
    """
    existing_codes: set[tuple[CodingSystem, str]] = {
        (row[0], row[1]) for row in session.query(MedicalCode.coding_system, MedicalCode.code).all()
    }

    codes_to_seed: list[dict[str, Any]] = [
        # Outpatient Evaluation & Management
        {"code": "99213", "coding_system": CodingSystem.CPT, "short_description": "Office/outpatient visit, established, low-moderate", "category": "Evaluation and Management", "standard_charge": Decimal("150.00"), "requires_prior_auth": False},
        {"code": "99214", "coding_system": CodingSystem.CPT, "short_description": "Office/outpatient visit, established, moderate-high", "category": "Evaluation and Management", "standard_charge": Decimal("225.00"), "requires_prior_auth": False},
        {"code": "99215", "coding_system": CodingSystem.CPT, "short_description": "Office/outpatient visit, established, high complexity", "category": "Evaluation and Management", "standard_charge": Decimal("320.00"), "requires_prior_auth": False},
        {"code": "99203", "coding_system": CodingSystem.CPT, "short_description": "Office/outpatient visit, new, low-moderate", "category": "Evaluation and Management", "standard_charge": Decimal("180.00"), "requires_prior_auth": False},
        {"code": "99204", "coding_system": CodingSystem.CPT, "short_description": "Office/outpatient visit, new, moderate", "category": "Evaluation and Management", "standard_charge": Decimal("260.00"), "requires_prior_auth": False},
        {"code": "99205", "coding_system": CodingSystem.CPT, "short_description": "Office/outpatient visit, new, high complexity", "category": "Evaluation and Management", "standard_charge": Decimal("360.00"), "requires_prior_auth": False},
        {"code": "99283", "coding_system": CodingSystem.CPT, "short_description": "Emergency department visit, moderate severity", "category": "Emergency", "standard_charge": Decimal("450.00"), "requires_prior_auth": False},
        {"code": "99284", "coding_system": CodingSystem.CPT, "short_description": "Emergency department visit, high severity", "category": "Emergency", "standard_charge": Decimal("750.00"), "requires_prior_auth": False},
        {"code": "99291", "coding_system": CodingSystem.CPT, "short_description": "Critical care, evaluation and management, first 30-74 min", "category": "Critical Care", "standard_charge": Decimal("1200.00"), "requires_prior_auth": False},

        # Diagnostic Imaging & Tests
        {"code": "71045", "coding_system": CodingSystem.CPT, "short_description": "Radiologic examination, chest; single view", "category": "Diagnostic Radiology", "standard_charge": Decimal("95.00"), "requires_prior_auth": False},
        {"code": "71046", "coding_system": CodingSystem.CPT, "short_description": "Radiologic examination, chest; 2 views", "category": "Diagnostic Radiology", "standard_charge": Decimal("140.00"), "requires_prior_auth": False},
        {"code": "71250", "coding_system": CodingSystem.CPT, "short_description": "Computed tomography, thorax; without contrast", "category": "Diagnostic Radiology", "standard_charge": Decimal("650.00"), "requires_prior_auth": False},
        {"code": "70553", "coding_system": CodingSystem.CPT, "short_description": "Magnetic resonance imaging, brain; with/without contrast", "category": "Diagnostic Radiology", "standard_charge": Decimal("1250.00"), "requires_prior_auth": False},
        {"code": "72148", "coding_system": CodingSystem.CPT, "short_description": "Magnetic resonance imaging, lumbar spine; without contrast", "category": "Diagnostic Radiology", "standard_charge": Decimal("1100.00"), "requires_prior_auth": False},
        {"code": "93000", "coding_system": CodingSystem.CPT, "short_description": "Electrocardiogram, routine ECG with interpretation", "category": "Cardiovascular", "standard_charge": Decimal("85.00"), "requires_prior_auth": False},
        {"code": "93306", "coding_system": CodingSystem.CPT, "short_description": "Echocardiography, transthoracic, complete with Doppler", "category": "Cardiovascular", "standard_charge": Decimal("850.00"), "requires_prior_auth": False},

        # Laboratory & Pathology
        {"code": "80053", "coding_system": CodingSystem.CPT, "short_description": "Comprehensive metabolic panel (CMP)", "category": "Pathology and Laboratory", "standard_charge": Decimal("75.00"), "requires_prior_auth": False},
        {"code": "85025", "coding_system": CodingSystem.CPT, "short_description": "Complete blood count (CBC) with differential", "category": "Pathology and Laboratory", "standard_charge": Decimal("45.00"), "requires_prior_auth": False},
        {"code": "36415", "coding_system": CodingSystem.CPT, "short_description": "Routine venipuncture for blood specimen collection", "category": "Pathology and Laboratory", "standard_charge": Decimal("25.00"), "requires_prior_auth": False},
        {"code": "86849", "coding_system": CodingSystem.CPT, "short_description": "Unlisted immunology procedure", "category": "Pathology and Laboratory", "standard_charge": Decimal("150.00"), "requires_prior_auth": False},
        {"code": "90837", "coding_system": CodingSystem.CPT, "short_description": "Psychotherapy, 60 minutes with patient", "category": "Behavioral Health", "standard_charge": Decimal("210.00"), "requires_prior_auth": False},
        {"code": "59400", "coding_system": CodingSystem.CPT, "short_description": "Obstetric care and vaginal delivery", "category": "Maternity"},

        # Surgical Procedures (Requiring Prior Auth)
        {"code": "27447", "coding_system": CodingSystem.CPT, "short_description": "Arthroplasty, knee, condyle and plateau (TKA)", "category": "Surgery / Musculoskeletal", "standard_charge": Decimal("42000.00"), "requires_prior_auth": True},
        {"code": "47562", "coding_system": CodingSystem.CPT, "short_description": "Laparoscopy, surgical; cholecystectomy", "category": "Surgery / Digestive", "standard_charge": Decimal("8500.00"), "requires_prior_auth": True},
        {"code": "33533", "coding_system": CodingSystem.CPT, "short_description": "Coronary artery bypass, using arterial graft(s); single arterial graft", "category": "Surgery / Cardiovascular", "standard_charge": Decimal("38000.00"), "requires_prior_auth": True},
        {"code": "29827", "coding_system": CodingSystem.CPT, "short_description": "Arthroscopy, shoulder, surgical; with rotator cuff repair", "category": "Surgery / Musculoskeletal", "standard_charge": Decimal("12500.00"), "requires_prior_auth": True},
        {"code": "21120", "coding_system": CodingSystem.CPT, "short_description": "Genioplasty; augmentation, bone graft", "category": "Surgery / Musculoskeletal", "standard_charge": Decimal("9500.00"), "requires_prior_auth": True},
        {"code": "44204", "coding_system": CodingSystem.CPT, "short_description": "Laparoscopy, surgical; colectomy, partial", "category": "Surgery / Digestive", "standard_charge": Decimal("16000.00"), "requires_prior_auth": True},
        {"code": "63030", "coding_system": CodingSystem.CPT, "short_description": "Laminotomy with decompression of nerve root(s), lumbar", "category": "Surgery / Nervous", "standard_charge": Decimal("14000.00"), "requires_prior_auth": True},
        {"code": "22842", "coding_system": CodingSystem.CPT, "short_description": "Posterior segmental instrumentation, 3 to 6 vertebral segments", "category": "Surgery / Musculoskeletal", "standard_charge": Decimal("11000.00"), "requires_prior_auth": True},
        {"code": "22612", "coding_system": CodingSystem.CPT, "short_description": "Arthrodesis, posterior or posterolateral, single level; lumbar", "category": "Surgery / Musculoskeletal", "standard_charge": Decimal("18500.00"), "requires_prior_auth": True},
        {"code": "43644", "coding_system": CodingSystem.CPT, "short_description": "Laparoscopy, surgical, gastric restrictive procedure; with gastric bypass", "category": "Surgery / Digestive", "standard_charge": Decimal("24000.00"), "requires_prior_auth": True},

        # Surgical Procedures (Standard/Emergency - No Prior Auth)
        {"code": "44970", "coding_system": CodingSystem.CPT, "short_description": "Laparoscopy, surgical, appendectomy", "category": "Surgery / Digestive", "standard_charge": Decimal("8750.00"), "requires_prior_auth": False},
        {"code": "43239", "coding_system": CodingSystem.CPT, "short_description": "Esophagogastroduodenoscopy, flexible, transoral; with biopsy", "category": "Surgery / Digestive", "standard_charge": Decimal("1850.00"), "requires_prior_auth": False},
        {"code": "00790", "coding_system": CodingSystem.CPT, "short_description": "Anesthesia for intraperitoneal procedures in upper abdomen", "category": "Anesthesiology", "standard_charge": Decimal("1200.00"), "requires_prior_auth": False},
        {"code": "15822", "coding_system": CodingSystem.CPT, "short_description": "Blepharoplasty, upper eyelid", "category": "Surgery / Integumentary", "standard_charge": Decimal("3200.00"), "requires_prior_auth": False},
        {"code": "17000", "coding_system": CodingSystem.CPT, "short_description": "Destruction premalignant lesion; first lesion", "category": "Surgery / Integumentary", "standard_charge": Decimal("165.00"), "requires_prior_auth": False},
        {"code": "15780", "coding_system": CodingSystem.CPT, "short_description": "Dermabrasion; total face", "category": "Surgery / Integumentary", "standard_charge": Decimal("2800.00"), "requires_prior_auth": False},
        {"code": "58300", "coding_system": CodingSystem.CPT, "short_description": "Insertion of intrauterine device (IUD)", "category": "Surgery / Female Genital", "standard_charge": Decimal("350.00"), "requires_prior_auth": False},

        # Revenue Codes (Inpatient Room & Board)
        {"code": "0100", "coding_system": CodingSystem.REV_CODE, "short_description": "All Inclusive Room and Board", "category": "Room and Board", "standard_charge": Decimal("1500.00"), "requires_prior_auth": False},
        {"code": "0110", "coding_system": CodingSystem.REV_CODE, "short_description": "Room & Board - Private (Medical/Surgical)", "category": "Room and Board", "standard_charge": Decimal("1800.00"), "requires_prior_auth": False},
        {"code": "0120", "coding_system": CodingSystem.REV_CODE, "short_description": "Room & Board - Semi-Private (Two Bed)", "category": "Room and Board", "standard_charge": Decimal("1250.00"), "requires_prior_auth": False},
        {"code": "0200", "coding_system": CodingSystem.REV_CODE, "short_description": "Intensive Care Unit (ICU) - General", "category": "Intensive Care", "standard_charge": Decimal("3500.00"), "requires_prior_auth": False},
        {"code": "0250", "coding_system": CodingSystem.REV_CODE, "short_description": "Pharmacy - General", "category": "Pharmacy", "standard_charge": Decimal("200.00"), "requires_prior_auth": False},
        {"code": "0270", "coding_system": CodingSystem.REV_CODE, "short_description": "Medical/Surgical Supplies and Devices", "category": "Supplies", "standard_charge": Decimal("350.00"), "requires_prior_auth": False},

        # ICD-10-CM diagnosis codes (billable codes only; no charge or prior-auth semantics)
        {"code": "E11.9", "coding_system": CodingSystem.ICD10_CM, "short_description": "Type 2 diabetes mellitus without complications", "category": "Endocrine"},
        {"code": "F32.1", "coding_system": CodingSystem.ICD10_CM, "short_description": "Major depressive disorder, single episode, moderate", "category": "Mental and Behavioral"},
        {"code": "G43.909", "coding_system": CodingSystem.ICD10_CM, "short_description": "Migraine, unspecified, not intractable, without status migrainosus", "category": "Nervous System"},
        {"code": "M54.50", "coding_system": CodingSystem.ICD10_CM, "short_description": "Low back pain, unspecified", "category": "Musculoskeletal"},
        {"code": "M54.51", "coding_system": CodingSystem.ICD10_CM, "short_description": "Vertebrogenic low back pain", "category": "Musculoskeletal"},
        {"code": "M54.59", "coding_system": CodingSystem.ICD10_CM, "short_description": "Other low back pain", "category": "Musculoskeletal"},
        {"code": "I10", "coding_system": CodingSystem.ICD10_CM, "short_description": "Essential (primary) hypertension", "category": "Circulatory"},
        {"code": "I25.10", "coding_system": CodingSystem.ICD10_CM, "short_description": "Atherosclerotic heart disease of native coronary artery without angina pectoris", "category": "Circulatory"},
        {"code": "J01.90", "coding_system": CodingSystem.ICD10_CM, "short_description": "Acute sinusitis, unspecified", "category": "Respiratory"},
        {"code": "J18.9", "coding_system": CodingSystem.ICD10_CM, "short_description": "Pneumonia, unspecified organism", "category": "Respiratory"},
        {"code": "K35.80", "coding_system": CodingSystem.ICD10_CM, "short_description": "Unspecified acute appendicitis", "category": "Digestive"},
        {"code": "K21.00", "coding_system": CodingSystem.ICD10_CM, "short_description": "GERD with esophagitis, without bleeding", "category": "Digestive"},
        {"code": "K21.01", "coding_system": CodingSystem.ICD10_CM, "short_description": "GERD with esophagitis, with bleeding", "category": "Digestive"},
        {"code": "M17.11", "coding_system": CodingSystem.ICD10_CM, "short_description": "Unilateral primary osteoarthritis, right knee", "category": "Musculoskeletal"},
        {"code": "M75.100", "coding_system": CodingSystem.ICD10_CM, "short_description": "Unspecified rotator cuff tear or rupture of unspecified shoulder, not specified as traumatic", "category": "Musculoskeletal"},
        {"code": "N39.0", "coding_system": CodingSystem.ICD10_CM, "short_description": "Urinary tract infection, site not specified", "category": "Genitourinary"},
        {"code": "S52.501A", "coding_system": CodingSystem.ICD10_CM, "short_description": "Unspecified fracture of the lower end of right radius, initial encounter for closed fracture", "category": "Injury"},
    ]

    seeded = 0
    now = datetime.now(UTC)
    for c in codes_to_seed:
        c_sys: CodingSystem = c["coding_system"]
        c_code: str = str(c["code"]).strip().upper()
        key = (c_sys, c_code)
        if key not in existing_codes:
            mc = MedicalCode(
                id=uuid.uuid4(),
                code=c_code,
                coding_system=c_sys,
                short_description=str(c["short_description"]),
                category=str(c["category"]),
                standard_charge=Decimal(str(c["standard_charge"])) if c.get("standard_charge") is not None else None,
                requires_prior_auth=bool(c.get("requires_prior_auth", False)),
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(mc)
            existing_codes.add(key)
            seeded += 1

    session.flush()
    logger.info("Successfully seeded %d medical codes into catalog", seeded)
    return seeded


def seed_clinical_guidelines_and_indications(session: Session) -> tuple[int, int]:
    """
    Seeds authoritative clinical practice guidelines (LCDs/NCDs) and diagnosis indication
    rules into relational tables (clinical_guidelines, guideline_criteria, diagnosis_indications)
    for PostgreSQL & SQLite (ADR-015, ADR-016).
    """
    now = datetime.now(UTC)
    seeded_guidelines = 0
    seeded_indications = 0

    existing_guideline_ids = {
        row[0] for row in session.query(ClinicalGuideline.guideline_id).all()
    }
    existing_indications = {
        row[0] for row in session.query(DiagnosisIndication.service_code).all()
    }

    # 1. 18 Authoritative Clinical Guidelines (LCDs)
    guideline_definitions: list[dict[str, Any]] = [
        {
            "service_code": "99214",
            "guideline_id": "LCD-99214-01",
            "guideline_version": "2026.1",
            "guideline_title": "Evaluation and Management Services - Level 4 Established Patient",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-99214-01", "description": "Documented moderate complexity medical decision making or >= 30 minutes visit time", "is_mandatory": True},
            ],
        },
        {
            "service_code": "99213",
            "guideline_id": "LCD-99213-01",
            "guideline_version": "2026.1",
            "guideline_title": "Evaluation and Management Services - Level 3 Established Patient",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-99213-01", "description": "Documented low complexity medical decision making or >= 20 minutes visit time", "is_mandatory": True},
            ],
        },
        {
            "service_code": "93000",
            "guideline_id": "LCD-93000-01",
            "guideline_version": "2026.1",
            "guideline_title": "Diagnostic Electrocardiogram (ECG/EKG) with Interpretation and Report",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-93000-01", "description": "Documented cardiac symptoms, rhythm disturbance, or baseline pre-operative assessment", "is_mandatory": True},
            ],
        },
        {
            "service_code": "44970",
            "guideline_id": "LCD-44970-01",
            "guideline_version": "2026.1",
            "guideline_title": "Laparoscopic Appendectomy",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-44970-01", "description": "Clinical, laboratory, or imaging evidence of acute or suspected appendicitis", "is_mandatory": True},
            ],
        },
        {
            "service_code": "80053",
            "guideline_id": "LCD-80053-01",
            "guideline_version": "2026.1",
            "guideline_title": "Comprehensive Metabolic Panel (CMP)",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-80053-01", "description": "Documented medical indication for electrolyte, renal, or hepatic assessment", "is_mandatory": True},
            ],
        },
        {
            "service_code": "70553",
            "guideline_id": "LCD-70553-01",
            "guideline_version": "2026.1",
            "guideline_title": "Magnetic Resonance Imaging (MRI) Brain With and Without Contrast",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": True,
            "criteria": [
                {"criterion_id": "CRIT-70553-01", "description": "Documented focal neurological signs, intractable headache, seizure, or intracranial lesion", "is_mandatory": True},
            ],
        },
        {
            "service_code": "27447",
            "guideline_id": "LCD-27447-01",
            "guideline_version": "2026.1",
            "guideline_title": "Total Knee Arthroplasty (TKA)",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-27447-CONSERVATIVE", "description": "Documented failure of non-surgical conservative therapy for >= 6 weeks", "is_mandatory": True},
                {"criterion_id": "CRIT-27447-RADIOGRAPHIC", "description": "Severe advanced joint disease demonstrated radiographically (Kellgren-Lawrence Grade 3 or 4)", "is_mandatory": True},
            ],
        },
        {
            "service_code": "47562",
            "guideline_id": "LCD-47562-01",
            "guideline_version": "2026.1",
            "guideline_title": "Laparoscopic Cholecystectomy",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-47562-01", "description": "Documented symptomatic cholelithiasis, acute cholecystitis, or biliary dyskinesia", "is_mandatory": True},
            ],
        },
        {
            "service_code": "71045",
            "guideline_id": "LCD-71045-01",
            "guideline_version": "2026.1",
            "guideline_title": "Chest Radiography Single View",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-71045-01", "description": "Documented respiratory symptoms, pulmonary evaluation, or chest trauma", "is_mandatory": True},
            ],
        },
        {
            "service_code": "49000",
            "guideline_id": "LCD-49000-01",
            "guideline_version": "2026.1",
            "guideline_title": "Exploratory Laparotomy",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-49000-01", "description": "Documented acute abdomen, blunt/penetrating abdominal trauma, or hemoperitoneum", "is_mandatory": True},
            ],
        },
        {
            "service_code": "0120",
            "guideline_id": "LCD-REV-0120",
            "guideline_version": "2026.1",
            "guideline_title": "Inpatient Room and Board - Semi-Private Two Bed",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-REV-01", "description": "Documented formal physician inpatient admission order and continuous nursing care", "is_mandatory": True},
            ],
        },
        {
            "service_code": "33533",
            "guideline_id": "LCD-33533-01",
            "guideline_version": "2026.1",
            "guideline_title": "Coronary Artery Bypass Graft (CABG)",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-33533-01", "description": "Documented coronary artery disease, multivessel disease, or angina pectoris", "is_mandatory": True},
            ],
        },
        {
            "service_code": "36415",
            "guideline_id": "LCD-36415-01",
            "guideline_version": "2026.1",
            "guideline_title": "Routine Venipuncture for Laboratory Specimen Collection",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-36415-01", "description": "Documented physician order for blood specimen or diagnostic laboratory workup", "is_mandatory": True},
            ],
        },
        {
            "service_code": "43239",
            "guideline_id": "LCD-43239-01",
            "guideline_version": "2026.1",
            "guideline_title": "Esophagogastroduodenoscopy (EGD) with Biopsy",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-43239-01", "description": "Documented dyspepsia, GERD, abdominal pain, gastrointestinal bleeding, or ulcer evaluation", "is_mandatory": True},
            ],
        },
        {
            "service_code": "90837",
            "guideline_id": "LCD-90837-01",
            "guideline_version": "2026.1",
            "guideline_title": "Psychotherapy Services - 60 Minutes",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-90837-01", "description": "Documented psychiatric evaluation, depressive disorder, anxiety, or behavioral health management", "is_mandatory": True},
            ],
        },
        {
            "service_code": "71046",
            "guideline_id": "LCD-71046-01",
            "guideline_version": "2026.1",
            "guideline_title": "Chest Radiography Two Views",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-71046-01", "description": "Documented respiratory symptoms, pulmonary evaluation, or chest trauma", "is_mandatory": True},
            ],
        },
        {
            "service_code": "93306",
            "guideline_id": "LCD-93306-01",
            "guideline_version": "2026.1",
            "guideline_title": "Transthoracic Echocardiography with Doppler",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-93306-01", "description": "Documented cardiac symptoms, murmur, heart failure, or ischemic heart disease assessment", "is_mandatory": True},
            ],
        },
        {
            "service_code": "99215",
            "guideline_id": "LCD-99215-01",
            "guideline_version": "2026.1",
            "guideline_title": "Evaluation and Management Services - Level 5 Established Patient",
            "effective_date": date(2025, 1, 1),
            "requires_prior_auth": False,
            "criteria": [
                {"criterion_id": "CRIT-99215-01", "description": "Documented high complexity medical decision making or >= 40 minutes visit time", "is_mandatory": True},
            ],
        },
    ]

    for g_def in guideline_definitions:
        g_id = g_def["guideline_id"]
        if g_id in existing_guideline_ids:
            continue

        g_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"guideline_{g_id}")
        guideline = ClinicalGuideline(
            id=g_uuid,
            guideline_id=g_id,
            service_code=g_def["service_code"],
            guideline_version=g_def.get("guideline_version", "2026.1"),
            guideline_title=g_def["guideline_title"],
            effective_date=g_def["effective_date"],
            expiration_date=g_def.get("expiration_date"),
            requires_prior_auth=g_def.get("requires_prior_auth", False),
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(guideline)
        existing_guideline_ids.add(g_id)
        seeded_guidelines += 1

        for c_def in g_def["criteria"]:
            c_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"crit_{g_id}_{c_def['criterion_id']}")
            crit = GuidelineCriterion(
                id=c_uuid,
                guideline_id=g_uuid,
                criterion_id=c_def["criterion_id"],
                description=c_def["description"],
                is_mandatory=c_def.get("is_mandatory", True),
                created_at=now,
            )
            session.add(crit)

    # 2. 9 Authoritative Diagnosis-to-Procedure Indications & Anatomical Rules
    indication_definitions: list[dict[str, Any]] = [
        {
            "service_code": "27447",
            "procedure_name": "Total Knee Arthroplasty (TKA)",
            "anatomy": "Knee Joint",
            "valid_icd_prefixes": ["M17", "M25.56", "M23", "M84.56", "M05", "M06"],
            "contraindicated_prefixes": ["S52", "S62", "F32", "F33", "J01"],
            "keywords": ["knee", "osteoarthritis", "arthroplasty", "joint disease", "gonarthrosis", "condyle", "m17"],
        },
        {
            "service_code": "44970",
            "procedure_name": "Laparoscopic Appendectomy",
            "anatomy": "Appendix / Lower Right Abdomen",
            "valid_icd_prefixes": ["K35", "K36", "K37", "R10.31"],
            "contraindicated_prefixes": ["F32", "F33", "F41", "S52", "S62", "M17"],
            "keywords": ["appendectomy", "appendix", "appendicitis", "lower right abdominal", "rlq", "k35"],
        },
        {
            "service_code": "47562",
            "procedure_name": "Laparoscopic Cholecystectomy",
            "anatomy": "Gallbladder / Biliary Tract",
            "valid_icd_prefixes": ["K80", "K81", "K82", "R10.13"],
            "contraindicated_prefixes": ["F32", "F33", "J01", "S52"],
            "keywords": ["cholecystectomy", "cholecystitis", "gallbladder", "cholelithiasis", "biliary", "k80", "k81"],
        },
        {
            "service_code": "33533",
            "procedure_name": "Coronary Artery Bypass Graft (CABG)",
            "anatomy": "Coronary Arteries / Cardiovascular",
            "valid_icd_prefixes": ["I25", "I20", "I21"],
            "contraindicated_prefixes": ["S52", "F32", "M17"],
            "keywords": ["coronary", "cad", "angina", "myocardial", "ischemic heart", "bypass", "i25"],
        },
        {
            "service_code": "43239",
            "procedure_name": "Esophagogastroduodenoscopy (EGD) with Biopsy",
            "anatomy": "Upper Gastrointestinal Tract / Esophagus / Stomach",
            "valid_icd_prefixes": ["K21", "K29", "K25", "K26", "K27", "R10", "K92", "K30"],
            "contraindicated_prefixes": ["S52", "M17"],
            "keywords": ["endoscopy", "egd", "gastritis", "dyspepsia", "gerd", "ulcer", "upper gi", "biopsy", "k21"],
        },
        {
            "service_code": "90837",
            "procedure_name": "Psychotherapy 60 min",
            "anatomy": "Mental / Behavioral Health",
            "valid_icd_prefixes": ["F32", "F33", "F41", "F43", "F31", "F10", "F90"],
            "contraindicated_prefixes": ["S52", "M17"],
            "keywords": ["psychotherapy", "depression", "depressive", "anxiety", "mental health", "behavioral", "f32"],
        },
        {
            "service_code": "93306",
            "procedure_name": "Transthoracic Echocardiography",
            "anatomy": "Heart / Cardiovascular",
            "valid_icd_prefixes": ["I25", "I50", "I20", "I21", "I48", "I34", "I35", "R00", "R01", "R07"],
            "contraindicated_prefixes": ["S52", "M17"],
            "keywords": ["echocardiography", "echo", "heart failure", "murmur", "doppler"],
        },
        {
            "service_code": "36415",
            "procedure_name": "Venipuncture for Specimen Collection",
            "anatomy": "Venous System / Diagnostic Workup",
            "valid_icd_prefixes": ["I", "E", "K", "J", "M", "N", "R", "F", "S", "G"],
            "contraindicated_prefixes": [],
            "keywords": ["venipuncture", "blood draw", "lab specimen"],
        },
        {
            "service_code": "71046",
            "procedure_name": "Chest Radiography 2 Views",
            "anatomy": "Thorax / Lungs",
            "valid_icd_prefixes": ["J", "R05", "R06", "R07", "I25", "I50"],
            "contraindicated_prefixes": [],
            "keywords": ["x-ray", "chest", "lungs", "radiography"],
        },
    ]

    for ind_def in indication_definitions:
        sc = ind_def["service_code"]
        if sc in existing_indications:
            continue

        ind_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"indication_{sc}")
        ind = DiagnosisIndication(
            id=ind_uuid,
            service_code=sc,
            procedure_name=ind_def["procedure_name"],
            anatomy=ind_def["anatomy"],
            valid_icd_prefixes=ind_def["valid_icd_prefixes"],
            contraindicated_prefixes=ind_def["contraindicated_prefixes"],
            keywords=ind_def["keywords"],
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(ind)
        existing_indications.add(sc)
        seeded_indications += 1

    session.flush()
    logger.info("Successfully seeded %d clinical guidelines and %d diagnosis indications", seeded_guidelines, seeded_indications)
    return seeded_guidelines, seeded_indications

# Baseline default test members (preserving full backwards compatibility with unit tests)
BASELINE_MEMBERS: list[dict[str, Any]] = [
    {
        "member_id": "MEM-12345",
        "first_name": "Alice",
        "last_name": "Smith",
        "dob": "05/20/1985",
        "ssn_last4": "4321",
        "policy_id": "POL-CORP-2026",
    },
    {
        "member_id": "MED-GLD-3592974",
        "first_name": "John",
        "last_name": "Johnson",
        "dob": "11/08/1958",
        "ssn_last4": "7658",
        "policy_id": "MED-GLD-3592974",
    },
    {
        "member_id": "PT_15075",
        "first_name": "John",
        "last_name": "Johnson",
        "dob": "11/08/1958",
        "ssn_last4": "7658",
        "policy_id": "MED-GLD-3592974",
    },
    {
        "member_id": "MSH-EN-004",
        "first_name": "Kavita",
        "last_name": "Rao",
        "dob": "04/18/1992",
        "ssn_last4": "0004",
        "policy_id": "POL-CORP-2026",
    },
]


def _load_clusters_from_generator() -> list[dict[str, Any]]:
    """Loads 30 benchmark patient clusters from canonical scripts.scripts.generate_docs."""
    try:
        import sys
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        scripts_dir = repo_root / "scripts" / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

        import random

        import generate_docs

        generate_docs.rng = random.Random(42)
        clusters, _ = generate_docs.generate_patient_clusters(30)
        return list(clusters)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not dynamically import generate_patient_clusters: %s; using static fallback", exc)
        return []


def _load_gold_policy_rider_terms() -> list[tuple[Any, ...]]:
    """Loads the shared Gold policy rider catalog as relational seed input."""
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    catalog_path = repo_root / "data" / "config" / "policy_riders_gold.json"
    with catalog_path.open(encoding="utf-8") as catalog_file:
        return json.load(catalog_file)


def _seed_policy_riders(
    session: Session,
    policy_version_id: uuid.UUID,
    policy_code: str,
    rider_terms: list[dict[str, Any]],
) -> dict[str, uuid.UUID]:
    """Upserts generator-defined rider terms for a policy version."""
    policy_rider_ids: dict[str, uuid.UUID] = {}
    for term in rider_terms:
        rider_code = term["rider_code"]
        section_number = term["section_number"]
        rider_id = uuid.uuid5(
            uuid.NAMESPACE_DNS, f"policy_rider_{policy_code}_{rider_code}"
        )
        rider = session.query(PolicyRider).filter_by(id=rider_id).first()
        if rider is None:
            source_text = (
                f"{section_number} {term['name']}. "
                f"Benefit limit: {term['benefit_limit_text']}. "
                f"Waiting period: {term['waiting_period_text']}. "
                f"Covered services: {' '.join(term['covered_services'])} "
                f"Exclusions: {' '.join(term['exclusions'])}"
            )
            rider = PolicyRider(
                id=rider_id,
                policy_version_id=policy_version_id,
                rider_code=rider_code,
                name=term["name"],
                waiting_period_value=term["waiting_period_value"],
                waiting_period_unit=term["waiting_period_unit"],
                waiting_period_start_event=term["waiting_period_start_event"],
                benefit_limit_text=term["benefit_limit_text"],
                covered_services=term["covered_services"],
                exclusions=term["exclusions"],
                source_section=section_number,
                source_page=6,
                source_pdf_key="gold_policy/medishield_gold_plan.pdf",
                source_text=source_text,
                source_verified=False,
            )
            session.add(rider)
        policy_rider_ids[rider_code] = rider_id
    return policy_rider_ids


# Fallback static definition for all 30 benchmark clusters if Pillow/generator unavailable
FALLBACK_CLUSTERS: list[dict[str, Any]] = [
    {"cluster_id": "C_001", "patient_id": "PT_19116", "name": "Christopher Harris I.", "dob": "04/25/1954", "ssn": "489-22-6881", "policy": "MED-GLD-6770619"},
    {"cluster_id": "C_002", "patient_id": "PT_99733", "name": "Jessica Jackson Y.", "dob": "02/28/1963", "ssn": "384-68-6977", "policy": "MED-GLD-3728882"},
    {"cluster_id": "C_003", "patient_id": "PT_62350", "name": "Patricia Miller H.", "dob": "01/26/1997", "ssn": "790-95-5858", "policy": "MED-GLD-4567281"},
    {"cluster_id": "C_004", "patient_id": "PT_69470", "name": "Elizabeth Taylor Q.", "dob": "02/25/1976", "ssn": "118-24-9694", "policy": "MED-GLD-2065818"},
    {"cluster_id": "C_005", "patient_id": "PT_20322", "name": "Sarah White Q.", "dob": "03/17/1993", "ssn": "173-67-2708", "policy": "MED-GLD-3564217"},
    {"cluster_id": "C_006", "patient_id": "PT_39451", "name": "Sandra Hall C.", "dob": "09/25/1993", "ssn": "480-14-1629", "policy": "MED-GLD-5446912"},
    {"cluster_id": "C_007", "patient_id": "PT_71993", "name": "Mary Davis S.", "dob": "04/19/1980", "ssn": "235-98-3567", "policy": "MED-GLD-4841005"},
    {"cluster_id": "C_008", "patient_id": "PT_82132", "name": "Richard Martinez D.", "dob": "11/14/1951", "ssn": "887-16-6466", "policy": "MED-GLD-2016911"},
    {"cluster_id": "C_009", "patient_id": "PT_17665", "name": "Mary Davis C.", "dob": "04/06/1993", "ssn": "732-23-4245", "policy": "MED-GLD-3762152"},
    {"cluster_id": "C_010", "patient_id": "PT_20745", "name": "John Johnson X.", "dob": "01/02/1965", "ssn": "214-72-8706", "policy": "MED-GLD-1954280"},
    {"cluster_id": "C_011", "patient_id": "PT_47353", "name": "Nancy Martin I.", "dob": "11/23/1958", "ssn": "339-50-8438", "policy": "MED-GLD-8670955"},
    {"cluster_id": "C_012", "patient_id": "PT_16658", "name": "Karen Harris T.", "dob": "11/17/1996", "ssn": "779-23-3200", "policy": "MED-GLD-5437001"},
    {"cluster_id": "C_013", "patient_id": "PT_57795", "name": "Betty Clark I.", "dob": "01/11/1947", "ssn": "368-30-8239", "policy": "MED-GLD-8175395"},
    {"cluster_id": "C_014", "patient_id": "PT_15075", "name": "John Johnson L.", "dob": "11/08/1958", "ssn": "153-27-7658", "policy": "MED-GLD-3592974"},
    {"cluster_id": "C_015", "patient_id": "PT_24208", "name": "Joseph Anderson G.", "dob": "08/12/1997", "ssn": "136-16-8692", "policy": "MED-GLD-7685149"},
    {"cluster_id": "C_016", "patient_id": "PT_50538", "name": "Daniel Taylor X.", "dob": "06/14/1995", "ssn": "802-99-9730", "policy": "MED-GLD-1744682"},
    {"cluster_id": "C_017", "patient_id": "PT_72021", "name": "Christopher Harris M.", "dob": "05/18/1989", "ssn": "917-80-7798", "policy": "MED-GLD-6049101"},
    {"cluster_id": "C_018", "patient_id": "PT_62383", "name": "David Garcia V.", "dob": "05/17/1950", "ssn": "165-27-2481", "policy": "MED-GLD-4768844"},
    {"cluster_id": "C_019", "patient_id": "PT_81810", "name": "Mary Davis Y.", "dob": "02/25/2000", "ssn": "875-98-3806", "policy": "MED-GLD-8793853"},
    {"cluster_id": "C_020", "patient_id": "PT_30028", "name": "Barbara Anderson X.", "dob": "08/15/2000", "ssn": "219-48-9366", "policy": "MED-GLD-5013879"},
    {"cluster_id": "C_021", "patient_id": "PT_67125", "name": "Robert Williams N.", "dob": "06/18/1971", "ssn": "161-55-9345", "policy": "MED-GLD-1327691"},
    {"cluster_id": "C_022", "patient_id": "PT_54236", "name": "Margaret Lee K.", "dob": "11/26/1987", "ssn": "173-16-8290", "policy": "MED-GLD-1452421"},
    {"cluster_id": "C_023", "patient_id": "PT_24993", "name": "Anthony Moore I.", "dob": "07/09/1993", "ssn": "798-50-6819", "policy": "MED-GLD-6871360"},
    {"cluster_id": "C_024", "patient_id": "PT_99687", "name": "Richard Martinez O.", "dob": "05/25/1989", "ssn": "578-83-5002", "policy": "MED-GLD-6218367"},
    {"cluster_id": "C_025", "patient_id": "PT_45400", "name": "Michael Brown Z.", "dob": "01/12/1981", "ssn": "761-94-6886", "policy": "MED-GLD-6724909"},
    {"cluster_id": "C_026", "patient_id": "PT_88139", "name": "Paul Walker O.", "dob": "12/19/1972", "ssn": "208-86-6398", "policy": "MED-GLD-8563202"},
    {"cluster_id": "C_027", "patient_id": "PT_55056", "name": "Thomas Jackson R.", "dob": "09/07/1945", "ssn": "168-96-8208", "policy": "MED-GLD-8987516"},
    {"cluster_id": "C_028", "patient_id": "PT_39816", "name": "Nancy Martin L.", "dob": "08/09/1989", "ssn": "726-57-5507", "policy": "MED-GLD-3005795"},
    {"cluster_id": "C_029", "patient_id": "PT_74454", "name": "Karen Harris A.", "dob": "09/05/1990", "ssn": "439-86-7788", "policy": "MED-GLD-9235577"},
    {"cluster_id": "C_030", "patient_id": "PT_41439", "name": "Patricia Miller E.", "dob": "10/10/1954", "ssn": "394-42-5369", "policy": "MED-GLD-9767312"},
]


def _parse_dob(dob_str: str) -> date:
    """Parses mm/dd/yyyy format to date object."""
    try:
        parts = dob_str.split("/")
        if len(parts) == 3:
            return date(int(parts[2]), int(parts[0]), int(parts[1]))
        return date(1980, 1, 1)
    except (ValueError, IndexError):
        return date(1980, 1, 1)


def seed_benchmark_dataset(session: Session) -> int:
    """
    Seeds all 30 benchmark patients, policies, enrollments, clauses, and medical terminology into the database.
    Returns the number of seeded members. Idempotent: checks for existence before adding.
    """
    seed_medical_codes(session)
    seed_clinical_guidelines_and_indications(session)
    seeded_count = 0
    seen_member_ids: set[str] = {
        row[0].upper() for row in session.query(Member.member_id).all()
    }
    seen_policy_ids: set[str] = {
        row[0].upper() for row in session.query(Policy.policy_id).all()
    }
    policy_version_map: dict[str, uuid.UUID] = {}
    for pv in session.query(PolicyVersion).all():
        if pv.policy:
            policy_version_map[pv.policy.policy_id.upper()] = pv.id

    # 1. Load dynamic or fallback clusters
    clusters = _load_clusters_from_generator()
    if not clusters:
        clusters = FALLBACK_CLUSTERS
    rider_terms = _load_gold_policy_rider_terms()

    # 2. Seed Baseline Members
    for b in BASELINE_MEMBERS:
        m_id_str = b["member_id"].upper()
        if m_id_str in seen_member_ids:
            continue
        seen_member_ids.add(m_id_str)

        m_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"member_{b['member_id']}")
        m = Member(
            id=m_id,
            member_id=b["member_id"],
            first_name=b["first_name"],
            last_name=b["last_name"],
            date_of_birth=_parse_dob(b["dob"]),
            ssn_last4=b["ssn_last4"],
            status="ACTIVE",
        )
        session.add(m)
        seeded_count += 1

        # Seed policy if needed
        pol_id_str = b["policy_id"]
        pol_clean = pol_id_str.upper()
        if pol_clean not in seen_policy_ids:
            seen_policy_ids.add(pol_clean)
            pol_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"policy_{pol_id_str}")
            pol = Policy(
                id=pol_uuid,
                policy_id=pol_id_str,
                plan_name="Corporate Master Health Plan",
                policy_type="COMPREHENSIVE",
                is_active=True,
            )
            session.add(pol)

            pv_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"pv_{pol_id_str}_v1.0")
            pv = PolicyVersion(
                id=pv_uuid,
                policy_id=pol_uuid,
                version_tag="v1.0",
                effective_start_date=date(2025, 1, 1),
                effective_end_date=date(2026, 12, 31),
                source_pdf_key=f"policies/{pol_id_str}.pdf",
                vector_collection_name=f"policy_{pol_id_str.lower().replace('-', '_')}",
                is_published=True,
            )
            session.add(pv)
            policy_version_map[pol_clean] = pv_uuid

            # Link enrollment
            enr_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"enr_{b['member_id']}_{pol_id_str}")
            enr = Enrollment(
                id=enr_uuid,
                member_id=m_id,
                policy_version_id=pv_uuid,
                coverage_start_date=date(2025, 1, 1),
                coverage_end_date=date(2026, 12, 31),
                status="ACTIVE",
            )
            session.add(enr)
        else:
            matched_pv_uuid = policy_version_map.get(pol_clean)
            if matched_pv_uuid:
                enr_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"enr_{b['member_id']}_{pol_id_str}")
                enr = Enrollment(
                    id=enr_uuid,
                    member_id=m_id,
                    policy_version_id=matched_pv_uuid,
                    coverage_start_date=date(2025, 1, 1),
                    coverage_end_date=date(2026, 12, 31),
                    status="ACTIVE",
                )
                session.add(enr)

    # 3. Seed 30 Benchmark Patient Clusters
    for c in clusters:
        patient_id = c["patient_id"]
        pid_clean = patient_id.upper()
        if pid_clean in seen_member_ids:
            continue
        seen_member_ids.add(pid_clean)

        raw_name = c["name"].strip()
        name_parts = raw_name.split()
        first_name = name_parts[0] if name_parts else "Patient"
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else "Unknown"

        ssn_raw = c.get("ssn", "000-00-0000")
        ssn_last4 = ssn_raw.split("-")[-1] if "-" in ssn_raw else ssn_raw[-4:]

        m_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"member_{patient_id}")
        member = Member(
            id=m_uuid,
            member_id=patient_id,
            first_name=first_name,
            last_name=last_name,
            date_of_birth=_parse_dob(c["dob"]),
            ssn_last4=ssn_last4,
            status="ACTIVE",
        )
        session.add(member)
        seeded_count += 1

        # Seed Policy for patient
        policy_code = c["policy"]
        pol_clean = policy_code.upper()
        if pol_clean not in seen_policy_ids:
            seen_policy_ids.add(pol_clean)
            pol_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"policy_{policy_code}")
            pol = Policy(
                id=pol_uuid,
                policy_id=policy_code,
                plan_name="Gold Comprehensive Healthcare Plan",
                policy_type="COMPREHENSIVE",
                is_active=True,
            )
            session.add(pol)

            pv_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"pv_{policy_code}_v1.0")
            pv = PolicyVersion(
                id=pv_uuid,
                policy_id=pol_uuid,
                version_tag="v1.0",
                effective_start_date=date(2025, 1, 1),
                effective_end_date=date(2026, 12, 31),
                source_pdf_key=f"policies/{policy_code}.pdf",
                vector_collection_name=f"policy_{policy_code.lower().replace('-', '_')}",
                is_published=True,
            )
            session.add(pv)

            # Seed standard Gold series policy chunks for this policy version
            sec31_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"chk_sec31_{policy_code}")
            sec31 = PolicyChunk(
                id=sec31_uuid,
                policy_version_id=pv_uuid,
                chunk_index=0,
                section_number="SEC-3.1",
                section_title="Outpatient Evaluation and Management",
                chunk_text=(
                    "Outpatient office visits, endoscopic evaluation, and routine diagnostic evaluations are covered benefits. "
                    "The insurer pays 80% of allowed charges after the annual deductible of $500.00 is satisfied. "
                    "Co-payment of 20% applies to in-network professional services."
                ),
                page_number=12,
                vector_id=f"vec_sec31_{policy_code}",
            )
            session.add(sec31)

            sec54_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"chk_sec54_{policy_code}")
            sec54 = PolicyChunk(
                id=sec54_uuid,
                policy_version_id=pv_uuid,
                chunk_index=1,
                section_number="SEC-5.4",
                section_title="Inpatient Surgical and Major Procedures",
                chunk_text=(
                    "Major surgical procedures including laparoscopic cholecystectomy, knee arthroplasty, and esophagogastroduodenoscopy "
                    "require prior authorization submitted at least 48 hours in advance. Covered at 90% after deductible when prior authorization is approved."
                ),
                page_number=25,
                vector_id=f"vec_sec54_{policy_code}",
            )
            session.add(sec54)

            sec92_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"chk_sec92_{policy_code}")
            sec92 = PolicyChunk(
                id=sec92_uuid,
                policy_version_id=pv_uuid,
                chunk_index=2,
                section_number="SEC-9.2",
                section_title="Cosmetic and Elective Resurfacing Exclusion",
                chunk_text=(
                    "Cosmetic surgery, dermabrasion, and elective non-reconstructive procedures are strictly excluded "
                    "from policy coverage. No benefit is payable for services billed under CPT 15780, 21120, or 17000."
                ),
                page_number=44,
                vector_id=f"vec_sec92_{policy_code}",
            )
            session.add(sec92)

            policy_version_map[pol_clean] = pv_uuid

            enr_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"enr_{patient_id}_{policy_code}")
            enr = Enrollment(
                id=enr_uuid,
                member_id=m_uuid,
                policy_version_id=pv_uuid,
                coverage_start_date=date(2025, 1, 1),
                coverage_end_date=date(2026, 12, 31),
                status="ACTIVE",
            )
            session.add(enr)
        else:
            fallback_pv_uuid = policy_version_map.get(pol_clean)
            if fallback_pv_uuid:
                enr_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"enr_{patient_id}_{policy_code}")
                enr = Enrollment(
                    id=enr_uuid,
                    member_id=m_uuid,
                    policy_version_id=fallback_pv_uuid,
                    coverage_start_date=date(2025, 1, 1),
                    coverage_end_date=date(2026, 12, 31),
                    status="ACTIVE",
                )
                session.add(enr)

    # Backfill rider catalog and pending applications for both new and existing seed rows.
    for c in clusters:
        patient_id = c["patient_id"]
        member = session.query(Member).filter_by(member_id=patient_id).first()
        policy_code = c["policy"]
        policy_version_id = policy_version_map.get(policy_code.upper())
        if member is None or policy_version_id is None:
            continue

        enrollment = (
            session.query(Enrollment)
            .filter_by(member_id=member.id, policy_version_id=policy_version_id)
            .first()
        )
        if enrollment is None:
            continue

        policy_rider_ids = _seed_policy_riders(
            session, policy_version_id, policy_code, rider_terms
        )
        rider_request_code = c.get("rider_request_code")
        policy_rider_id = policy_rider_ids.get(rider_request_code)
        if not rider_request_code or not policy_rider_id:
            continue

        application_id = uuid.uuid5(
            uuid.NAMESPACE_DNS, f"rider_application_{patient_id}_{rider_request_code}"
        )
        if session.query(EnrollmentRiderApplication).filter_by(id=application_id).first() is not None:
            continue

        requested_on_value = c.get("treatment_date")
        requested_on = (
            requested_on_value.date()
            if isinstance(requested_on_value, datetime)
            else requested_on_value
        )
        session.add(
            EnrollmentRiderApplication(
                id=application_id,
                enrollment_id=enrollment.id,
                policy_rider_id=policy_rider_id,
                status="REQUESTED",
                requested_on=requested_on,
                requested_effective_date=requested_on,
                source_document_ref=f"amend_{patient_id}",
            )
        )

    session.commit()
    logger.info("Successfully seeded %d benchmark members into database", seeded_count)
    return seeded_count


def seed_demo_cases_if_empty(session: Session) -> int:
    """Seeds initial demonstration cases into SQLite if cases table is empty (ADR-007, ADR-008)."""
    from datetime import UTC
    from decimal import Decimal

    from medishield.db.enums import (
        ActorType,
        BusinessDecision,
        DocumentType,
        EscalationSubReason,
        WorkflowState,
    )
    from medishield.db.models import Case, Decision, Document

    if session.query(Case).count() > 0:
        return 0

    now = datetime.now(UTC)
    c1_id = uuid.uuid4()
    c1 = Case(
        id=c1_id,
        case_id="CASE-20261004-902144",
        workflow_state=WorkflowState.AWAITING_HUMAN,
        workflow_version="v1.0.0",
        current_decision=BusinessDecision.ESCALATE,
        current_sub_reason=EscalationSubReason.FRAUD_REVIEW,
        decision_confidence=Decimal("0.88"),
        is_complete=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    d1 = Document(
        id=uuid.uuid4(),
        document_id="DOC-902144-01",
        case_id=c1_id,
        file_name="hospital_discharge_summary.pdf",
        object_key="raw/CASE-20261004-902144/DOC-902144-01.pdf",
        mime_type="application/pdf",
        checksum="a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
        size_bytes=245000,
        doc_type=DocumentType.DISCHARGE_SUMMARY,
        created_at=now,
    )
    d2 = Document(
        id=uuid.uuid4(),
        document_id="DOC-902144-02",
        case_id=c1_id,
        file_name="itemized_claim_form.pdf",
        object_key="raw/CASE-20261004-902144/DOC-902144-02.pdf",
        mime_type="application/pdf",
        checksum="b2c3d4e5f6a10718293a4b5c6d7e8f90123456789abcdef0123456789abcdef1",
        size_bytes=180000,
        doc_type=DocumentType.CLAIM_FORM,
        created_at=now,
    )
    dec1 = Decision(
        id=uuid.uuid4(),
        case_id=c1_id,
        decision=BusinessDecision.ESCALATE,
        escalation_sub_reason=EscalationSubReason.FRAUD_REVIEW,
        actor_type=ActorType.SYSTEM,
        actor_id="system-decision-engine",
        decision_confidence=Decimal("0.88"),
        rule_id_matched="RULE-P4-HIGH-FRAUD",
        justification="Fraud Escalation (P4): High fraud risk score (0.88 >= 0.70). Anomalies: Duplicate billing detected across providers.",
        agent_summary_snapshot={
            "fraud": {
                "verdict": "FAIL",
                "confidence": 0.88,
                "fraud_score": 0.88,
                "risk_level": "HIGH",
                "anomalies": ["Duplicate billing detected across providers"],
            }
        },
        case_version_at_decision=1,
        is_active=True,
        created_at=now,
    )
    session.add_all([c1, d1, d2, dec1])

    c2_id = uuid.uuid4()
    c2 = Case(
        id=c2_id,
        case_id="CASE-20261004-811092",
        workflow_state=WorkflowState.COMPLETED,
        workflow_version="v1.0.0",
        current_decision=BusinessDecision.APPROVE,
        decision_confidence=Decimal("0.99"),
        is_complete=True,
        version=1,
        created_at=now,
        updated_at=now,
    )
    d3 = Document(
        id=uuid.uuid4(),
        document_id="DOC-811092-01",
        case_id=c2_id,
        file_name="pharmacy_prescription.png",
        object_key="raw/CASE-20261004-811092/DOC-811092-01.png",
        mime_type="image/png",
        checksum="c3d4e5f6a1b20718293a4b5c6d7e8f90123456789abcdef0123456789abcdef2",
        size_bytes=85000,
        doc_type=DocumentType.PRESCRIPTION,
        created_at=now,
    )
    dec2 = Decision(
        id=uuid.uuid4(),
        case_id=c2_id,
        decision=BusinessDecision.APPROVE,
        escalation_sub_reason=None,
        actor_type=ActorType.SYSTEM,
        actor_id="system-decision-engine",
        decision_confidence=Decimal("0.99"),
        rule_id_matched="RULE-P6-CLEAN-PASS",
        justification="Clean Straight-Through Pass (P6): All identity, claims arithmetic, and policy coverage criteria passed with low fraud risk.",
        agent_summary_snapshot={
            "fraud": {
                "verdict": "PASS",
                "confidence": 0.99,
                "fraud_score": 0.05,
                "risk_level": "LOW",
                "anomalies": [],
            }
        },
        case_version_at_decision=1,
        is_active=True,
        created_at=now,
    )
    session.add_all([c2, d3, dec2])
    session.commit()
    logger.info("Successfully seeded 2 demo cases into database")
    return 2


def seed_benchmark_cluster_cases(session: Session) -> int:
    """Seeds representative cases from the 30-cluster dataset into the database."""
    from datetime import UTC
    from decimal import Decimal

    from medishield.db.enums import (
        ActorType,
        BusinessDecision,
        DocumentType,
        EscalationSubReason,
        WorkflowState,
    )
    from medishield.db.models import Case, Decision, Document

    existing_case_ids = {c[0] for c in session.query(Case.case_id).all()}

    now = datetime.now(UTC)
    seeded = 0

    benchmark_cases: list[dict[str, Any]] = [
        {
            "case_id": "CASE-C001-PT19116",
            "state": WorkflowState.COMPLETED,
            "decision": BusinessDecision.APPROVE,
            "sub_reason": None,
            "confidence": Decimal("0.99"),
            "rule": "RULE-P6-CLEAN-PASS",
            "justification": "Clean Straight-Through Pass (P6): Identity, hospital claim arithmetic ($1,250.00), and policy coverage verified with zero fraud flags.",
            "fraud": {"verdict": "PASS", "confidence": 0.99, "fraud_score": 0.05, "risk_level": "LOW", "anomalies": []},
            "docs": [
                ("claim_PT_19116.png", DocumentType.CLAIM_FORM, 185000),
                ("id_PT_19116.png", DocumentType.ID_DOCUMENT, 92000),
                ("discharge_PT_19116.png", DocumentType.DISCHARGE_SUMMARY, 210000),
                ("rx_PT_19116.png", DocumentType.PRESCRIPTION, 88000),
                ("amend_PT_19116.png", DocumentType.POLICY_AMENDMENT, 95000),
            ],
        },
        {
            "case_id": "CASE-C005-PT20322",
            "state": WorkflowState.AWAITING_HUMAN,
            "decision": BusinessDecision.ESCALATE,
            "sub_reason": EscalationSubReason.FRAUD_REVIEW,
            "confidence": Decimal("0.85"),
            "rule": "RULE-P4-HIGH-FRAUD",
            "justification": "Fraud Escalation (P4): Duplicate claim submission collision detected with prior service episode in active policy window.",
            "fraud": {"verdict": "FAIL", "confidence": 0.85, "fraud_score": 0.85, "risk_level": "HIGH", "anomalies": ["Duplicate claim hash collision with active billing episode"]},
            "docs": [
                ("claim_PT_20322.png", DocumentType.CLAIM_FORM, 172000),
                ("id_PT_20322.png", DocumentType.ID_DOCUMENT, 91000),
                ("discharge_PT_20322.png", DocumentType.DISCHARGE_SUMMARY, 198000),
            ],
        },
        {
            "case_id": "CASE-C012-PT16658",
            "state": WorkflowState.COMPLETED,
            "decision": BusinessDecision.APPROVE,
            "sub_reason": None,
            "confidence": Decimal("0.98"),
            "rule": "RULE-P6-CLEAN-PASS",
            "justification": "Clean Straight-Through Pass (P6): Orthopedic surgical pre-authorization matched policy rider MED-GLD-5437001.",
            "fraud": {"verdict": "PASS", "confidence": 0.98, "fraud_score": 0.04, "risk_level": "LOW", "anomalies": []},
            "docs": [
                ("claim_PT_16658.png", DocumentType.CLAIM_FORM, 190000),
                ("id_PT_16658.png", DocumentType.ID_DOCUMENT, 89000),
                ("discharge_PT_16658.png", DocumentType.DISCHARGE_SUMMARY, 220000),
                ("rx_PT_16658.png", DocumentType.PRESCRIPTION, 84000),
            ],
        },
        {
            "case_id": "CASE-C014-PT15075",
            "state": WorkflowState.AWAITING_INFO,
            "decision": BusinessDecision.ESCALATE,
            "sub_reason": EscalationSubReason.MISSING_INFORMATION,
            "confidence": Decimal("0.72"),
            "rule": "RULE-P2-MISSING-MANDATORY-DOCS",
            "justification": "Awaiting Supplementary Documentation: Hospital discharge summary required to substantiate inpatient diagnosis code.",
            "fraud": {"verdict": "PASS", "confidence": 0.90, "fraud_score": 0.10, "risk_level": "LOW", "anomalies": []},
            "docs": [
                ("claim_PT_15075.png", DocumentType.CLAIM_FORM, 165000),
                ("id_PT_15075.png", DocumentType.ID_DOCUMENT, 90000),
            ],
        },
        {
            "case_id": "CASE-C002-PT99733",
            "state": WorkflowState.COMPLETED,
            "decision": BusinessDecision.APPROVE,
            "sub_reason": None,
            "confidence": Decimal("0.99"),
            "rule": "RULE-P6-CLEAN-PASS",
            "justification": "Clean Straight-Through Pass (P6): Outpatient emergency care claim validated against policy MED-GLD-3728882.",
            "fraud": {"verdict": "PASS", "confidence": 0.99, "fraud_score": 0.03, "risk_level": "LOW", "anomalies": []},
            "docs": [
                ("claim_PT_99733.png", DocumentType.CLAIM_FORM, 178000),
                ("id_PT_99733.png", DocumentType.ID_DOCUMENT, 93000),
                ("rx_PT_99733.png", DocumentType.PRESCRIPTION, 81000),
            ],
        },
    ]

    for bc in benchmark_cases:
        if bc["case_id"] in existing_case_ids:
            continue

        c_id = uuid.uuid4()
        c = Case(
            id=c_id,
            case_id=bc["case_id"],
            workflow_state=bc["state"],
            workflow_version="v1.0.0",
            current_decision=bc["decision"],
            current_sub_reason=bc["sub_reason"],
            decision_confidence=bc["confidence"],
            is_complete=(bc["state"] == WorkflowState.COMPLETED),
            missing_documents=["DISCHARGE_SUMMARY"] if bc["state"] == WorkflowState.AWAITING_INFO else [],
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(c)

        for fname, dtype, size in bc["docs"]:
            d_id = uuid.uuid4()
            d = Document(
                id=d_id,
                document_id=f"DOC-{fname.replace('.png', '')}",
                case_id=c_id,
                file_name=fname,
                object_key=f"raw/{bc['case_id']}/{fname}",
                mime_type="image/png",
                checksum=uuid.uuid5(uuid.NAMESPACE_DNS, fname).hex,
                size_bytes=size,
                doc_type=dtype,
                created_at=now,
            )
            session.add(d)

        dec_id = uuid.uuid4()
        dec = Decision(
            id=dec_id,
            case_id=c_id,
            decision=bc["decision"],
            escalation_sub_reason=bc["sub_reason"],
            actor_type=ActorType.SYSTEM,
            actor_id="system-decision-engine",
            decision_confidence=bc["confidence"],
            rule_id_matched=bc["rule"],
            justification=bc["justification"],
            agent_summary_snapshot={"fraud": bc["fraud"]},
            case_version_at_decision=1,
            is_active=True,
            created_at=now,
        )
        session.add(dec)
        seeded += 1

    session.commit()
    logger.info("Successfully seeded %d benchmark cases from dataset into database", seeded)
    return seeded


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    session_factory = get_sqlite_session_factory()  # type: ignore[no-untyped-call]
    with session_factory() as sess:
        count = seed_benchmark_dataset(sess)
        demo_count = seed_demo_cases_if_empty(sess)
        bench_count = seed_benchmark_cluster_cases(sess)
        print(f"Database seeder complete: {count} members seeded, {demo_count} demo cases, {bench_count} benchmark cases.")

