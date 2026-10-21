"""
Unit and Regression Tests for Multilingual Clinical Indication Linking,
Institutional Revenue Codes, Allowable Caps, and Currency Awareness (ADR-018).
"""

from datetime import date
from decimal import Decimal

from medishield.db.catalog import InMemoryClinicalGuidelineRepository
from medishield.db.enums import AgentVerdict, BusinessDecision
from medishield.domain.clinical_align import (
    DeterministicClinicalAlignmentEngine,
    DischargeSummaryParser,
)
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    ClinicalIndicationStatus,
    CriterionEvaluationOutcome,
    OrchestratorOutput,
    PerformanceEvidenceStatus,
    ProvenanceValue,
    ServiceLine,
)
from medishield.domain.decision import DecisionInputContext
from medishield.domain.explanation import generate_deterministic_explanation
from medishield.ocr.multilingual import (
    detect_currency,
    extract_multilingual_clinical_indications,
    extract_multilingual_icd_codes,
)
from medishield.ocr.ocr_reconcile import (
    ArithmeticCheckStatus,
    detect_claim_currency,
    evaluate_claim_totals,
)

# ── 1. Multilingual Currency Detection Tests ──────────────────────────────────


def test_detect_currency_iso_and_symbols() -> None:
    """Verifies currency detection across INR, EUR, GBP, JPY, and USD."""
    # INR detection
    code, sym = detect_currency("Billing system:SYNTHETIC·Currency:INR")
    assert code == "INR"
    assert sym == "₹"

    code, sym = detect_currency("Total Amount: ₹ 79,400.00")
    assert code == "INR"
    assert sym == "₹"

    code, sym = detect_currency("Grand Total: Rs. 1,50,000.00")
    assert code == "INR"
    assert sym == "₹"

    # EUR detection
    code, sym = detect_currency("Total facturado: 1.250,75 €")
    assert code == "EUR"
    assert sym == "€"

    code, sym = detect_currency("Importe en EUR: 450.00")
    assert code == "EUR"
    assert sym == "€"

    # GBP detection
    code, sym = detect_currency("Total payable: £ 540.00")
    assert code == "GBP"
    assert sym == "£"

    # USD default / detection
    code, sym = detect_currency("Total charges: $ 1,200.00")
    assert code == "USD"
    assert sym == "$"

    code, sym = detect_currency("Standard hospital bill without explicit currency")
    assert code == "USD"
    assert sym == "$"


def test_detect_claim_currency_from_spans() -> None:
    """Verifies detect_claim_currency from raw OCR spans."""
    spans = [
        {"token_text": "Hospital"},
        {"token_text": "Invoice"},
        {"token_text": "Currency:INR"},
        {"token_text": "Total"},
        {"token_text": "₹79,400"},
    ]
    curr_code, curr_sym = detect_claim_currency(spans)
    assert curr_code == "INR"
    assert curr_sym == "₹"


# ── 2. Multilingual Clinical Terminology & Entity Linking ─────────────────────


def test_extract_multilingual_clinical_indications_hindi() -> None:
    """Verifies Hindi Devanagari clinical term extraction mapped to ICD-10."""
    text = "मुख्य निदान: घुटने का ऑस्टियोआर्थराइटिस। मरीज को घुटने में गंभीर दर्द था।"
    indications = extract_multilingual_clinical_indications(text)
    assert len(indications) >= 1

    knee_ind = next((i for i in indications if i["condition_group"] == "Knee Osteoarthritis"), None)
    assert knee_ind is not None
    assert knee_ind["condition_group"] == "Knee Osteoarthritis"
    assert knee_ind["script"] == "DEVANAGARI"
    assert knee_ind["icd10_code"] is None
    assert knee_ind["code_specificity"] == "LATERALITY_UNSPECIFIED"
    assert "27447" in knee_ind["cpt_codes"]


def test_extract_multilingual_clinical_indications_spanish() -> None:
    """Verifies Spanish clinical term extraction mapped to ICD-10."""
    text = "Diagnóstico Principal: Gonartrosis de rodilla avanzada. Procedimiento programado."
    indications = extract_multilingual_clinical_indications(text)
    assert len(indications) >= 1

    knee_ind = next((i for i in indications if i["condition_group"] == "Knee Osteoarthritis"), None)
    assert knee_ind is not None
    assert knee_ind["condition_group"] == "Knee Osteoarthritis"
    assert knee_ind["script"] == "LATIN"
    assert knee_ind["icd10_code"] is None
    assert knee_ind["code_specificity"] == "LATERALITY_UNSPECIFIED"
    assert "27447" in knee_ind["cpt_codes"]


def test_extract_multilingual_icd_codes() -> None:
    """Verifies ICD-10 code extraction from vernacular text and alphanumeric patterns."""
    text = "निदान: घुटने का ऑस्टियोआर्थराइटिस (ICD-10: M17.11) तथा मोतियाबिंद"
    codes = extract_multilingual_icd_codes(text)
    assert "M17.11" in codes  # The source explicitly supplies laterality-specific code.
    assert "H25.9" in codes


# ── 3. Hindi and Spanish Discharge Summary Parsing ────────────────────────────


def test_discharge_summary_parser_hindi_devanagari() -> None:
    """Verifies parsing of Hindi discharge summary with Devanagari numerals and section headers."""
    hindi_doc = """
    अस्पताल डिस्चार्ज सारांश
    दाखिला तिथि: १५/१०/२०२६
    छुट्टी तिथि: २०/१०/२०२६
    मुख्य निदान: घुटने का ऑस्टियोआर्थराइटिस
    प्रक्रिया कोड: २७४४७
    अस्पताल का विवरण: मरीज अस्पताल में ५ दिन भर्ती रहा। गंभीर घुटने के दर्द हेतु सफल टोटल नी रिप्लेसमेंट सर्जरी की गई। प्रयोगशाला जांच सामान्य थी।
    """
    analysis = DischargeSummaryParser.parse(hindi_doc)

    assert analysis.admission_date == date(2026, 10, 15)
    assert analysis.discharge_date == date(2026, 10, 20)
    assert "घुटने का ऑस्टियोआर्थराइटिस" in analysis.primary_diagnosis_text
    assert "M17.11" not in analysis.primary_icd_codes
    assert "27447" in analysis.procedures_cpt_codes
    assert analysis.has_lab_evaluation is True
    assert analysis.has_inpatient_stay is True


def test_discharge_summary_parser_spanish() -> None:
    """Verifies parsing of Spanish discharge summary."""
    spanish_doc = """
    Resumen de Alta Hospitalaria
    Fecha de ingreso: 10/10/2026
    Fecha de alta: 15/10/2026
    Diagnóstico Principal: Gonartrosis de rodilla
    Procedimientos Realizados: 27447 Artroplastia total de rodilla
    Curso hospitalario: Paciente ingresó con dolor articular severo. Se realizaron pruebas de laboratorio e imagen.
    """
    analysis = DischargeSummaryParser.parse(spanish_doc)

    assert analysis.admission_date == date(2026, 10, 10)
    assert analysis.discharge_date == date(2026, 10, 15)
    assert "Gonartrosis de rodilla" in analysis.primary_diagnosis_text
    assert "M17.11" not in analysis.primary_icd_codes
    assert "27447" in analysis.procedures_cpt_codes
    assert analysis.has_lab_evaluation is True
    assert analysis.has_imaging_studies is True


# ── 4. Clinical Alignment Engine with Multilingual Indications ───────────────


def test_clinical_alignment_hindi_diagnosis_requires_catalog_valid_code(monkeypatch) -> None:
    """Hindi diagnosis can indicate CPT only after a catalog-validated code is supplied."""
    repo = InMemoryClinicalGuidelineRepository(
        indications={"27447": {
            "name": "Total Knee Arthroplasty",
            "anatomy": "Knee Joint",
            "valid_icd_prefixes": ("M17",),
            "keywords": (),
        }},
        valid_diagnosis_codes={"M17.9"},
    )
    monkeypatch.setattr(DeterministicClinicalAlignmentEngine, "_default_repo_instance", repo)
    result = DeterministicClinicalAlignmentEngine.evaluate_line_item(
        line_number=1,
        service_code="27447",
        billed_units=1.0,
        billed_amount=20000.0,
        primary_diagnosis_text="घुटने का ऑस्टियोआर्थराइटिस",
        primary_icd10_codes=["M17.9"],
    )
    assert result.indication_status == ClinicalIndicationStatus.EXPLICITLY_INDICATED
    assert result.line_verdict == AgentVerdict.INDETERMINATE


def test_clinical_alignment_spanish_diagnosis_requires_catalog_valid_code(monkeypatch) -> None:
    """Spanish diagnosis cannot infer laterality and uses a validated unspecified code."""
    repo = InMemoryClinicalGuidelineRepository(
        indications={"27447": {
            "name": "Total Knee Arthroplasty",
            "anatomy": "Knee Joint",
            "valid_icd_prefixes": ("M17",),
            "keywords": (),
        }},
        valid_diagnosis_codes={"M17.9"},
    )
    monkeypatch.setattr(DeterministicClinicalAlignmentEngine, "_default_repo_instance", repo)
    result = DeterministicClinicalAlignmentEngine.evaluate_line_item(
        line_number=1,
        service_code="27447",
        billed_units=1.0,
        billed_amount=20000.0,
        primary_diagnosis_text="Gonartrosis de rodilla",
        primary_icd10_codes=["M17.9"],
    )
    assert result.indication_status == ClinicalIndicationStatus.EXPLICITLY_INDICATED
    assert result.line_verdict == AgentVerdict.INDETERMINATE


def test_clinical_alignment_misaligned_diagnosis_flagged(monkeypatch) -> None:
    """Verifies CPT 27447 (Knee Replacement) is NO_DOCUMENTED_INDICATION when diagnosis is Cataract."""
    repo = InMemoryClinicalGuidelineRepository(
        indications={"27447": {
            "name": "Total Knee Arthroplasty",
            "anatomy": "Knee Joint",
            "valid_icd_prefixes": ("M17",),
            "keywords": (),
        }},
        valid_diagnosis_codes={"H25.9"},
    )
    monkeypatch.setattr(DeterministicClinicalAlignmentEngine, "_default_repo_instance", repo)
    result = DeterministicClinicalAlignmentEngine.evaluate_line_item(
        line_number=1,
        service_code="27447",
        billed_units=1.0,
        billed_amount=20000.0,
        primary_diagnosis_text="सफेद मोतियाबिंद",
        primary_icd10_codes=["H25.9"],
    )
    assert result.indication_status == ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION
    assert result.discrepancy_flag is True
    assert "Diagnosis-procedure clinical misalignment" in (result.discrepancy_description or "")


def test_procedure_mention_does_not_establish_clinical_indication(monkeypatch) -> None:
    """A billed/procedure-section phrase cannot satisfy a diagnosis-based indication."""
    repo = InMemoryClinicalGuidelineRepository(
        guidelines={
            "47562": {
                "guideline_id": "TEST-LCD-47562",
                "guideline_version": "test-1",
                "guideline_title": "Laparoscopic cholecystectomy",
                "effective_date": date(2025, 1, 1),
                "expiration_date": None,
                "requires_prior_auth": False,
                "mandatory_criteria": [
                    {
                        "criterion_id": "TEST-47562-01",
                        "description": "Documented biliary disease",
                        "is_mandatory": True,
                    }
                ],
            }
        },
        indications={
            "47562": {
                "name": "Laparoscopic cholecystectomy",
                "anatomy": "Gallbladder / Biliary Tract",
                "valid_icd_prefixes": ("K80", "K81", "K82"),
                "keywords": ("gallbladder", "cholelithiasis", "cholecystitis"),
            }
        },
        valid_diagnosis_codes={"G43.909", "I10"},
    )
    monkeypatch.setattr(DeterministicClinicalAlignmentEngine, "_default_repo_instance", repo)

    result = DeterministicClinicalAlignmentEngine.evaluate_line_item(
        line_number=1,
        service_code="47562",
        billed_units=1.0,
        billed_amount=18500.0,
        service_date=date(2026, 10, 18),
        performance_status=PerformanceEvidenceStatus.DIRECTLY_DOCUMENTED_PERFORMANCE,
        clinical_notes_text="Procedures Performed: CPT 47562 laparoscopic cholecystectomy",
        diagnosis_codes=["G43.909", "I10"],
        primary_icd10_codes=["G43.909"],
        secondary_icd10_codes=["I10"],
        primary_diagnosis_text="Migraine, unspecified, not intractable",
        secondary_diagnosis_text="Essential hypertension",
    )

    assert result.indication_status == ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION
    assert result.line_verdict == AgentVerdict.INDETERMINATE
    assert result.guideline_citation is not None
    assert result.guideline_citation.criteria_evaluations
    assert all(
        criterion.evaluation_outcome == CriterionEvaluationOutcome.UNASSESSED_EVIDENCE_UNAVAILABLE
        for criterion in result.guideline_citation.criteria_evaluations
        if criterion.is_mandatory
    )


# ── 5. Institutional Revenue Codes & Allowable Caps ──────────────────────────


def test_evaluate_claim_totals_institutional_allowable_cap() -> None:
    """
    Verifies that institutional claims with chargemaster excess reconciled against
    contractual allowable fee schedules pass arithmetic validation (ADR-018).
    Example from CASE-20261018-EA1BE2:
    - Declared Total: 79,400 INR
    - Lines Sum: 99,400 INR (65,000 Room & Board + 20,000 Surgery + 9,800 Lab + 4,600 Pharmacy)
    - Difference: 20,000 INR allowable contractual adjustment on institutional chargemaster rate.
    """
    lines = [
        ServiceLine(
            line_number=1,
            cpt_code="0100",  # Revenue Code: Room & Board
            line_description="Room and Board - Private (5 days)",
            billed_amount=Decimal("65000.00"),
            currency="INR",
            currency_symbol="₹",
        ),
        ServiceLine(
            line_number=2,
            cpt_code="27447",
            line_description="Total Knee Arthroplasty",
            billed_amount=Decimal("20000.00"),
            currency="INR",
            currency_symbol="₹",
        ),
        ServiceLine(
            line_number=3,
            cpt_code="800",  # Revenue Code: Laboratory
            line_description="Laboratory services",
            billed_amount=Decimal("9800.00"),
            currency="INR",
            currency_symbol="₹",
        ),
        ServiceLine(
            line_number=4,
            cpt_code="PHARM",
            line_description="Pharmacy drugs and solutions",
            billed_amount=Decimal("4600.00"),
            currency="INR",
            currency_symbol="₹",
        ),
    ]

    declared_total = 79400.00
    declared_pv = ProvenanceValue(value=79400.00, confidence=0.98)
    _pv, total, status, errors = evaluate_claim_totals(
        declared_total=declared_total,
        declared_total_pv=declared_pv,
        service_lines=lines,
        has_total_label=True,
        currency_symbol="₹",
    )

    assert status == ArithmeticCheckStatus.PASS
    assert total == 79400.00
    assert len(errors) == 0


# ── 6. Explanation Generator Currency Formatting ─────────────────────────────


def test_deterministic_explanation_currency_symbol() -> None:
    """Verifies that decision explanations format amounts using the claim currency symbol."""
    lines = [
        ServiceLine(
            line_number=1,
            cpt_code="27447",
            line_description="Total Knee Arthroplasty",
            billed_amount=Decimal("20000.00"),
            currency="INR",
            currency_symbol="₹",
        ),
    ]
    claims_out = ClaimsAgentOutput(
        verdict=AgentVerdict.PASS,
        confidence=0.98,
        claim_amount=ProvenanceValue(value=79400.00, confidence=0.98),
        currency="INR",
        currency_symbol="₹",
        service_date=ProvenanceValue(value="2026-10-15", confidence=0.99),
        provider_npi=ProvenanceValue(value="1234567890", confidence=0.99),
        line_items_sum_valid=True,
        calculated_lines_sum=79400.00,
        schema_valid=True,
        latency_ms=15,
        service_lines=lines,
        cpt_codes=[ProvenanceValue(value="27447", confidence=0.98)],
        icd10_codes=[ProvenanceValue(value="M17.11", confidence=0.98)],
    )

    context = DecisionInputContext(
        case_id="CASE-INR-01",
        execution_id="EXEC-01",
        claim_id="CLM-TEST-INR",
        member_id="MEM-12345",
        claims_output=claims_out,
    )
    decision = OrchestratorOutput(
        case_id="CASE-INR-01",
        execution_id="EXEC-01",
        decision=BusinessDecision.APPROVE,
        decision_confidence=0.98,
        rule_id_matched="P1_ALL_PASS",
        justification="All checks passed.",
        agent_summaries={},
        is_auto_decided=True,
    )

    explanation = generate_deterministic_explanation(context, decision)

    assert "₹79,400.00" in explanation
    assert "₹20,000.00" in explanation
    assert "$79,400" not in explanation


# ── 7. Hybrid Multilingual LLM Clinical Extraction Gateway Tests (Rule 11) ────


class MockGroqClient:
    """Mock Groq LLM client for testing hybrid multilingual clinical translation."""

    def __init__(self, response_data: dict, available: bool = True) -> None:
        self.response_data = response_data
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def generate_json(self, prompt: str, system_prompt: str = "") -> dict:
        if not self._available:
            raise RuntimeError("Groq LLM gateway is offline")
        return self.response_data


def test_hybrid_multilingual_llm_extraction_uncataloged_condition() -> None:
    """
    Verifies that uncataloged, open-vocabulary foreign language diagnoses
    (e.g. Hindi 'तीव्र अग्नाशयशोथ' for Acute Pancreatitis) are translated
    and mapped to canonical ICD-10 (K85.90) via LLM assistance (Rule 11).
    """
    hindi_narrative = (
        "मरीज को अचानक तेज पेट दर्द हुआ। विस्तृत जांच में तीव्र अग्नाशयशोथ "
        "(तीव्र अग्नाशयशोथ) पाया गया। गहन चिकित्सा कक्ष में उपचार शुरू किया गया।"
    )
    mock_llm = MockGroqClient({
        "clinical_entities": [
            {
                "source_term": "तीव्र अग्नाशयशोथ",
                "standard_name": "Acute pancreatitis",
                "condition_group": "Pancreatitis",
                "icd10_code": "K85.90",
                "anatomy": "Pancreas",
                "confidence": 0.96,
            }
        ]
    })

    indications = extract_multilingual_clinical_indications(
        hindi_narrative,
        llm_client=mock_llm,
    )

    assert len(indications) >= 1
    pancreas_ind = next((i for i in indications if i["icd10_code"] == "K85.90"), None)
    assert pancreas_ind is not None
    assert pancreas_ind["standard_name"] == "Acute pancreatitis"
    assert pancreas_ind["source_term"] if "source_term" in pancreas_ind else pancreas_ind["matched_term"] == "तीव्र अग्नाशयशोथ"
    assert pancreas_ind["script"] == "DEVANAGARI"
    assert pancreas_ind["extraction_method"] == "LLM_MULTILINGUAL_TRANSLATION"


def test_hybrid_multilingual_llm_hallucination_filtered() -> None:
    """
    Verifies that hallucinated LLM terms not grounded in the source text
    are deterministically filtered out to preserve evidence integrity (Rule 12).
    """
    spanish_text = "El paciente presentó cuadro de dolor abdominal leve y náuseas."
    mock_llm = MockGroqClient({
        "clinical_entities": [
            {
                "source_term": "cáncer de pulmón avanzado",  # Completely absent from source text
                "standard_name": "Advanced lung cancer",
                "condition_group": "Oncology",
                "icd10_code": "C34.90",
                "anatomy": "Lung",
                "confidence": 0.99,
            }
        ]
    })

    indications = extract_multilingual_clinical_indications(
        spanish_text,
        llm_client=mock_llm,
    )
    # Hallucinated entity must be rejected
    assert not any(i["icd10_code"] == "C34.90" for i in indications)


def test_hybrid_multilingual_llm_invalid_icd_filtered() -> None:
    """
    Verifies that malformed or fabricated ICD-10 strings from LLM outputs
    are rejected by deterministic regex format validation (Rule 11).
    """
    spanish_text = "Paciente con diagnóstico de apendicitis aguda confirmada."
    mock_llm = MockGroqClient({
        "clinical_entities": [
            {
                "source_term": "apendicitis aguda",
                "standard_name": "Acute appendicitis",
                "condition_group": "Appendix",
                "icd10_code": "NOT_AN_ICD_CODE_12345",  # Invalid format
                "anatomy": "Appendix",
                "confidence": 0.95,
            }
        ]
    })

    indications = extract_multilingual_clinical_indications(
        spanish_text,
        llm_client=mock_llm,
    )
    assert not any(i["icd10_code"] == "NOT_AN_ICD_CODE_12345" for i in indications)


def test_hybrid_multilingual_llm_offline_fallback() -> None:
    """
    Verifies that when the LLM gateway is offline or unavailable,
    the system falls back gracefully to deterministic ontology matching (Rule 13).
    """
    text = "मुख्य निदान: घुटने का ऑस्टियोआर्थराइटिस"
    offline_llm = MockGroqClient({}, available=False)

    indications = extract_multilingual_clinical_indications(
        text,
        llm_client=offline_llm,
    )
    assert len(indications) >= 1
    assert indications[0]["icd10_code"] is None
    assert indications[0]["extraction_method"] == "DETERMINISTIC_REGISTRY"

