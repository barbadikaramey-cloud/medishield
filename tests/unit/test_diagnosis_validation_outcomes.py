"""Diagnosis-code validation outcomes must separate uncertainty from true clinical misalignment."""

from medishield.db.catalog import InMemoryClinicalGuidelineRepository
from medishield.db.enums import AgentVerdict
from medishield.domain.clinical_align import DeterministicClinicalAlignmentEngine
from medishield.domain.contracts import ClinicalIndicationStatus

_KNEE_RULE = {
    "27447": {
        "name": "Total Knee Arthroplasty",
        "anatomy": "Knee Joint",
        "valid_icd_prefixes": ("M17",),
        "keywords": (),
    }
}


def _evaluate(monkeypatch, valid_codes: set[str], documented: list[str]):
    repo = InMemoryClinicalGuidelineRepository(indications=_KNEE_RULE, valid_diagnosis_codes=valid_codes)
    monkeypatch.setattr(DeterministicClinicalAlignmentEngine, "_default_repo_instance", repo)
    return DeterministicClinicalAlignmentEngine.evaluate_line_item(
        line_number=1,
        service_code="27447",
        billed_units=1.0,
        billed_amount=20000.0,
        primary_diagnosis_text="Unspecified acute appendicitis",
        primary_icd10_codes=documented,
    )


def test_unvalidated_code_is_uncertainty_not_misalignment(monkeypatch) -> None:
    result = _evaluate(monkeypatch, valid_codes=set(), documented=["K35.80"])
    assert result.indication_status == ClinicalIndicationStatus.POTENTIALLY_RELATED
    assert result.line_verdict == AgentVerdict.INDETERMINATE
    description = result.discrepancy_description or ""
    assert "K35.80" in description
    assert "could not be validated" in description
    assert "None documented" not in description
    assert "clinical misalignment" not in description


def test_validated_but_unrelated_code_is_still_misalignment(monkeypatch) -> None:
    result = _evaluate(monkeypatch, valid_codes={"K35.80"}, documented=["K35.80"])
    assert result.indication_status == ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION
    assert "Diagnosis-procedure clinical misalignment" in (result.discrepancy_description or "")
    assert "K35.80" in (result.discrepancy_description or "")


def test_no_documented_diagnosis_reports_none_documented(monkeypatch) -> None:
    result = _evaluate(monkeypatch, valid_codes={"K35.80"}, documented=[])
    assert result.indication_status == ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION
    assert "None documented" in (result.discrepancy_description or "")
