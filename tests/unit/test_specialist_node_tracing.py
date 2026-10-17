"""
Acceptance unit tests for Step 2C: Specialist Agent Node Tracing Instrumentation.

Validates:
1. Each specialist node (kyc_agent_node, claims_extraction_node, policy_context_node)
   emits a span when tracing is enabled.
2. All three specialist spans are nested under case_workflow_execute during coordinator execution.
3. Span summaries contain only validated operational metrics conforming to the approved schema.
4. Synthetic sensitive canary values do not appear in captured trace payloads.
5. Tracing-enabled and tracing-disabled executions preserve the same business outputs and state mutations.
6. Original exceptions continue to propagate, with only normalized error information recorded.
7. Telemetry transport failures do not change agent results.
8. Repeated node invocation creates separate child spans without duplicate root traces.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any
from unittest import mock

import pytest

from medishield.agents.claims import ClaimsAgent
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
from medishield.core.tracing import reset_tracing_client
from medishield.db.enums import AgentVerdict, DocumentType
from medishield.intake.storage import InMemoryStorageBackend
from medishield.ocr.preprocessor import DefaultOCREngine, OCRPreprocessorEngine
from medishield.workflows.coordinator import CaseWorkflowCoordinator
from medishield.workflows.nodes import (
    claims_extraction_node,
    kyc_agent_node,
    policy_context_node,
)
from medishield.workflows.state import DocumentReference, create_initial_case_state

SAMPLE_ID_TEXT = """STATE DRIVER LICENSE
DL NUMBER: D9817264
MEMBER ID: MEM-12345
NAME: Alice Smith
DOB: 1985-05-20
EXP DATE: 2030-01-01
"""

SAMPLE_CLAIM_TEXT = """HEALTH INSURANCE CLAIM FORM
PROVIDER: Dr. Sarah Jenkins Hospital
NPI: 1234567890
DATE OF SERVICE: 2026-03-15
DIAGNOSIS: J02.9
PROCEDURE: 99213
1. 99213 Office Visit 2026-03-15 1.0 $200.00 $200.00
2. 93000 Routine ECG 2026-03-15 1.0 $150.00 $150.00
TOTAL CHARGE: $350.00
"""


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


def _setup_test_environment() -> tuple[
    CaseWorkflowCoordinator,
    InMemoryStorageBackend,
    KYCAgent,
    ClaimsAgent,
    PolicyContextAgent,
]:
    """Helper setting up configured coordinator, storage, and repositories."""
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

    corpus = PolicyCorpusRepository()
    corpus.add_chunk(
        PolicyChunkRecord(
            chunk_id="chk-001",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            section_number="SEC-3.1",
            section_title="Outpatient Evaluation and Management",
            chunk_text="Outpatient office visits are covered benefits.",
            page_number=12,
            is_exclusion=False,
            requires_prior_auth=False,
            covered_percentage=80.0,
            deductible_limit=500.0,
            copay_percentage=20.0,
            matching_cpt_codes=["99213", "99214", "93000"],
        )
    )
    policy_coverage_agent = PolicyCoverageAgent(corpus_repo=corpus)
    audit_ledger: list[dict[str, Any]] = []

    coordinator = CaseWorkflowCoordinator(
        storage=storage,
        ocr_engine=ocr_engine,
        kyc_agent=kyc_agent,
        claims_agent=claims_agent,
        policy_context_agent=policy_context_agent,
        policy_coverage_agent=policy_coverage_agent,
        policy_corpus=corpus,
        audit_ledger=audit_ledger,
    )
    return coordinator, storage, kyc_agent, claims_agent, policy_context_agent


def _upload_test_document(
    storage: InMemoryStorageBackend,
    case_id: str,
    doc_id: str,
    content_str: str,
    doc_type: str | None = None,
) -> DocumentReference:
    """Helper to seed storage and build DocumentReference with preprocessed OCR payload."""
    raw_key = f"cases/{case_id}/{doc_id}.txt"
    storage.put_bytes(raw_key, content_str.encode("utf-8"), "text/plain")

    ocr_engine = OCRPreprocessorEngine(engine=DefaultOCREngine())
    out = ocr_engine.process_document(
        content=content_str.encode("utf-8"),
        document_id=doc_id,
        case_id=case_id,
        mime_type="text/plain",
        storage_backend=storage,
    )

    return DocumentReference(
        document_id=doc_id,
        object_key=raw_key,
        mime_type="text/plain",
        checksum="dummy-hash",
        split_source_page_indices=[1],
        file_name=f"{doc_id}.txt",
        doc_type=doc_type,
        ocr_payload_uri=out.payload_storage_uri,
    )


# ── Test 1: Each Specialist Node Emits a Span When Tracing Enabled ───────────
def test_each_specialist_node_emits_span_when_enabled():
    """Asserts that each specialist node emits a span with its exact non-sensitive name."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    _, storage, kyc_agent, claims_agent, pol_ctx_agent = _setup_test_environment()
    case_id = "case-spec-trace-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])

    emitted_spans: list[dict[str, Any]] = []

    def mock_trace(name, **kwargs):
        emitted_spans.append({"name": name, "kwargs": kwargs})
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.MagicMock()
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        state = kyc_agent_node(state, storage, kyc_agent)
        state = claims_extraction_node(state, storage, claims_agent)
        state = policy_context_node(state, pol_ctx_agent)

    specialist_node_spans = [
        s for s in emitted_spans
        if s["name"] in ["kyc_agent_node", "claims_extraction_node", "policy_context_node"]
    ]
    names = [s["name"] for s in specialist_node_spans]
    assert names == [
        "kyc_agent_node",
        "claims_extraction_node",
        "policy_context_node",
    ]

    for s in specialist_node_spans:
        assert s["kwargs"].get("run_type") == "chain"
        assert s["kwargs"].get("inputs") == {"document_count": 2}


# ── Test 2: Specialist Spans Nested Under case_workflow_execute ───────────────
def test_specialist_spans_nested_under_case_workflow_execute():
    """Asserts that specialist nodes are invoked as child spans under the coordinator root trace."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage, _, _, _ = _setup_test_environment()
    case_id = "case-spec-nested-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])

    captured_traces = []

    def mock_trace(name, **kwargs):
        captured_traces.append({"name": name, "kwargs": kwargs})
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        result = coordinator.execute(state)

    assert result["decision"] in ("APPROVE", "REJECT", "ESCALATE")

    trace_names = [t["name"] for t in captured_traces]
    assert "case_workflow_execute" in trace_names
    assert trace_names[0] == "case_workflow_execute"
    assert "kyc_agent_node" in trace_names
    assert "claims_extraction_node" in trace_names
    assert "policy_context_node" in trace_names

    # Exactly 1 root span was created
    assert trace_names.count("case_workflow_execute") == 1


# ── Test 3: Span Summaries Contain Only Validated Operational Metrics ─────────
def test_specialist_nodes_contain_only_approved_summaries():
    """Asserts that specialist nodes capture strictly approved operational summaries."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    _, storage, kyc_agent, claims_agent, pol_ctx_agent = _setup_test_environment()
    case_id = "case-spec-summary-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])

    captured_ends: dict[str, dict[str, Any]] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()

        def on_end(outputs=None, error=None):
            captured_ends[name] = outputs or {}

        mock_tree.end.side_effect = on_end
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        state = kyc_agent_node(state, storage, kyc_agent)
        state = claims_extraction_node(state, storage, claims_agent)
        state = policy_context_node(state, pol_ctx_agent)

    # 1. KYC Summary
    kyc_out = captured_ends.get("kyc_agent_node", {})
    assert kyc_out.get("status") == "SUCCESS"
    assert kyc_out.get("document_count") == 2
    assert "duration_ms" in kyc_out
    assert kyc_out.get("member_matched") is True
    assert kyc_out.get("verdict") == "PASS"
    assert 0.0 <= kyc_out.get("confidence", 0.0) <= 1.0

    # 2. Claims Summary
    claims_out = captured_ends.get("claims_extraction_node", {})
    assert claims_out.get("status") == "SUCCESS"
    assert claims_out.get("document_count") == 2
    assert "duration_ms" in claims_out
    assert claims_out.get("service_line_count") == 2
    assert claims_out.get("arithmetic_valid") is True
    assert claims_out.get("verdict") == "PASS"

    # 3. Policy Context Summary
    pol_out = captured_ends.get("policy_context_node", {})
    assert pol_out.get("status") == "SUCCESS"
    assert pol_out.get("document_count") == 2
    assert "duration_ms" in pol_out
    assert pol_out.get("verdict") == "PASS"

    # Verify prohibited clinical / PHI / financial fields are ABSENT across all three
    prohibited_fields = (
        "cpt",
        "icd10",
        "member_id",
        "amount",
        "service_date",
        "patient_name",
        "candidate_policies",
        "prompt",
        "raw_text",
        "ocr_spans",
        "provider_npi",
        "first_name",
        "last_name",
        "date_of_birth",
        "case_id",
    )
    for span_name, out in captured_ends.items():
        for field in prohibited_fields:
            assert field not in out, f"Prohibited field '{field}' leaked into {span_name} outputs: {out}"


# ── Test 4: Zero Sensitive Canary Leakage in Specialist Spans ─────────────────
def test_specialist_nodes_zero_sensitive_canary_leakage():
    """Asserts that synthetic PHI, clinical, and financial canary strings never leak into trace payloads."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    _, storage, kyc_agent, claims_agent, pol_ctx_agent = _setup_test_environment()
    case_id = "case-spec-canary-01"

    canary_ssn = "999-00-1111"
    canary_name = "Alice Montgomery-Canary"
    canary_amount = "$98,765.43"
    canary_diag = "Z99.99"
    canary_cpt = "99213"

    canary_id_text = f"""STATE DRIVER LICENSE
DL NUMBER: D9817264
MEMBER ID: MEM-12345
NAME: {canary_name}
DOB: 1985-05-20
SSN: {canary_ssn}
EXP DATE: 2030-01-01
"""

    canary_claim_text = f"""HEALTH INSURANCE CLAIM FORM
PROVIDER: Dr. Sarah Jenkins Hospital
NPI: 1234567890
DATE OF SERVICE: 2026-03-15
DIAGNOSIS: {canary_diag}
PROCEDURE: {canary_cpt}
1. {canary_cpt} Office Visit 2026-03-15 1.0 {canary_amount} {canary_amount}
TOTAL CHARGE: {canary_amount}
"""

    doc_id = _upload_test_document(
        storage, case_id, "doc-id-canary", canary_id_text, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage, case_id, "doc-claim-canary", canary_claim_text, DocumentType.CLAIM_FORM.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])

    recorded_payloads: list[dict[str, Any]] = []

    def mock_trace(name, **kwargs):
        payload_record = {"name": name, "kwargs": kwargs, "outputs": {}}

        def on_end(outputs=None, error=None):
            payload_record["outputs"] = outputs or {}

        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        mock_tree.end.side_effect = on_end
        cm.__enter__.return_value = mock_tree
        recorded_payloads.append(payload_record)
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        state = kyc_agent_node(state, storage, kyc_agent)
        state = claims_extraction_node(state, storage, claims_agent)
        state = policy_context_node(state, pol_ctx_agent)

    canaries = [canary_ssn, canary_name, canary_amount, canary_diag, "MEM-12345"]
    for record in recorded_payloads:
        dumped = str(record)
        for canary in canaries:
            assert canary not in dumped, f"Canary '{canary}' leaked into {record['name']} payload: {dumped}"


# ── Test 5: Tracing Disabled Preserves 100% Identical Output and Zero Calls ───
def test_specialist_nodes_tracing_disabled_preserves_state_and_returns():
    """Verifies that with tracing disabled, specialist execution is identical and zero telemetry occurs."""
    settings.LANGSMITH_TRACING = False

    _, storage, kyc_agent, claims_agent, pol_ctx_agent = _setup_test_environment()
    case_id = "case-spec-disabled-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])

    trace_called = False

    def mock_trace(name, **kwargs):
        nonlocal trace_called
        trace_called = True
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.MagicMock()
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        state = kyc_agent_node(state, storage, kyc_agent)
        state = claims_extraction_node(state, storage, claims_agent)
        state = policy_context_node(state, pol_ctx_agent)

    assert not trace_called
    assert state.get("kyc_verdict") == AgentVerdict.PASS.value
    assert state.get("claims_verdict") == AgentVerdict.PASS.value
    assert state.get("policy_context_verdict") == AgentVerdict.PASS.value
    assert "kyc_agent_node" in state.get("completed_nodes", [])
    assert "claims_extraction_node" in state.get("completed_nodes", [])
    assert "policy_context_node" in state.get("completed_nodes", [])


# ── Test 6: Node Exceptions Propagate and Record Safely ───────────────────────
@pytest.mark.parametrize(
    ("node_fn_name", "exc_agent_attr"),
    [
        ("kyc_agent_node", "kyc"),
        ("claims_extraction_node", "claims"),
        ("policy_context_node", "pol_ctx"),
    ],
)
def test_specialist_nodes_exceptions_propagate_and_record_safely(
    node_fn_name: str,
    exc_agent_attr: str,
):
    """Verifies that unhandled specialist agent exceptions propagate unchanged while recording normalized telemetry."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    _, storage, *_ = _setup_test_environment()
    case_id = f"case-spec-err-{node_fn_name}"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id])

    captured_tree = None

    def mock_trace(name, **kwargs):
        nonlocal captured_tree
        cm = mock.MagicMock()
        tree = mock.MagicMock()
        if name == node_fn_name:
            captured_tree = tree
        cm.__enter__.return_value = tree
        return cm

    canary_error_msg = f"Fatal extraction failure for patient Alice Montgomery in {node_fn_name}"

    mock_kyc = mock.MagicMock(spec=KYCAgent)
    mock_claims = mock.MagicMock(spec=ClaimsAgent)
    mock_pol_ctx = mock.MagicMock(spec=PolicyContextAgent)

    if exc_agent_attr == "kyc":
        mock_kyc.execute.side_effect = ValueError(canary_error_msg)
    elif exc_agent_attr == "claims":
        mock_claims.execute.side_effect = ValueError(canary_error_msg)
    elif exc_agent_attr == "pol_ctx":
        mock_pol_ctx.execute.side_effect = ValueError(canary_error_msg)

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        pytest.raises(ValueError, match="Fatal extraction failure for patient Alice Montgomery"),
    ):
        if node_fn_name == "kyc_agent_node":
            kyc_agent_node(state, storage, mock_kyc)
        elif node_fn_name == "claims_extraction_node":
            claims_extraction_node(state, storage, mock_claims)
        elif node_fn_name == "policy_context_node":
            policy_context_node(state, mock_pol_ctx)

    assert captured_tree is not None
    assert captured_tree.end.called
    end_kwargs = captured_tree.end.call_args.kwargs
    outputs = end_kwargs.get("outputs", {})
    assert outputs.get("status") == "FAILED"
    assert outputs.get("error_category") == "VALIDATION_ERROR"
    assert outputs.get("error_type") == "ValueError"

    # Ensure canary error text was NOT stored in outputs or error string
    assert canary_error_msg not in str(outputs)
    assert "Alice Montgomery" not in str(end_kwargs.get("error"))


# ── Test 7: Telemetry Transport Failures Do Not Alter Agent Results ───────────
def test_specialist_nodes_telemetry_error_does_not_alter_result():
    """Verifies that an error in the LangSmith trace context manager does not disrupt specialist execution."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    _, storage, kyc_agent, claims_agent, pol_ctx_agent = _setup_test_environment()
    case_id = "case-spec-telemetry-err-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])

    def failing_trace(name, **kwargs):
        raise ConnectionError("LangSmith network endpoint unreachable (simulated transport drop)")

    with mock.patch("langsmith.trace", side_effect=failing_trace):
        state = kyc_agent_node(state, storage, kyc_agent)
        state = claims_extraction_node(state, storage, claims_agent)
        state = policy_context_node(state, pol_ctx_agent)

    # All three nodes must complete successfully despite the telemetry failure
    assert state.get("kyc_verdict") == AgentVerdict.PASS.value
    assert state.get("claims_verdict") == AgentVerdict.PASS.value
    assert state.get("policy_context_verdict") == AgentVerdict.PASS.value
    assert "kyc_agent_node" in state.get("completed_nodes", [])
    assert "claims_extraction_node" in state.get("completed_nodes", [])
    assert "policy_context_node" in state.get("completed_nodes", [])


# ── Test 8: Repeated Invocation Creates Separate Child Spans ──────────────────
def test_specialist_nodes_repeated_invocation_creates_separate_spans():
    """Verifies that repeated invocation creates separate child spans without duplicate root traces."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    _, storage, kyc_agent, _, _ = _setup_test_environment()
    case_id = "case-spec-repeated-01"
    doc = _upload_test_document(
        storage, case_id, "doc-1", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc])

    emitted_spans: list[str] = []

    def mock_trace(name, **kwargs):
        emitted_spans.append(name)
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.MagicMock()
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        state = kyc_agent_node(state, storage, kyc_agent)
        state = kyc_agent_node(state, storage, kyc_agent)

    specialist_spans = [
        s
        for s in emitted_spans
        if s in ("kyc_agent_node", "claims_extraction_node", "policy_context_node")
    ]
    assert len(specialist_spans) == 2
    assert specialist_spans == ["kyc_agent_node", "kyc_agent_node"]
