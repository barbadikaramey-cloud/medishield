"""Unit tests for institutional revenue code clinical alignment and surgical room & board substantiation (ADR-021)."""

from datetime import date

from medishield.db.catalog import InMemoryClinicalGuidelineRepository
from medishield.db.enums import AgentVerdict
from medishield.domain.clinical_align import DeterministicClinicalAlignmentEngine
from medishield.domain.contracts import (
    ClinicalIndicationStatus,
    MedicalNecessityStatus,
    PerformanceEvidenceStatus,
    ServiceModality,
)
from medishield.domain.provenance import FieldProvenance


def test_classify_modality_ub04_revenue_codes() -> None:
    """Verifies that standard UB-04 revenue codes are accurately classified by clinical modality (ADR-021)."""
    engine = DeterministicClinicalAlignmentEngine

    # Room & Board (0100 - 0219)
    assert engine.classify_modality("0100") == ServiceModality.ROOM_AND_BOARD
    assert engine.classify_modality("0110") == ServiceModality.ROOM_AND_BOARD
    assert engine.classify_modality("0120") == ServiceModality.ROOM_AND_BOARD
    assert engine.classify_modality("0200") == ServiceModality.ROOM_AND_BOARD
    assert engine.classify_modality("REV_0120") == ServiceModality.ROOM_AND_BOARD

    # Pharmacy (0250 - 0259)
    assert engine.classify_modality("0250") == ServiceModality.PHARMACEUTICAL
    assert engine.classify_modality("0251") == ServiceModality.PHARMACEUTICAL
    assert engine.classify_modality("REV_0250") == ServiceModality.PHARMACEUTICAL

    # Laboratory (0300 - 0319)
    assert engine.classify_modality("0300") == ServiceModality.LABORATORY_TEST
    assert engine.classify_modality("0310") == ServiceModality.LABORATORY_TEST

    # Radiology / Imaging (0320 - 0359, 0400 - 0409)
    assert engine.classify_modality("0320") == ServiceModality.DIAGNOSTIC_IMAGING
    assert engine.classify_modality("0400") == ServiceModality.DIAGNOSTIC_IMAGING

    # Operating Room / Surgery (0360 - 0379)
    assert engine.classify_modality("0360") == ServiceModality.SURGICAL_PROCEDURE
    assert engine.classify_modality("0370") == ServiceModality.SURGICAL_PROCEDURE

    # CPT Codes
    assert engine.classify_modality("27447") == ServiceModality.SURGICAL_PROCEDURE
    assert engine.classify_modality("47562") == ServiceModality.SURGICAL_PROCEDURE
    assert engine.classify_modality("99213") == ServiceModality.MEDICAL_VISIT_OR_EVAL


def test_ancillary_revenue_code_necessity_substantiated_by_surgery() -> None:
    """Ancillary lines may have contextual necessity without evidence-complete PASS (ADR-021)."""
    engine = DeterministicClinicalAlignmentEngine
    dummy_span = FieldProvenance(
        document_id="disch_01",
        page_number=1,
        span_id="s1",
        text_content="Inpatient TKA surgery",
        evidence_quality_score=1.0,
    )

    # Evaluate Room & Board (0120) line item
    r_and_b = engine.evaluate_line_item(
        line_number=2,
        service_code="0120",
        billed_units=3.0,
        billed_amount=4500.0,
        service_date=date(2025, 11, 15),
        modality=ServiceModality.ROOM_AND_BOARD,
        performance_status=PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
        indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
        supporting_spans=[dummy_span],
        clinical_notes_text="Patient admitted for Total Knee Arthroplasty (CPT 27447). Hospital stay uneventful.",
        guideline_repo=InMemoryClinicalGuidelineRepository(),
    )

    assert r_and_b.modality == ServiceModality.ROOM_AND_BOARD
    assert r_and_b.necessity_status == MedicalNecessityStatus.MET
    assert r_and_b.line_verdict == AgentVerdict.INDETERMINATE
    assert r_and_b.discrepancy_flag is True

    # Evaluate Pharmacy (0250) line item
    pharm = engine.evaluate_line_item(
        line_number=3,
        service_code="0250",
        billed_units=1.0,
        billed_amount=1200.0,
        service_date=date(2025, 11, 15),
        modality=ServiceModality.PHARMACEUTICAL,
        performance_status=PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
        indication_status=ClinicalIndicationStatus.EXPLICITLY_INDICATED,
        supporting_spans=[dummy_span],
        clinical_notes_text="Post-operative analgesics and IV cefazolin administered.",
        guideline_repo=InMemoryClinicalGuidelineRepository(),
    )

    assert pharm.modality == ServiceModality.PHARMACEUTICAL
    assert pharm.necessity_status == MedicalNecessityStatus.MET
    assert pharm.line_verdict == AgentVerdict.INDETERMINATE
    assert pharm.discrepancy_flag is True
