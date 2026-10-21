"""INDETERMINATE outcomes must carry structured reason codes without changing verdicts."""

from datetime import date

from medishield.agents.claims import ClaimsAgent
from medishield.agents.policy import PolicyChunkRecord, PolicyCorpusRepository, PolicyCoverageAgent
from medishield.db.catalog import InMemoryClinicalGuidelineRepository, InMemoryCodeCatalogRepository
from medishield.db.enums import AgentVerdict, IndeterminateReason
from medishield.domain.clinical_align import DeterministicClinicalAlignmentEngine
from medishield.domain.contracts import ClinicalIndicationStatus, ProvenanceValue, ServiceLine
from medishield.domain.provenance import FieldProvenance


class _OfflineLLM:
    def is_available(self) -> bool:
        return False


def _prov() -> FieldProvenance:
    return FieldProvenance(document_id="D1", page_number=1, text_content="x", evidence_quality_score=1.0)


def _line() -> ServiceLine:
    return ServiceLine(
        line_number=1,
        hcpcs=ProvenanceValue(value="99300", confidence=0.9, provenance=_prov()),
        amount=ProvenanceValue(value=150.0, confidence=0.9, provenance=_prov()),
        service_date=ProvenanceValue(value=date(2026, 1, 15), confidence=0.9, provenance=_prov()),
        units=ProvenanceValue(value=1.0, confidence=0.9, provenance=_prov()),
    )


def _claims_agent() -> ClaimsAgent:
    return ClaimsAgent(code_catalog=InMemoryCodeCatalogRepository(initial_codes=[]))


def test_claims_unverified_diagnosis_maps_to_reason_code() -> None:
    agent = _claims_agent()
    codes = agent._indeterminate_reasons(
        verdict=AgentVerdict.INDETERMINATE,
        validation_errors=["UNVERIFIED_ICD10_CODES: Diagnosis code(s) K35.80 lack verified OCR provenance."],
        schema_valid=True,
        raw_claim_amount=150.0,
        raw_npi="1234567890",
        service_lines=[_line()],
    )
    assert codes == [IndeterminateReason.UNVERIFIED_DIAGNOSIS_CODE]


def test_claims_multiple_causes_are_all_reported() -> None:
    agent = _claims_agent()
    codes = agent._indeterminate_reasons(
        verdict=AgentVerdict.INDETERMINATE,
        validation_errors=[
            "UNRESOLVED_VISUAL_CANDIDATE: amount could not be corroborated",
            "UNSUPPORTED_CPT_CODES: Code(s) 99999 cannot be verified",
        ],
        schema_valid=True,
        raw_claim_amount=1.0,
        raw_npi="1234567890",
        service_lines=[],
    )
    assert IndeterminateReason.UNRESOLVED_VISUAL_EVIDENCE in codes
    assert IndeterminateReason.UNVERIFIED_PROCEDURE_CODE in codes


def test_claims_missing_header_data_is_incomplete_claim_data() -> None:
    agent = _claims_agent()
    codes = agent._indeterminate_reasons(
        verdict=AgentVerdict.INDETERMINATE,
        validation_errors=[],
        schema_valid=False,
        raw_claim_amount=None,
        raw_npi=None,
        service_lines=[],
    )
    assert codes == [IndeterminateReason.INCOMPLETE_CLAIM_DATA]


def test_claims_non_indeterminate_verdict_has_no_reason_codes() -> None:
    agent = _claims_agent()
    assert agent._indeterminate_reasons(
        verdict=AgentVerdict.PASS,
        validation_errors=[],
        schema_valid=True,
        raw_claim_amount=1.0,
        raw_npi="1234567890",
        service_lines=[],
    ) == []


def test_policy_without_matching_clause_reports_no_policy_clause() -> None:
    agent = PolicyCoverageAgent(
        corpus_repo=PolicyCorpusRepository(chunks=[]),
        llm_client=_OfflineLLM(),  # type: ignore[arg-type]
        code_catalog=InMemoryCodeCatalogRepository(initial_codes=[]),
    )
    out = agent.execute(policy_id="P", policy_version="v1", cpt_codes=["99300"], matched_chunks=[])
    assert out.verdict == AgentVerdict.INDETERMINATE
    assert out.reason_codes == [IndeterminateReason.NO_POLICY_CLAUSE]


def test_policy_without_billing_codes_reports_missing_input() -> None:
    agent = PolicyCoverageAgent(
        corpus_repo=PolicyCorpusRepository(chunks=[]),
        llm_client=_OfflineLLM(),  # type: ignore[arg-type]
        code_catalog=InMemoryCodeCatalogRepository(initial_codes=[]),
    )
    out = agent.execute(policy_id="P", policy_version="v1", cpt_codes=[])
    assert out.reason_codes == [IndeterminateReason.MISSING_INPUT]


def test_policy_pass_has_no_reason_codes() -> None:
    chunk = PolicyChunkRecord(
        chunk_id="chk-test-1",
        policy_id="P",
        version_tag="v1",
        section_number="1.1",
        section_title="Covered",
        chunk_text="Covered service",
        page_number=1,
        covered_percentage=80.0,
        deductible_limit=100.0,
        copay_percentage=10.0,
        matching_cpt_codes=["99300"],
    )
    agent = PolicyCoverageAgent(
        corpus_repo=PolicyCorpusRepository(chunks=[chunk]),
        llm_client=_OfflineLLM(),  # type: ignore[arg-type]
        code_catalog=InMemoryCodeCatalogRepository(initial_codes=[]),
    )
    out = agent.execute(policy_id="P", policy_version="v1", cpt_codes=["99300"])
    assert out.reason_codes == []


def test_clinical_line_reason_codes_for_unvalidated_diagnosis(monkeypatch) -> None:
    repo = InMemoryClinicalGuidelineRepository(
        indications={
            "27447": {"name": "TKA", "anatomy": "Knee Joint", "valid_icd_prefixes": ("M17",), "keywords": ()}
        },
        valid_diagnosis_codes=set(),
    )
    monkeypatch.setattr(DeterministicClinicalAlignmentEngine, "_default_repo_instance", repo)
    line = DeterministicClinicalAlignmentEngine.evaluate_line_item(
        line_number=1,
        service_code="27447",
        billed_units=1.0,
        billed_amount=1000.0,
        primary_icd10_codes=["K35.80"],
    )
    assert line.indication_status == ClinicalIndicationStatus.POTENTIALLY_RELATED
    assert IndeterminateReason.DIAGNOSIS_VALIDATION_UNAVAILABLE in line.reason_codes
    assert IndeterminateReason.PERFORMANCE_NOT_DOCUMENTED in line.reason_codes
    assert "reason_codes" in line.model_dump(mode="json")

    output = DeterministicClinicalAlignmentEngine.align_claim(expected_line_numbers=[1], line_assessments=[line])
    assert output.verdict == AgentVerdict.INDETERMINATE
    assert IndeterminateReason.DIAGNOSIS_VALIDATION_UNAVAILABLE in output.reason_codes
