"""
Acceptance unit tests for Step 2E-3: Policy Retrieval and RAG Tracing Instrumentation.

Validates:
1. PolicyCorpusRepository.query_chunks emits a single child span named 'policy_corpus_query'
   with run_type='retriever', component='policy_corpus_repository', operation='query_chunks',
   inputs={}, and safe operational metrics (status='SUCCESS', retrieved_chunk_count, is_complete=True).
2. Span hierarchy: policy_corpus_query is properly nested beneath policy_coverage_rag_node
   and case_workflow_execute root trace.
3. Safe trace summary conforms strictly to ALLOWED_TRACE_KEYS allowlist schema.
4. Strict sensitive content isolation: zero raw query strings, CPT/Rev codes, policy IDs,
   member IDs, effective dates, clause texts, embeddings, or raw exceptions in trace payloads.
5. Functional invariance: query_chunks behaves identically whether tracing is enabled or disabled.
6. Empty candidate / no match queries emit status='SUCCESS', retrieved_chunk_count=0, is_complete=False.
7. Original exceptions propagate unchanged while recording normalized error categories.
8. Telemetry transport failures do not disrupt policy retrieval.
9. Repeated retrieval invocations create separate child spans without duplicate root traces.
10. Disabled tracing requires no credentials, makes no network calls, and creates zero trace spans.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any
from unittest import mock

import pytest

from medishield.agents.claims import ClaimsAgent
from medishield.agents.classifier import DocumentClassifierAgent
from medishield.agents.kyc import InMemoryMemberRepository, KYCAgent, MemberRecord
from medishield.agents.policy import (
    InMemoryPolicyEnrollmentRepository,
    PolicyChunkRecord,
    PolicyContextAgent,
    PolicyCorpusRepository,
    PolicyCoverageAgent,
    PolicyEnrollmentRecord,
)
from medishield.core.config import settings
from medishield.core.tracing import ALLOWED_TRACE_KEYS, reset_tracing_client
from medishield.db.enums import DocumentType
from medishield.domain.fraud import DeterministicFraudEngine
from medishield.intake.storage import InMemoryStorageBackend
from medishield.ocr.preprocessor import DefaultOCREngine, OCRPreprocessorEngine
from medishield.workflows.coordinator import CaseWorkflowCoordinator
from medishield.workflows.state import DocumentReference, create_initial_case_state

CANARY_PATIENT = "CANARY_PATIENT_ALICE_MONTGOMERY_DOB_1985"
CANARY_QUERY = "pancreatic adenocarcinoma stage IV chemotherapy Gemcitabine CPT 99215"
CANARY_POLICY_ID = "MED-GLD-CANARY-9999"
CANARY_CHUNK_TEXT = "SECRET_CANARY_INTERNAL_POLICY_UNDERWRITING_NOTES_DO_NOT_DISCLOSE"

SAMPLE_ID_TEXT = """STATE DRIVER LICENSE
DL NUMBER: D9817264
MEMBER ID: MEM-12345
NAME: ALICE SMITH
DOB: 1985-05-20
SSN: ***-**-4321
ADDRESS: 123 MAIN ST, SPRINGFIELD
EXPIRATION: 2028-10-15
"""

SAMPLE_CLAIM_TEXT = """UB-04 / CMS-1500 HEALTH INSURANCE CLAIM FORM
PATIENT NAME: ALICE SMITH
MEMBER ID: MEM-12345
POLICY ID: POL-CORP-2026
VERSION: v2.0
SERVICE DATE: 2026-03-15
DIAGNOSIS: J02.9
PROCEDURE: 99213
1. 99213 Office Visit 2026-03-15 1.0 $200.00 $200.00
2. 93000 Routine ECG 2026-03-15 1.0 $150.00 $150.00
TOTAL CHARGE: $350.00
"""


def _upload_test_document(
    storage: InMemoryStorageBackend,
    case_id: str,
    doc_id: str,
    text_content: str,
    doc_type: str | None = None,
) -> DocumentReference:
    """Helper storing raw text bytes in storage and returning a DocumentReference."""
    key = f"uploads/{case_id}/{doc_id}.txt"
    storage.put_bytes(key, text_content.encode("utf-8"), "text/plain")
    return DocumentReference(
        document_id=doc_id,
        object_key=key,
        mime_type="text/plain",
        checksum="dummy-hash",
        split_source_page_indices=[1],
        file_name=f"{doc_id}.txt",
        doc_type=doc_type,
    )


@pytest.fixture(autouse=True)
def clean_tracing_state():
    """Ensure clean tracing state before and after each test."""
    reset_tracing_client()
    orig_tracing = settings.LANGSMITH_TRACING
    orig_key = settings.LANGSMITH_API_KEY
    orig_pseudo = settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM
    orig_env = os.environ.get("LANGSMITH_TRACING")
    yield
    settings.LANGSMITH_TRACING = orig_tracing
    settings.LANGSMITH_API_KEY = orig_key
    settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM = orig_pseudo
    if orig_env is not None:
        os.environ["LANGSMITH_TRACING"] = orig_env
    else:
        os.environ.pop("LANGSMITH_TRACING", None)
    reset_tracing_client()


def _build_test_corpus() -> PolicyCorpusRepository:
    """Builds a test corpus with standard test chunks."""
    corpus = PolicyCorpusRepository(chunks=[])
    corpus.add_chunk(
        PolicyChunkRecord(
            chunk_id="chk-001",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            section_number="SEC-3.1",
            section_title="Outpatient Evaluation and Management",
            chunk_text="Outpatient office visits are covered benefits when medically necessary.",
            page_number=12,
            is_exclusion=False,
            requires_prior_auth=False,
            covered_percentage=80.0,
            deductible_limit=500.0,
            copay_percentage=20.0,
            matching_cpt_codes=["99213", "99214", "93000"],
        )
    )
    corpus.add_chunk(
        PolicyChunkRecord(
            chunk_id="chk-002",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            section_number="SEC-4.2",
            section_title="Diagnostic Testing and Imaging",
            chunk_text="Routine electrocardiograms (ECG) and diagnostic testing are covered benefits.",
            page_number=18,
            is_exclusion=False,
            requires_prior_auth=False,
            covered_percentage=80.0,
            deductible_limit=500.0,
            copay_percentage=20.0,
            matching_cpt_codes=["93000"],
        )
    )
    return corpus


def _setup_coordinator_environment() -> tuple[CaseWorkflowCoordinator, InMemoryStorageBackend, PolicyCorpusRepository]:
    """Sets up a complete coordinator workflow test harness."""
    storage = InMemoryStorageBackend()
    ocr_engine = OCRPreprocessorEngine(engine=DefaultOCREngine())
    member_repo = InMemoryMemberRepository([
        MemberRecord(
            member_id="MEM-12345",
            first_name="Alice",
            last_name="Smith",
            date_of_birth=date(1985, 5, 20),
            ssn_last4="4321",
        )
    ])
    kyc_agent = KYCAgent(member_repo=member_repo)
    claims_agent = ClaimsAgent()

    enrollment_repo = InMemoryPolicyEnrollmentRepository([
        PolicyEnrollmentRecord(
            member_id="MEM-12345",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            plan_name="Comprehensive Gold PPO",
            effective_start_date=date(2026, 1, 1),
            effective_end_date=date(2026, 12, 31),
        ),
    ])
    policy_context_agent = PolicyContextAgent(enrollment_repo=enrollment_repo)
    corpus = _build_test_corpus()
    policy_coverage_agent = PolicyCoverageAgent(corpus_repo=corpus)
    fraud_engine = DeterministicFraudEngine
    classifier_agent = DocumentClassifierAgent()
    audit_ledger: list[dict[str, Any]] = []

    coordinator = CaseWorkflowCoordinator(
        storage=storage,
        ocr_engine=ocr_engine,
        classifier_agent=classifier_agent,
        kyc_agent=kyc_agent,
        claims_agent=claims_agent,
        policy_context_agent=policy_context_agent,
        policy_coverage_agent=policy_coverage_agent,
        fraud_engine=fraud_engine,
        policy_corpus=corpus,
        audit_ledger=audit_ledger,
    )
    return coordinator, storage, corpus


# ── Test 1: Span Emission and Safe Operational Metrics ─────────────────────────
def test_policy_retrieval_tracing_emits_span_with_operational_metrics():
    """Asserts that query_chunks emits 'policy_corpus_query' with run_type='retriever' and safe summaries."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    corpus = _build_test_corpus()
    captured_kwargs: dict[str, Any] = {}
    captured_tree: mock.MagicMock | None = None

    def mock_trace(name, **kwargs):
        nonlocal captured_tree, captured_kwargs
        captured_kwargs = dict(kwargs)
        captured_kwargs["name"] = name
        cm = mock.MagicMock()
        captured_tree = mock.MagicMock()
        cm.__enter__.return_value = captured_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        chunks = corpus.query_chunks(
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            query_text="outpatient office visit evaluation",
            cpt_codes=["99213"],
            top_k=5,
        )

    assert len(chunks) > 0
    assert captured_kwargs["name"] == "policy_corpus_query"
    assert captured_kwargs["run_type"] == "retriever"
    assert captured_kwargs["inputs"] == {}

    assert captured_tree is not None
    assert captured_tree.end.called
    end_kwargs = captured_tree.end.call_args.kwargs
    outputs = end_kwargs.get("outputs", {})
    assert outputs.get("status") == "SUCCESS"
    assert outputs.get("retrieved_chunk_count") == len(chunks)
    assert outputs.get("retrieved_chunk_count") >= 1
    assert outputs.get("is_complete") is True
    assert outputs.get("component_name") == "policy_corpus_repository"
    assert outputs.get("operation_name") == "query_chunks"
    assert outputs.get("run_type") == "retriever"


# ── Test 2: Span Hierarchy Under Workflow Coordinator ─────────────────────────
def test_policy_retrieval_tracing_nested_under_policy_coverage_rag_node():
    """Verifies that policy_corpus_query is nested under policy_coverage_rag_node and case_workflow_execute."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage, _ = _setup_coordinator_environment()
    case_id = "case-pol-rag-trace-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value
    )

    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])
    span_sequence: list[str] = []

    def mock_trace(name, **kwargs):
        span_sequence.append(name)
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.MagicMock()
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        result = coordinator.execute(state)

    assert result["decision"] in ("APPROVE", "ESCALATE", "REJECT")
    assert "policy_corpus_query" in span_sequence
    assert span_sequence[0] == "case_workflow_execute"

    # Assert policy_corpus_query appears after policy_coverage_rag_node in execution sequence
    cov_idx = span_sequence.index("policy_coverage_rag_node")
    retrieval_indices = [i for i, s in enumerate(span_sequence) if s == "policy_corpus_query"]
    assert len(retrieval_indices) >= 1
    for r_idx in retrieval_indices:
        assert r_idx > cov_idx


# ── Test 3: Safe Trace Summary Conforms to ALLOWED_TRACE_KEYS ─────────────────
def test_policy_retrieval_safe_trace_summary_allowed_keys_only():
    """Validates that all emitted trace payload keys strictly belong to the allowed telemetry schema."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    corpus = _build_test_corpus()
    captured_outputs: dict[str, Any] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        tree = mock.MagicMock()
        def end_fn(**end_kw):
            nonlocal captured_outputs
            captured_outputs = end_kw.get("outputs", {})
        tree.end.side_effect = end_fn
        cm.__enter__.return_value = tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        corpus.query_chunks(
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            query_text="office visit",
            cpt_codes=["99213"],
        )

    assert len(captured_outputs) > 0
    for key in captured_outputs:
        assert key in ALLOWED_TRACE_KEYS, f"Unexpected unapproved telemetry key emitted: {key}"


# ── Test 4: Strict Sensitive Content Isolation (Canary Test) ───────────────────
def test_policy_retrieval_zero_sensitive_canary_leakage():
    """Asserts zero canary PHI, clinical queries, policy text, or policy IDs leak into trace payloads."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    corpus = PolicyCorpusRepository(chunks=[])
    corpus.add_chunk(
        PolicyChunkRecord(
            chunk_id="canary-chk-999",
            policy_id=CANARY_POLICY_ID,
            version_tag="v1.0",
            section_number="SEC-CANARY",
            section_title="Canary Oncology Underwriting Clause",
            chunk_text=CANARY_CHUNK_TEXT,
            page_number=99,
            is_exclusion=False,
            requires_prior_auth=False,
            covered_percentage=100.0,
            deductible_limit=0.0,
            copay_percentage=0.0,
            matching_cpt_codes=["99215"],
        )
    )

    all_trace_data: list[str] = []

    def mock_trace(name, **kwargs):
        all_trace_data.append(str(name))
        all_trace_data.append(str(kwargs))
        cm = mock.MagicMock()
        tree = mock.MagicMock()
        def end_fn(**end_kw):
            all_trace_data.append(str(end_kw))
        tree.end.side_effect = end_fn
        cm.__enter__.return_value = tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        corpus.query_chunks(
            policy_id=CANARY_POLICY_ID,
            version_tag="v1.0",
            query_text=CANARY_QUERY,
            cpt_codes=["99215"],
        )

    serialized = " ".join(all_trace_data)
    assert CANARY_PATIENT not in serialized
    assert CANARY_QUERY not in serialized
    assert CANARY_POLICY_ID not in serialized
    assert CANARY_CHUNK_TEXT not in serialized
    assert "Alice" not in serialized
    assert "pancreatic" not in serialized
    assert "Gemcitabine" not in serialized
    assert "99215" not in serialized
    assert "SECRET_CANARY" not in serialized


# ── Test 5: Functional Invariance (Tracing Enabled vs Disabled) ───────────────
def test_policy_retrieval_tracing_enabled_vs_disabled_behavior_identical():
    """Verifies that retrieval returns identical chunk IDs, ordering, and count whether tracing is on or off."""
    corpus = _build_test_corpus()

    # 1. Tracing disabled
    settings.LANGSMITH_TRACING = False
    os.environ["LANGSMITH_TRACING"] = "false"
    chunks_disabled = corpus.query_chunks(
        policy_id="POL-CORP-2026",
        version_tag="v2.0",
        query_text="outpatient diagnostic electrocardiogram",
        cpt_codes=["99213", "93000"],
        top_k=5,
    )

    # 2. Tracing enabled
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"
    chunks_enabled = corpus.query_chunks(
        policy_id="POL-CORP-2026",
        version_tag="v2.0",
        query_text="outpatient diagnostic electrocardiogram",
        cpt_codes=["99213", "93000"],
        top_k=5,
    )

    assert len(chunks_disabled) == len(chunks_enabled)
    assert len(chunks_disabled) > 0
    for c_dis, c_en in zip(chunks_disabled, chunks_enabled):
        assert c_dis.chunk_id == c_en.chunk_id
        assert c_dis.policy_id == c_en.policy_id
        assert c_dis.version_tag == c_en.version_tag
        assert c_dis.chunk_text == c_en.chunk_text
        assert c_dis.matching_cpt_codes == c_en.matching_cpt_codes


# ── Test 6: Empty Candidates and No Match Scenarios ───────────────────────────
def test_policy_retrieval_empty_candidates_or_no_match():
    """Asserts that queries with no matching candidates or scores emit status='SUCCESS' and count=0."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    corpus = _build_test_corpus()
    captured_outputs: list[dict[str, Any]] = []

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        tree = mock.MagicMock()
        def end_fn(**end_kw):
            captured_outputs.append(end_kw.get("outputs", {}))
        tree.end.side_effect = end_fn
        cm.__enter__.return_value = tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        # Case A: Nonexistent policy_id / version_tag
        res_a = corpus.query_chunks(
            policy_id="POL-NONEXISTENT",
            version_tag="v99.9",
            query_text="office visit",
        )
        assert res_a == []

        # Case B: Matching policy version exists, but zero keyword / CPT overlap (score < 1.0)
        res_b = corpus.query_chunks(
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            query_text="unrelated irrelevant search text with no clinical terms",
            cpt_codes=["99999"],
        )
        assert res_b == []

    assert len(captured_outputs) == 2
    for out in captured_outputs:
        assert out.get("status") == "SUCCESS"
        assert out.get("retrieved_chunk_count") == 0
        assert out.get("is_complete") is False


# ── Test 7: Exception Propagation and Normalized Error Recording ──────────────
def test_policy_retrieval_exception_propagation_and_recording():
    """Verifies that unhandled exceptions propagate untouched while recording normalized telemetry."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    corpus = _build_test_corpus()
    captured_tree: mock.MagicMock | None = None

    def mock_trace(name, **kwargs):
        nonlocal captured_tree
        cm = mock.MagicMock()
        captured_tree = mock.MagicMock()
        cm.__enter__.return_value = captured_tree
        return cm

    # Corrupt corpus._chunks to trigger an internal AttributeError/TypeError
    canary_err_chunk = mock.MagicMock()
    canary_err_chunk.policy_id = "POL-CORP-2026"
    canary_err_chunk.version_tag = "v2.0"
    canary_err_chunk.matching_cpt_codes = None  # Causes TypeError when set() called
    corpus._chunks = [canary_err_chunk]

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        pytest.raises(TypeError),
    ):
        corpus.query_chunks(
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            query_text="office visit",
            cpt_codes=["99213"],
        )

    assert captured_tree is not None
    assert captured_tree.end.called
    end_kwargs = captured_tree.end.call_args.kwargs
    outputs = end_kwargs.get("outputs", {})
    assert outputs.get("status") == "FAILED"
    assert outputs.get("error_category") == "INTERNAL_ERROR"
    assert outputs.get("error_type") == "TypeError"


# ── Test 8: Telemetry Transport Error Does Not Break Retrieval ────────────────
def test_policy_retrieval_telemetry_transport_error_does_not_break_retrieval():
    """Verifies that an external LangSmith network/transport failure does not disrupt query_chunks."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    corpus = _build_test_corpus()

    def buggy_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        mock_tree.end.side_effect = ConnectionError("LangSmith network endpoint unreachable (simulated transport drop)")
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=buggy_trace):
        chunks = corpus.query_chunks(
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            query_text="outpatient office visit",
            cpt_codes=["99213"],
        )

    # Retrieval must succeed and return the policy chunks despite telemetry transport failure
    assert len(chunks) > 0
    assert chunks[0].chunk_id == "chk-001"


# ── Test 9: Repeated Retrieval Invocations Create Distinct Spans ──────────────
def test_policy_retrieval_repeated_invocations_create_distinct_spans():
    """Asserts that repeated query_chunks calls emit separate child spans without duplicate root traces."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    corpus = _build_test_corpus()
    emitted_spans: list[str] = []

    def mock_trace(name, **kwargs):
        emitted_spans.append(name)
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.MagicMock()
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        chunks1 = corpus.query_chunks(
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            query_text="outpatient office visit",
            cpt_codes=["99213"],
        )
        chunks2 = corpus.query_chunks(
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            query_text="routine electrocardiogram",
            cpt_codes=["93000"],
        )

    assert len(chunks1) > 0
    assert len(chunks2) > 0
    assert emitted_spans.count("policy_corpus_query") == 2


# ── Test 10: Disabled Tracing Zero Credentials and Zero Network ───────────────
def test_policy_retrieval_disabled_tracing_requires_no_credentials():
    """Verifies that disabled tracing requires no API key, makes zero trace calls, and performs normally."""
    settings.LANGSMITH_TRACING = False
    settings.LANGSMITH_API_KEY = ""
    os.environ.pop("LANGSMITH_TRACING", None)
    os.environ.pop("LANGSMITH_API_KEY", None)

    corpus = _build_test_corpus()

    with mock.patch("langsmith.trace") as mock_trace_fn:
        chunks = corpus.query_chunks(
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            query_text="outpatient office visit",
            cpt_codes=["99213"],
        )

    assert len(chunks) > 0
    assert mock_trace_fn.call_count == 0
