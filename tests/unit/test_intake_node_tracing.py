"""
Acceptance unit tests for Step 2B: Intake Node Tracing Instrumentation.

Validates:
1. Each of the four intake nodes emits a span when tracing is enabled.
2. A node span is nested under case_workflow_execute during coordinator execution.
3. Inputs and outputs contain only approved operational summaries.
4. Synthetic sensitive canary values do not appear in the captured trace payload.
5. Tracing-disabled execution preserves the original return values and state changes.
6. Existing node exceptions still propagate with their original type and message.
7. Telemetry transport failures do not alter the node result or disrupt execution.
8. Repeated invocation creates a separate child span per invocation without duplicate root traces.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any
from unittest import mock

import pytest

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
from medishield.core.tracing import reset_tracing_client
from medishield.db.enums import DocumentType, WorkflowState
from medishield.intake.storage import InMemoryStorageBackend
from medishield.ocr.preprocessor import DefaultOCREngine, OCRPreprocessorEngine
from medishield.workflows.coordinator import CaseWorkflowCoordinator
from medishield.workflows.nodes import (
    case_completeness_node,
    classifier_node,
    ocr_preprocessor_node,
    split_group_node,
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


def _setup_test_environment() -> tuple[CaseWorkflowCoordinator, InMemoryStorageBackend, list[dict[str, Any]]]:
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
        policy_context_agent=policy_context_agent,
        policy_coverage_agent=policy_coverage_agent,
        policy_corpus=corpus,
        audit_ledger=audit_ledger,
    )
    return coordinator, storage, audit_ledger


def _upload_test_document(
    storage: InMemoryStorageBackend,
    case_id: str,
    doc_id: str,
    content_str: str,
    doc_type: str | None = None,
) -> DocumentReference:
    """Helper to seed storage and build DocumentReference."""
    key = f"cases/{case_id}/{doc_id}.txt"
    storage.put_bytes(key, content_str.encode("utf-8"), "text/plain")
    return DocumentReference(
        document_id=doc_id,
        object_key=key,
        mime_type="text/plain",
        checksum="dummy-hash",
        split_source_page_indices=[1],
        file_name=f"{doc_id}.txt",
        doc_type=doc_type,
    )


# ── Test 1: Each of the 4 Nodes Emits a Span When Tracing Enabled ─────────────
def test_each_intake_node_emits_span_when_enabled():
    """Asserts that each of the four intake nodes emits a span with its exact non-sensitive name."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    storage = InMemoryStorageBackend()
    case_id = "case-nodes-01"
    doc = _upload_test_document(
        storage, case_id, "doc-1", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc])

    emitted_spans: list[dict[str, Any]] = []

    def mock_trace(name, **kwargs):
        emitted_spans.append({"name": name, "kwargs": kwargs})
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.MagicMock()
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        # 1. OCR Preprocessor node
        state = ocr_preprocessor_node(state, storage)
        ocr_node_spans = [s for s in emitted_spans if s["name"] == "ocr_preprocessor_node"]
        assert len(ocr_node_spans) == 1
        assert ocr_node_spans[0]["name"] == "ocr_preprocessor_node"
        assert ocr_node_spans[0]["kwargs"]["run_type"] == "chain"
        assert ocr_node_spans[0]["kwargs"]["inputs"] == {"document_count": 1}

        # 2. Split group node
        state = split_group_node(state)
        split_spans = [s for s in emitted_spans if s["name"] == "split_group_node"]
        assert len(split_spans) == 1
        assert split_spans[0]["name"] == "split_group_node"
        assert split_spans[0]["kwargs"]["run_type"] == "chain"
        assert split_spans[0]["kwargs"]["inputs"] == {"document_count": 1}

        # 3. Classifier node
        state = classifier_node(state, storage)
        clf_spans = [s for s in emitted_spans if s["name"] == "classifier_node"]
        assert len(clf_spans) == 1
        assert clf_spans[0]["name"] == "classifier_node"
        assert clf_spans[0]["kwargs"]["run_type"] == "chain"
        assert clf_spans[0]["kwargs"]["inputs"] == {"document_count": 1}

        # 4. Case completeness node
        state = case_completeness_node(state)
        comp_spans = [s for s in emitted_spans if s["name"] == "case_completeness_node"]
        assert len(comp_spans) == 1
        assert comp_spans[0]["name"] == "case_completeness_node"
        assert comp_spans[0]["kwargs"]["run_type"] == "chain"
        assert comp_spans[0]["kwargs"]["inputs"] == {"document_count": 1}


# ── Test 2: Node Spans Nested Under case_workflow_execute ─────────────────────
def test_node_spans_nested_under_case_workflow_execute():
    """Validates that during coordinator execution, all 4 intake nodes are invoked within the root trace."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage, _ = _setup_test_environment()
    case_id = "case-nested-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])

    trace_sequence: list[str] = []

    def mock_trace(name, **kwargs):
        trace_sequence.append(name)
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.MagicMock()
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        coordinator.execute(state)

    # Root span followed by four intake child spans in deterministic sequence
    assert trace_sequence[0] == "case_workflow_execute"
    intake_workflow_spans = [
        s for s in trace_sequence
        if s in [
            "ocr_preprocessor_node",
            "split_group_node",
            "classifier_node",
            "case_completeness_node",
        ]
    ]
    assert intake_workflow_spans == [
        "ocr_preprocessor_node",
        "split_group_node",
        "classifier_node",
        "case_completeness_node",
    ]


# ── Test 3: Inputs & Outputs Contain Only Approved Operational Summaries ─────
def test_intake_nodes_contain_only_approved_summaries():
    """Asserts that each node's inputs and outputs contain only safe operational telemetry."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    storage = InMemoryStorageBackend()
    case_id = "case-summaries-01"
    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])

    span_trees: dict[str, Any] = {}
    span_inputs: dict[str, Any] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        span_trees[name] = mock_tree
        span_inputs[name] = kwargs.get("inputs", {})
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        state = ocr_preprocessor_node(state, storage)
        state = split_group_node(state)
        state = classifier_node(state, storage)
        state = case_completeness_node(state)

    # 1. Check OCR preprocessor summary
    ocr_outputs = span_trees["ocr_preprocessor_node"].end.call_args.kwargs.get("outputs") or {}
    assert ocr_outputs.get("status") == "SUCCESS"
    assert ocr_outputs.get("document_count") == 2
    assert "duration_ms" in ocr_outputs

    # 2. Check Split group summary
    split_outputs = span_trees["split_group_node"].end.call_args.kwargs.get("outputs") or {}
    assert split_outputs.get("status") == "SUCCESS"
    assert split_outputs.get("document_count") == 2

    # 3. Check Classifier summary
    clf_outputs = span_trees["classifier_node"].end.call_args.kwargs.get("outputs") or {}
    assert clf_outputs.get("status") == "SUCCESS"
    assert clf_outputs.get("document_count") == 2
    assert clf_outputs.get("classification_completed") is True

    # 4. Check Completeness summary
    comp_outputs = span_trees["case_completeness_node"].end.call_args.kwargs.get("outputs") or {}
    assert comp_outputs.get("status") == "SUCCESS"
    assert comp_outputs.get("document_count") == 2
    assert comp_outputs.get("is_complete") is True
    assert comp_outputs.get("verdict") == "PASS"

    # Verify absence of sensitive keys across all node inputs and outputs
    prohibited = {"case_id", "patient_name", "ssn", "cpt", "amount", "notes", "file_name", "file_path"}
    for name in ("ocr_preprocessor_node", "split_group_node", "classifier_node", "case_completeness_node"):
        inp = span_inputs[name]
        out = span_trees[name].end.call_args.kwargs.get("outputs") or {}
        for p in prohibited:
            assert p not in inp, f"{p} found in inputs of {name}"
            assert p not in out, f"{p} found in outputs of {name}"


# ── Test 4: Zero Sensitive Canary Leakage ─────────────────────────────────────
def test_intake_nodes_zero_sensitive_canary_leakage():
    """Asserts that canary PHI strings in state or document objects never leak into telemetry payloads."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    storage = InMemoryStorageBackend()
    case_id = "case-canary-intake-01"
    canary_ssn = "999-00-1111"
    canary_name = "Alice Montgomery-Smith"
    canary_amount = "$45,210.50"

    doc = _upload_test_document(
        storage, case_id, "doc-canary", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc])
    state["canary_ssn"] = canary_ssn
    state["canary_name"] = canary_name
    state["canary_amount"] = canary_amount

    captured_payloads: list[str] = []

    def mock_trace(name, **kwargs):
        captured_payloads.append(str(kwargs))
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()

        def capture_end(*args, **end_kwargs):
            captured_payloads.append(str(end_kwargs))

        mock_tree.end.side_effect = capture_end
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        state = ocr_preprocessor_node(state, storage)
        state = split_group_node(state)
        state = classifier_node(state, storage)
        state = case_completeness_node(state)

    combined_telemetry = "\n".join(captured_payloads)
    assert canary_ssn not in combined_telemetry
    assert canary_name not in combined_telemetry
    assert canary_amount not in combined_telemetry
    assert case_id not in combined_telemetry


# ── Test 5: Tracing Disabled Preserves Original Returns and State ─────────────
def test_intake_nodes_tracing_disabled_preserves_state_and_returns():
    """Verifies that with tracing disabled, intake nodes produce identical state transitions and zero trace calls."""
    settings.LANGSMITH_TRACING = False

    storage = InMemoryStorageBackend()
    case_id = "case-disabled-intake-01"
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
        state = ocr_preprocessor_node(state, storage)
        state = split_group_node(state)
        state = classifier_node(state, storage)
        state = case_completeness_node(state)

    assert not trace_called
    assert state["workflow_state"] == WorkflowState.ANALYZING.value
    assert state["is_complete"] is True
    assert "ocr_preprocessor_node" in state["completed_nodes"]
    assert "split_group_node" in state["completed_nodes"]
    assert "classifier_node" in state["completed_nodes"]
    assert "case_completeness_node" in state["completed_nodes"]


# ── Test 6: Node Exceptions Propagate and Record Safely ───────────────────────
def test_intake_nodes_exceptions_propagate_and_record_safely():
    """Verifies that unhandled node exceptions propagate unchanged while recording safe normalized telemetry."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    storage = InMemoryStorageBackend()
    case_id = "case-error-intake-01"
    doc = _upload_test_document(
        storage, case_id, "doc-err", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc])

    captured_tree = None

    def mock_trace(name, **kwargs):
        nonlocal captured_tree
        cm = mock.MagicMock()
        captured_tree = mock.MagicMock()
        cm.__enter__.return_value = captured_tree
        return cm

    canary_msg = "Classifier parse error for patient Alice Montgomery-Smith SSN 999-00-1111"
    mock_agent = mock.MagicMock(spec=DocumentClassifierAgent)
    mock_agent.classify.side_effect = ValueError(canary_msg)

    # Mark document as unknown to trigger agent.classify
    state["documents"][0]["doc_type"] = DocumentType.UNKNOWN.value

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        pytest.raises(ValueError, match="Classifier parse error for patient Alice Montgomery-Smith"),
    ):
        classifier_node(state, storage, classifier_agent=mock_agent)

    assert captured_tree is not None
    end_kwargs = captured_tree.end.call_args.kwargs
    outputs = end_kwargs.get("outputs") or {}
    error_str = end_kwargs.get("error")

    assert outputs.get("status") == "FAILED"
    assert outputs.get("error_category") == "VALIDATION_ERROR"
    assert outputs.get("error_type") == "ValueError"

    assert "999-00-1111" not in str(error_str)
    assert "Alice Montgomery-Smith" not in str(error_str)
    assert error_str == "VALIDATION_ERROR: ValueError"


# ── Test 7: Telemetry Error Does Not Alter Node Result ─────────────────────────
def test_intake_nodes_telemetry_error_does_not_alter_result():
    """Verifies that an internal LangSmith telemetry exception does not disrupt node execution."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    storage = InMemoryStorageBackend()
    case_id = "case-telemetry-err-01"
    doc = _upload_test_document(
        storage, case_id, "doc-1", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc])

    def crashing_trace(name, **kwargs):
        raise RuntimeError("LangSmith connection timed out")

    with mock.patch("langsmith.trace", side_effect=crashing_trace):
        # Nodes must complete normally despite telemetry crash
        state = ocr_preprocessor_node(state, storage)
        assert state["workflow_state"] == WorkflowState.SCANNING.value

        state = split_group_node(state)
        assert state["workflow_state"] == WorkflowState.SPLIT.value

        state = classifier_node(state, storage)
        assert state["workflow_state"] == WorkflowState.CLASSIFYING.value

        state = case_completeness_node(state)
        assert state["workflow_state"] in (WorkflowState.AWAITING_INFO.value, WorkflowState.ANALYZING.value)


# ── Test 8: Repeated Invocation Creates Separate Child Spans ──────────────────
def test_intake_nodes_repeated_invocation_creates_separate_spans():
    """Verifies that repeated invocation creates separate child spans per invocation without duplicate root traces."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    storage = InMemoryStorageBackend()
    case_id = "case-repeated-01"
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
        state = split_group_node(state)
        state = split_group_node(state)

    assert len(emitted_spans) == 2
    assert emitted_spans == ["split_group_node", "split_group_node"]


# ── Regression Tests: Baseline Classifier & Completeness Behavior ─────────────
@pytest.mark.parametrize("tracing_enabled", [True, False])
def test_classifier_baseline_condition_preserved(tracing_enabled: bool):
    """
    Regression test: Verifies that classifier_node strictly adheres to the baseline condition:
    documents already classified with verified confidence (> 0.0) are NOT reclassified,
    while untyped (UNKNOWN) or unverified (confidence is None or 0.0) documents ARE classified.
    This behavior is 100% identical with tracing enabled and disabled.
    """
    settings.LANGSMITH_TRACING = tracing_enabled
    if tracing_enabled:
        settings.LANGSMITH_API_KEY = "test-key"
        os.environ["LANGSMITH_TRACING"] = "true"
    else:
        os.environ["LANGSMITH_TRACING"] = "false"

    storage = InMemoryStorageBackend()
    case_id = f"case-reg-clf-{tracing_enabled}"

    # 1. Pre-classified document with verified confidence (must NOT be reclassified)
    doc_pre = _upload_test_document(
        storage, case_id, "doc-pre", SAMPLE_ID_TEXT, doc_type=DocumentType.ID_DOCUMENT.value
    )
    doc_pre["classification_confidence"] = 0.95

    # 2. Unknown document that requires classification
    doc_untyped = _upload_test_document(
        storage, case_id, "doc-untyped", SAMPLE_CLAIM_TEXT, doc_type=DocumentType.UNKNOWN.value
    )
    doc_untyped["classification_confidence"] = None

    state = create_initial_case_state(case_id=case_id, documents=[doc_pre, doc_untyped])

    from medishield.db.enums import AgentVerdict
    from medishield.domain.contracts import ClassifierAgentOutput
    mock_agent = mock.MagicMock(spec=DocumentClassifierAgent)
    mock_agent.classify.return_value = ClassifierAgentOutput(
        document_id="doc-untyped",
        doc_type=DocumentType.CLAIM_FORM,
        confidence=0.92,
        routing_tags=["claim"],
        verdict=AgentVerdict.PASS,
        latency_ms=10,
    )

    with mock.patch("langsmith.trace") as mock_trace_fn:
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.MagicMock()
        mock_trace_fn.return_value = cm
        state = classifier_node(state, storage, classifier_agent=mock_agent)

    # Classify should be called ONLY ONCE for doc_untyped, never for doc_pre
    assert mock_agent.classify.call_count == 1
    call_doc_id = mock_agent.classify.call_args.kwargs.get("document_id")
    assert call_doc_id == "doc-untyped"

    # doc_pre doc_type is untouched
    assert state["documents"][0]["doc_type"] == DocumentType.ID_DOCUMENT.value
    assert state["documents"][0]["classification_confidence"] == 0.95
    # doc_untyped was classified
    assert state["documents"][1]["doc_type"] == DocumentType.CLAIM_FORM.value
    assert state["documents"][1]["classification_confidence"] == 0.92


# ── Regression Test Matrix: All 5 Document States Across Tracing Enabled/Disabled ─
@pytest.mark.parametrize("tracing_enabled", [True, False])
@pytest.mark.parametrize(
    ("case_label", "initial_doc_type", "initial_confidence", "expected_invoked"),
    [
        # 1. Existing known document type with confidence 0.95 -> NOT invoked
        ("known_type_conf_0.95", DocumentType.ID_DOCUMENT.value, 0.95, False),
        # 2. Existing known document type with confidence None -> IS invoked
        ("known_type_conf_none", DocumentType.ID_DOCUMENT.value, None, True),
        # 3. Existing known document type with confidence 0.0 -> IS invoked
        ("known_type_conf_zero", DocumentType.CLAIM_FORM.value, 0.0, True),
        # 4. Unknown document type -> IS invoked
        ("unknown_type", DocumentType.UNKNOWN.value, 0.95, True),
        # 5. Missing document type (None) -> IS invoked
        ("missing_type_none", None, None, True),
        # 6. Missing document type (key absent) -> IS invoked
        ("missing_type_key_absent", "OMIT_KEY", None, True),
    ],
)
def test_classifier_baseline_document_states_matrix(
    tracing_enabled: bool,
    case_label: str,
    initial_doc_type: str | None,
    initial_confidence: float | None,
    expected_invoked: bool,
):
    """
    Validates that classifier_node strictly adheres to the verified pre-instrumentation baseline:
      if not current_type or current_type == DocumentType.UNKNOWN.value or current_conf is None or current_conf == 0.0:

    Verifies all 5 required document states:
      - Existing known document type with confidence 0.95 (not invoked)
      - Existing known document type with confidence None (invoked)
      - Existing known document type with confidence 0.0 (invoked)
      - Unknown document type (invoked)
      - Missing document type (invoked)

    Proves that classifier invocation behaviour is 100% identical whether tracing is enabled or disabled.
    """
    settings.LANGSMITH_TRACING = tracing_enabled
    if tracing_enabled:
        settings.LANGSMITH_API_KEY = "test-key"
        os.environ["LANGSMITH_TRACING"] = "true"
    else:
        os.environ["LANGSMITH_TRACING"] = "false"

    storage = InMemoryStorageBackend()
    case_id = f"case-matrix-{case_label}-{tracing_enabled}"
    doc_id = f"doc-{case_label}"

    doc = _upload_test_document(
        storage,
        case_id,
        doc_id,
        SAMPLE_ID_TEXT,
        doc_type=initial_doc_type if initial_doc_type != "OMIT_KEY" else None,
    )
    if initial_doc_type == "OMIT_KEY":
        doc.pop("doc_type", None)
    else:
        doc["doc_type"] = initial_doc_type
    doc["classification_confidence"] = initial_confidence

    state = create_initial_case_state(case_id=case_id, documents=[doc])

    from medishield.db.enums import AgentVerdict
    from medishield.domain.contracts import ClassifierAgentOutput

    mock_agent = mock.MagicMock(spec=DocumentClassifierAgent)
    mock_agent.classify.return_value = ClassifierAgentOutput(
        document_id=doc_id,
        doc_type=DocumentType.PRESCRIPTION,
        confidence=0.88,
        routing_tags=["rx"],
        verdict=AgentVerdict.PASS,
        latency_ms=12,
    )

    with mock.patch("langsmith.trace") as mock_trace_fn:
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.MagicMock()
        mock_trace_fn.return_value = cm
        res_state = classifier_node(state, storage, classifier_agent=mock_agent)

    if expected_invoked:
        assert mock_agent.classify.call_count == 1
        assert mock_agent.classify.call_args.kwargs.get("document_id") == doc_id
        assert res_state["documents"][0]["doc_type"] == DocumentType.PRESCRIPTION.value
        assert res_state["documents"][0]["classification_confidence"] == 0.88
    else:
        assert mock_agent.classify.call_count == 0
        assert res_state["documents"][0]["doc_type"] == initial_doc_type
        assert res_state["documents"][0]["classification_confidence"] == initial_confidence


@pytest.mark.parametrize("tracing_enabled", [True, False])
def test_case_completeness_baseline_mutations_preserved(tracing_enabled: bool):
    """
    Regression test: Verifies that case_completeness_node retains all baseline state mutations
    including completed_nodes, is_complete, decision, and escalation_sub_reason.
    """
    settings.LANGSMITH_TRACING = tracing_enabled
    if tracing_enabled:
        settings.LANGSMITH_API_KEY = "test-key"
        os.environ["LANGSMITH_TRACING"] = "true"
    else:
        os.environ["LANGSMITH_TRACING"] = "false"

    storage = InMemoryStorageBackend()
    case_id = f"case-reg-comp-{tracing_enabled}"

    # 1. Complete case (ID + Claim)
    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, doc_type=DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, doc_type=DocumentType.CLAIM_FORM.value
    )
    state_complete = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])

    # 2. Incomplete case (missing ID document)
    doc_claim_only = _upload_test_document(
        storage, case_id, "doc-claim-2", SAMPLE_CLAIM_TEXT, doc_type=DocumentType.CLAIM_FORM.value
    )
    state_incomplete = create_initial_case_state(case_id=case_id, documents=[doc_claim_only])

    with mock.patch("langsmith.trace") as mock_trace_fn:
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.MagicMock()
        mock_trace_fn.return_value = cm

        res_complete = case_completeness_node(state_complete)
        assert res_complete["is_complete"] is True
        assert res_complete["workflow_state"] == WorkflowState.ANALYZING.value
        assert "case_completeness_node" in res_complete["completed_nodes"]

        res_incomplete = case_completeness_node(state_incomplete)
        assert res_incomplete["is_complete"] is False
        assert res_incomplete["workflow_state"] == WorkflowState.AWAITING_INFO.value
        assert res_incomplete["decision"] == "ESCALATE"
        assert res_incomplete["escalation_sub_reason"] == "MISSING_INFORMATION"
        assert "case_completeness_node" in res_incomplete["completed_nodes"]

