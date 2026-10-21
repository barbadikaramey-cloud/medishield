"""Regression tests for P6 Approval Safety Gate (Phase 2A, decision_quality_audit.md).

Proves that the Decision Engine cannot auto-approve when:
  1. No billed service lines are present.
  2. The source claim contains a service line missing from structured Claims output
     (PT_20322 scenario: CPT 70553 + 93000 on source, only 70553 in extraction).
  3. Any required service line lacks an applicable, verified coverage determination.
  4. A material, unresolved validation conflict exists.
  5. Critical validation output is missing or unavailable.
"""

from medishield.db.enums import (
    AgentVerdict,
    BusinessDecision,
    DocumentType,
)
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    ClinicalAlignmentOutput,
    FraudAgentOutput,
    KYCAgentOutput,
    PolicyCoverageOutput,
    ProvenanceValue,
    RiskLevel,
    ServiceLine,
    build_clinical_alignment_output,
)
from medishield.domain.decision import (
    RULE_P3_AMBIGUITY,
    RULE_P6_CLEAN_PASS,
    DecisionInputContext,
    DeterministicDecisionEngine,
)
from tests.unit.test_clinical_alignment import make_clean_line_evidence

# ── Fixture Helpers ───────────────────────────────────────────────────────────


def _kyc_pass(confidence: float = 0.95) -> KYCAgentOutput:
    return KYCAgentOutput(
        agent_name="kyc_agent",
        verdict=AgentVerdict.PASS,
        confidence=confidence,
        latency_ms=10,
    )


def _fraud_pass(score: float = 0.0, review_required: bool = False) -> FraudAgentOutput:
    return FraudAgentOutput(
        agent_name="fraud_agent",
        verdict=AgentVerdict.INDETERMINATE if review_required else AgentVerdict.PASS,
        confidence=1.0,
        fraud_score=1.0 if review_required else score,
        fraud_review_required=review_required,
        risk_level=RiskLevel.REVIEW_REQUIRED if review_required else RiskLevel.LOW,
        anomalies=["A deterministic fraud factor was detected"] if review_required else [],
        latency_ms=10,
    )


def _make_service_line(
    line_number: int,
    cpt: str,
    amount: float,
    service_date: str = "2026-09-15",
) -> ServiceLine:
    return ServiceLine(
        line_number=line_number,
        hcpcs=ProvenanceValue(value=cpt, confidence=0.96),
        service_date=ProvenanceValue(value=service_date, confidence=0.96),
        units=ProvenanceValue(value=1.0, confidence=0.95),
        amount=ProvenanceValue(value=amount, confidence=0.98),
    )


def _claims_two_lines() -> ClaimsAgentOutput:
    """Clean claim with two service lines: CPT 70553 ($1500) and CPT 93000 ($480)."""
    return ClaimsAgentOutput(
        agent_name="claims_agent",
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        claim_amount=ProvenanceValue(value="1980.00", confidence=0.98),
        service_date=ProvenanceValue(value="2026-09-15", confidence=0.96),
        provider_npi=ProvenanceValue(value="1234567890", confidence=0.95),
        cpt_codes=[
            ProvenanceValue(value="70553", confidence=0.96),
            ProvenanceValue(value="93000", confidence=0.96),
        ],
        service_lines=[
            _make_service_line(1, "70553", 1500.00),
            _make_service_line(2, "93000", 480.00),
        ],
        schema_valid=True,
        line_items_sum_valid=True,
        calculated_lines_sum=1980.00,
        latency_ms=100,
    )


def _claims_single_code() -> ClaimsAgentOutput:
    """Clean claim with a single service line: CPT 99213 ($150)."""
    return ClaimsAgentOutput(
        agent_name="claims_agent",
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        claim_amount=ProvenanceValue(value="150.00", confidence=0.98),
        service_date=ProvenanceValue(value="2026-09-15", confidence=0.96),
        provider_npi=ProvenanceValue(value="1234567890", confidence=0.95),
        cpt_codes=[ProvenanceValue(value="99213", confidence=0.96)],
        service_lines=[_make_service_line(1, "99213", 150.00)],
        schema_valid=True,
        line_items_sum_valid=True,
        calculated_lines_sum=150.00,
        latency_ms=100,
    )


def _policy_covering(codes: list[str]) -> PolicyCoverageOutput:
    """Policy that returns PASS with the specified codes as applicable."""
    return PolicyCoverageOutput(
        agent_name="policy_agent",
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        covered=True,
        coverage_percentage=80.0,
        applicable_service_codes=codes,
        reasoning_summary="Eligible covered service.",
        latency_ms=10,
    )


def _clinical_pass(lines: list[tuple[int, str, float]]) -> ClinicalAlignmentOutput:
    line_evs = [
        make_clean_line_evidence(
            line_number=ln,
            service_code=cpt,
            billed_units=1.0,
            billed_amount=amt,
        )
        for ln, cpt, amt in lines
    ]
    return build_clinical_alignment_output(
        expected_line_numbers=[ln for ln, _, _ in lines],
        line_assessments=line_evs,
        clinical_documents_present=["DOC-CLIN-01"],
    )


def _base_context(
    case_id: str,
    claims: ClaimsAgentOutput,
    policy: PolicyCoverageOutput,
    fraud: FraudAgentOutput | None = None,
    kyc: KYCAgentOutput | None = None,
    clinical: ClinicalAlignmentOutput | None = None,
) -> DecisionInputContext:
    docs = [DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT]
    if clinical is not None:
        docs.append(DocumentType.DISCHARGE_SUMMARY)
    return DecisionInputContext(
        case_id=case_id,
        execution_id=f"exec-{case_id}",
        present_document_types=docs,
        kyc_output=kyc or _kyc_pass(),
        claims_output=claims,
        policy_output=policy,
        fraud_output=fraud or _fraud_pass(),
        clinical_output=clinical,
    )


# ── Baseline: Fully valid claim CAN approve ──────────────────────────────────

class TestApprovalSafetyGateBaseline:
    """Sanity check: a properly constructed single-code claim with matching
    service lines and full policy coverage still auto-approves."""

    def test_valid_single_line_claim_approves(self) -> None:
        claims = _claims_single_code()
        policy = _policy_covering(["99213"])
        clin = _clinical_pass([(1, "99213", 150.00)])
        ctx = _base_context("BASELINE-SINGLE", claims, policy, clinical=clin)
        output = DeterministicDecisionEngine.adjudicate(ctx)
        assert output.decision == BusinessDecision.APPROVE
        assert output.rule_id_matched == RULE_P6_CLEAN_PASS


# ── Scenario 1: No billed service lines ──────────────────────────────────────

class TestApprovalBlockedNoServiceLines:
    """Rule P6 must NOT approve when the claims output contains CPT codes
    in the header but zero itemized service lines."""

    def test_no_service_lines_blocks_approval(self) -> None:
        claims = _claims_single_code()
        claims.service_lines = []  # Wipe service lines

        policy = _policy_covering(["99213"])
        ctx = _base_context("SAFETY-NO-LINES", claims, policy)
        output = DeterministicDecisionEngine.adjudicate(ctx)

        assert output.decision != BusinessDecision.APPROVE, (
            "APPROVE must not be granted without itemized service lines"
        )
        assert output.decision == BusinessDecision.ESCALATE

    def test_no_service_lines_and_no_cpt_blocks_approval(self) -> None:
        claims = _claims_single_code()
        claims.service_lines = []
        claims.cpt_codes = []

        policy = _policy_covering(["99213"])
        ctx = _base_context("SAFETY-NO-LINES-NO-CPT", claims, policy)
        output = DeterministicDecisionEngine.adjudicate(ctx)

        assert output.decision != BusinessDecision.APPROVE


# ── Scenario 2: PT_20322 — Source CPT dropped from extraction ────────────────

class TestApprovalBlockedPT20322OmittedCPT:
    """Reproduces the PT_20322 regression: the source claim contains two
    procedure lines (CPT 70553 and CPT 93000), but extraction only captured
    CPT 70553.  The policy covers 70553 but not 93000.

    The decision engine must not approve because a source service line is
    missing from the structured output — the subset test must fail."""

    def test_omitted_cpt_93000_blocks_approval(self) -> None:
        """Source has 70553+93000, extraction has only 70553.
        Policy covers 70553.  The claim must NOT auto-approve."""
        claims = ClaimsAgentOutput(
            agent_name="claims_agent",
            verdict=AgentVerdict.PASS,
            confidence=0.88,
            claim_amount=ProvenanceValue(value="1980.00", confidence=0.96),
            service_date=ProvenanceValue(value="2026-09-15", confidence=0.96),
            provider_npi=ProvenanceValue(value="1234567890", confidence=0.95),
            # Only 70553 extracted — 93000 was dropped by OCR
            cpt_codes=[ProvenanceValue(value="70553", confidence=0.96)],
            service_lines=[_make_service_line(1, "70553", 1500.00)],
            schema_valid=True,
            line_items_sum_valid=True,
            calculated_lines_sum=1500.00,
            latency_ms=100,
        )
        # Policy covers 70553 via SEC-3.1
        policy = _policy_covering(["70553"])

        ctx = _base_context("SAFETY-PT20322-OMIT", claims, policy)
        output = DeterministicDecisionEngine.adjudicate(ctx)

        # The claim total is $1980 but only one line sums to $1500.
        # This arithmetic mismatch must block clean approval.
        assert output.decision != BusinessDecision.APPROVE, (
            "APPROVE must not be granted when claim total ($1980) exceeds "
            "the sum of extracted service lines ($1500)"
        )

    def test_both_codes_extracted_and_covered_approves(self) -> None:
        """When both 70553 and 93000 are properly extracted AND covered,
        clean approval should proceed."""
        claims = _claims_two_lines()
        policy = _policy_covering(["70553", "93000"])
        clin = _clinical_pass([(1, "70553", 1500.00), (2, "93000", 480.00)])
        ctx = _base_context("SAFETY-PT20322-FULL", claims, policy, clinical=clin)
        output = DeterministicDecisionEngine.adjudicate(ctx)
        assert output.decision == BusinessDecision.APPROVE

    def test_both_codes_extracted_but_only_one_covered_blocks(self) -> None:
        """Both codes extracted, but policy only covers 70553.
        93000 is uncovered. Must not approve."""
        claims = _claims_two_lines()
        policy = _policy_covering(["70553"])  # 93000 not covered

        ctx = _base_context("SAFETY-PT20322-PARTIAL-COV", claims, policy)
        output = DeterministicDecisionEngine.adjudicate(ctx)

        assert output.decision != BusinessDecision.APPROVE, (
            "APPROVE must not be granted when billed code 93000 lacks "
            "applicable policy coverage"
        )
        assert output.decision == BusinessDecision.ESCALATE


# ── Scenario 3: Service line lacks applicable coverage ───────────────────────

class TestApprovalBlockedIncompleteCoverage:
    """Policy coverage must exist for every billed service code.
    Partial coverage must not be sufficient for auto-approval."""

    def test_partial_cpt_coverage_blocks_approval(self) -> None:
        """Claim has CPT 99213 and 93000.  Policy covers only 99213."""
        claims = _claims_single_code()
        claims.cpt_codes.append(ProvenanceValue(value="93000", confidence=0.96))
        claims.service_lines.append(_make_service_line(2, "93000", 95.00))
        claims.calculated_lines_sum = 245.00
        claims.claim_amount = ProvenanceValue(value="245.00", confidence=0.98)

        policy = _policy_covering(["99213"])  # 93000 not covered

        ctx = _base_context("SAFETY-PARTIAL-COV", claims, policy)
        output = DeterministicDecisionEngine.adjudicate(ctx)

        assert output.decision != BusinessDecision.APPROVE
        assert output.decision == BusinessDecision.ESCALATE

    def test_empty_applicable_codes_blocks_approval(self) -> None:
        """Policy verdict PASS but applicable_service_codes is empty."""
        claims = _claims_single_code()
        policy = _policy_covering([])  # Empty!

        ctx = _base_context("SAFETY-EMPTY-SCOPE", claims, policy)
        output = DeterministicDecisionEngine.adjudicate(ctx)

        assert output.decision != BusinessDecision.APPROVE


# ── Scenario 4: Material unresolved validation conflict ──────────────────────

class TestApprovalBlockedMaterialConflict:
    """Claims with CONFLICT verdict or validation errors that indicate
    unresolved material discrepancies must not auto-approve."""

    def test_claims_conflict_verdict_blocks_approval(self) -> None:
        claims = _claims_single_code()
        claims.verdict = AgentVerdict.CONFLICT
        claims.confidence = 0.85

        policy = _policy_covering(["99213"])
        ctx = _base_context("SAFETY-CONFLICT", claims, policy)
        output = DeterministicDecisionEngine.adjudicate(ctx)

        assert output.decision != BusinessDecision.APPROVE
        assert output.decision == BusinessDecision.ESCALATE
        assert output.rule_id_matched == RULE_P3_AMBIGUITY

    def test_claims_arithmetic_invalid_blocks_approval(self) -> None:
        """line_items_sum_valid=False must prevent clean approval."""
        claims = _claims_single_code()
        claims.line_items_sum_valid = False
        claims.validation_errors = ["ARITHMETIC_DRIFT: sum $100 differs from header $150"]

        policy = _policy_covering(["99213"])
        ctx = _base_context("SAFETY-ARITH-FAIL", claims, policy)
        output = DeterministicDecisionEngine.adjudicate(ctx)

        assert output.decision != BusinessDecision.APPROVE

    def test_claims_schema_invalid_blocks_approval(self) -> None:
        """schema_valid=False must prevent clean approval."""
        claims = _claims_single_code()
        claims.schema_valid = False

        policy = _policy_covering(["99213"])
        ctx = _base_context("SAFETY-SCHEMA-FAIL", claims, policy)
        output = DeterministicDecisionEngine.adjudicate(ctx)

        assert output.decision != BusinessDecision.APPROVE


# ── Scenario 5: Critical validation output missing or unavailable ────────────

class TestApprovalBlockedMissingOutputs:
    """Auto-approval must not occur when any required specialist output is
    absent or returned UNAVAILABLE."""

    def test_missing_claims_output_blocks_approval(self) -> None:
        policy = _policy_covering(["99213"])
        ctx = DecisionInputContext(
            case_id="SAFETY-NO-CLAIMS",
            execution_id="exec-no-claims",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=_kyc_pass(),
            claims_output=None,
            policy_output=policy,
            fraud_output=_fraud_pass(),
        )
        output = DeterministicDecisionEngine.adjudicate(ctx)
        assert output.decision != BusinessDecision.APPROVE

    def test_missing_policy_output_blocks_approval(self) -> None:
        claims = _claims_single_code()
        ctx = DecisionInputContext(
            case_id="SAFETY-NO-POLICY",
            execution_id="exec-no-policy",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=_kyc_pass(),
            claims_output=claims,
            policy_output=None,
            fraud_output=_fraud_pass(),
        )
        output = DeterministicDecisionEngine.adjudicate(ctx)
        assert output.decision != BusinessDecision.APPROVE

    def test_missing_kyc_output_blocks_approval(self) -> None:
        claims = _claims_single_code()
        policy = _policy_covering(["99213"])
        ctx = DecisionInputContext(
            case_id="SAFETY-NO-KYC",
            execution_id="exec-no-kyc",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=None,
            claims_output=claims,
            policy_output=policy,
            fraud_output=_fraud_pass(),
        )
        output = DeterministicDecisionEngine.adjudicate(ctx)
        assert output.decision != BusinessDecision.APPROVE

    def test_missing_fraud_output_blocks_approval(self) -> None:
        claims = _claims_single_code()
        policy = _policy_covering(["99213"])
        ctx = DecisionInputContext(
            case_id="SAFETY-NO-FRAUD",
            execution_id="exec-no-fraud",
            present_document_types=[DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
            kyc_output=_kyc_pass(),
            claims_output=claims,
            policy_output=policy,
            fraud_output=None,
        )
        output = DeterministicDecisionEngine.adjudicate(ctx)
        assert output.decision != BusinessDecision.APPROVE

    def test_unavailable_policy_blocks_approval(self) -> None:
        claims = _claims_single_code()
        policy = PolicyCoverageOutput(
            agent_name="policy_agent",
            verdict=AgentVerdict.UNAVAILABLE,
            confidence=0.0,
            covered=False,
            coverage_percentage=0.0,
            reasoning_summary="Policy repository unreachable.",
            latency_ms=1,
        )
        ctx = _base_context("SAFETY-UNAVAIL-POL", claims, policy)
        output = DeterministicDecisionEngine.adjudicate(ctx)
        assert output.decision != BusinessDecision.APPROVE


# ── Scenario: Service-line total vs claim-amount reconciliation ──────────────

class TestApprovalBlockedLineTotalMismatch:
    """If the sum of extracted service-line charges does not reconcile with
    the declared claim total, the decision engine must not auto-approve,
    because it indicates missing or corrupted line items."""

    def test_lines_sum_below_claim_total_blocks_approval(self) -> None:
        """Claim declares $1980 but only one line at $1500 was extracted.
        This is the PT_20322 arithmetic scenario."""
        claims = ClaimsAgentOutput(
            agent_name="claims_agent",
            verdict=AgentVerdict.PASS,
            confidence=0.88,
            claim_amount=ProvenanceValue(value="1980.00", confidence=0.96),
            service_date=ProvenanceValue(value="2026-09-15", confidence=0.96),
            provider_npi=ProvenanceValue(value="1234567890", confidence=0.95),
            cpt_codes=[ProvenanceValue(value="70553", confidence=0.96)],
            service_lines=[_make_service_line(1, "70553", 1500.00)],
            schema_valid=True,
            # Even if the Claims Agent declared this valid:
            line_items_sum_valid=True,
            calculated_lines_sum=1500.00,
            latency_ms=100,
        )
        policy = _policy_covering(["70553"])
        ctx = _base_context("SAFETY-LINE-SUM-MISMATCH", claims, policy)
        output = DeterministicDecisionEngine.adjudicate(ctx)

        assert output.decision != BusinessDecision.APPROVE, (
            "APPROVE must not be granted when extracted line sum ($1500) "
            "does not reconcile with claim total ($1980)"
        )

