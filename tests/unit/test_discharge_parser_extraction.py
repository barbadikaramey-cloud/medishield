"""Regression tests for OCR-tolerant CPT/ICD extraction in DischargeSummaryParser."""

from medishield.domain.clinical_align import DischargeSummaryParser


def _doc(primary: str, secondary: str, procedures: str) -> str:
    return (
        "FINALDIAGNOSES\n"
        f"Primary:\n{primary}\n"
        f"Secondary:\n{secondary}\n"
        f"PROCEDURESPERFORMED\n{procedures}\n"
        "HOSPITALCOURSE\nStable throughout the stay.\n"
    )


def test_cpt_glued_to_label_is_extracted() -> None:
    text = _doc(
        "Acute appendicitis (ICD-10:K35.80)",
        "None",
        "CPT59400\nRoutine obstetric care\nBilled: $3,500.00\nCPT 27447\nTotal knee\nBilled: $15,400.00",
    )
    analysis = DischargeSummaryParser.parse(text)
    assert analysis.procedures_cpt_codes == ["59400", "27447"]


def test_cpt_in_parentheses_without_space_is_extracted() -> None:
    text = _doc(
        "Acute appendicitis (ICD-10:K35.80)",
        "Low back pain (ICD-10: M54.5)",
        "Destruction lesions (CPT17000)\u2014Billed:$1,200.00\nEcho (CPT93306)\u2014Billed:$1,450.00",
    )
    analysis = DischargeSummaryParser.parse(text)
    assert analysis.procedures_cpt_codes == ["17000", "93306"]


def test_billed_amount_and_npi_are_not_cpt_codes() -> None:
    text = _doc(
        "Acute appendicitis (ICD-10:K35.80)",
        "None",
        "CPT 47562 Billed: $12345.00 NPI-8374651029",
    )
    analysis = DischargeSummaryParser.parse(text)
    assert analysis.procedures_cpt_codes == ["47562"]


def test_icd_seventh_character_letter_is_preserved() -> None:
    text = _doc(
        "Acute appendicitis (ICD-10:K35.80)",
        "Fracture of lower end of radius, initial encounter (ICD-10:S52.501A)",
        "CPT 44970",
    )
    analysis = DischargeSummaryParser.parse(text)
    assert "S52.501A" in analysis.secondary_icd_codes
    assert DischargeSummaryParser.ICD_PATTERN.findall("(ICD-10:S52.501A)") == ["S52.501A"]
