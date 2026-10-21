"""Clinical-only INDETERMINATE alignment with majority line support must not escalate (ADR-027).

A clinical INDETERMINATE verdict escalates via P3 by default. When the fully supported billed-line
ratio exceeds CLINICAL_SUPPORT_APPROVAL_MIN_RATIO and no line is contradicted, failed, or
unavailable, and the other specialists pass, the case approves via P6.
"""

import pytest

from medishield.core.config import Settings
from medishield.db.enums import AgentVerdict, BusinessDecision, IndeterminateReason
from medishield.domain.contracts import (
    BedDayReconciliation,
    ClaimsAgentOutput,
    ClinicalAlignmentOutput,
    ClinicalIndicationStatus,
    LineClinicalEvidence,
    MedicalNecessityStatus,
    PerformanceEvidenceStatus,
    ProvenanceValue,
    build_clinical_alignment_output,
)
from medishield.domain.decision import (
    RULE_P4_HIGH_FRAUD,
    RULE_P3_AMBIGUITY,
    RULE_P6_CLEAN_PASS,
    DecisionInputContext,
    DeterministicDecisionEngine,
    is_clinical_support_exempt,
)
from tests.unit.test_approval_safety_gate import (
    _base_context,
    _fraud_pass,
    _make_service_line,
    _policy_covering,
)
from tests.unit.test_clinical_alignment import make_clean_line_evidence

ClinicalLine = tuple[int, str, float, AgentVerdict]


def _claims(lines: list[tuple[int, str, float]]) -> ClaimsAgentOutput:
    total = sum(amount for _, _, amount in lines)
    return ClaimsAgentOutput(
        agent_name="claims_agent",
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        claim_amount=ProvenanceValue(value=f"{total:.2f}", confidence=0.98),
        service_date=ProvenanceValue(value="2026-09-15", confidence=0.96),
        provider_npi=ProvenanceValue(value="1234567890", confidence=0.95),
        cpt_codes=[ProvenanceValue(value=cpt, confidence=0.96) for _, cpt, _ in lines],
        service_lines=[_make_service_line(ln, cpt, amount) for ln, cpt, amount in lines],
        schema_valid=True,
        line_items_sum_valid=True,
        calculated_lines_sum=total,
        latency_ms=100,
    )


def _clinical(lines: list[ClinicalLine]) -> ClinicalAlignmentOutput:
    assessments = []
    for ln, cpt, amount, verdict in lines:
        evidence = make_clean_line_evidence(
            line_number=ln,
            service_code=cpt,
            billed_units=1.0,
            billed_amount=amount,
        )
        if verdict != AgentVerdict.PASS:
            updates: dict[str, object] = {"line_verdict": verdict}
            if verdict == AgentVerdict.INDETERMINATE:
                updates.update(
                    {
                        "performance_status": PerformanceEvidenceStatus.NOT_DOCUMENTED,
                        "indication_status": ClinicalIndicationStatus.POTENTIALLY_RELATED,
                        "necessity_status": MedicalNecessityStatus.GUIDELINE_UNAVAILABLE,
                        "guideline_citation": None,
                        "supporting_spans": [],
                    }
                )
            evidence = evidence.model_copy(update=updates)
        assessments.append(evidence)
    return build_clinical_alignment_output(
        expected_line_numbers=[ln for ln, _, _, _ in lines],
        line_assessments=assessments,
        clinical_documents_present=["DOC-CLIN-01"],
    )


def _decide(
    claims_lines: list[tuple[int, str, float]],
    clinical: ClinicalAlignmentOutput | None,
    threshold: float | None = 0.5,
    fraud_score: float = 0.0,
    fraud_review_required: bool = False,
    uncertainty_enabled: bool = False,
) -> tuple[BusinessDecision, str | None, str]:
    codes = [cpt for _, cpt, _ in claims_lines]
    context: DecisionInputContext = _base_context(
        case_id="CASE-CLIN-SUPPORT",
        claims=_claims(claims_lines),
        policy=_policy_covering(codes),
        fraud=_fraud_pass(fraud_score, fraud_review_required),
        clinical=clinical,
    ).model_copy(
        update={
            "clinical_support_approval_min_ratio": threshold,
            "clinical_uncertainty_approval_enabled": uncertainty_enabled,
        }
    )
    output = DeterministicDecisionEngine.adjudicate(context)
    return output.decision, output.rule_id_matched, output.justification


def _three_lines_two_supported() -> list[ClinicalLine]:
    return [
        (1, "70553", 1500.00, AgentVerdict.PASS),
        (2, "93000", 480.00, AgentVerdict.PASS),
        (3, "99213", 150.00, AgentVerdict.INDETERMINATE),
    ]


class TestClinicalSupportExemptionDecision:
    def test_clinical_uncertainty_approves_with_discrepancy_notice_when_enabled(self) -> None:
        claims_lines = [(1, "99213", 150.00), (2, "99214", 200.00)]
        clinical = _clinical(
            [
                (1, "99213", 150.00, AgentVerdict.INDETERMINATE),
                (2, "99214", 200.00, AgentVerdict.INDETERMINATE),
            ]
        )

        decision, rule, justification = _decide(
            claims_lines, clinical, threshold=None, uncertainty_enabled=True
        )

        assert decision == BusinessDecision.APPROVE
        assert rule == RULE_P6_CLEAN_PASS
        assert "Clinical discrepancy notice" in justification
        assert "0/2 billed lines fully supported" in justification

    def test_bed_day_conflict_blocks_clinical_uncertainty_approval(self) -> None:
        claims_lines = [(1, "99213", 150.00), (2, "99214", 200.00)]
        clinical = _clinical(
            [
                (1, "99213", 150.00, AgentVerdict.INDETERMINATE),
                (2, "99214", 200.00, AgentVerdict.INDETERMINATE),
            ]
        ).model_copy(
            update={
                "bed_day_reconciliation": BedDayReconciliation(
                    billed_bed_days=1.0,
                    reconciliation_verdict=AgentVerdict.CONFLICT,
                    uncertainty_flag=True,
                    remarks="Billed bed days conflict with stay evidence.",
                )
            }
        )

        decision, rule, _ = _decide(
            claims_lines, clinical, threshold=None, uncertainty_enabled=True
        )

        assert decision == BusinessDecision.ESCALATE
        assert rule == RULE_P3_AMBIGUITY

    def test_unclassified_clinical_reason_does_not_use_discrepancy_approval(self) -> None:
        evidence = make_clean_line_evidence(
            line_number=1,
            service_code="99213",
            billed_units=1.0,
            billed_amount=150.0,
        ).model_copy(update={"line_verdict": AgentVerdict.INDETERMINATE})
        clinical = build_clinical_alignment_output(
            expected_line_numbers=[1],
            line_assessments=[evidence],
            clinical_documents_present=["DOC-CLIN-01"],
        )

        decision, rule, _ = _decide(
            [(1, "99213", 150.0)],
            clinical,
            threshold=None,
            uncertainty_enabled=True,
        )

        assert decision == BusinessDecision.ESCALATE
        assert rule == RULE_P3_AMBIGUITY

    def test_failed_clinical_line_still_escalates_when_uncertainty_approval_enabled(self) -> None:
        claims_lines = [(1, "99213", 150.0)]
        clinical = _clinical([(1, "99213", 150.0, AgentVerdict.FAIL)])

        decision, rule, _ = _decide(
            claims_lines, clinical, threshold=None, uncertainty_enabled=True
        )

        assert decision == BusinessDecision.ESCALATE
        assert rule == RULE_P3_AMBIGUITY

    def test_majority_supported_clinical_indeterminate_approves_via_p6(self) -> None:
        claims_lines = [(1, "70553", 1500.00), (2, "93000", 480.00), (3, "99213", 150.00)]

        decision, rule, justification = _decide(
            claims_lines, _clinical(_three_lines_two_supported())
        )

        assert decision == BusinessDecision.APPROVE
        assert rule == RULE_P6_CLEAN_PASS
        assert "Clinical alignment supported 2/3 billed lines" in justification

    def test_exactly_half_supported_still_escalates(self) -> None:
        claims_lines = [
            (1, "70553", 1500.00),
            (2, "93000", 480.00),
            (3, "99213", 150.00),
            (4, "99214", 200.00),
        ]
        clinical = _clinical(
            [
                (1, "70553", 1500.00, AgentVerdict.PASS),
                (2, "93000", 480.00, AgentVerdict.PASS),
                (3, "99213", 150.00, AgentVerdict.INDETERMINATE),
                (4, "99214", 200.00, AgentVerdict.INDETERMINATE),
            ]
        )

        decision, rule, _ = _decide(claims_lines, clinical)

        assert decision == BusinessDecision.ESCALATE
        assert rule == RULE_P3_AMBIGUITY

    def test_minority_supported_still_escalates(self) -> None:
        claims_lines = [(1, "70553", 1500.00), (2, "93000", 480.00), (3, "99213", 150.00)]
        clinical = _clinical(
            [
                (1, "70553", 1500.00, AgentVerdict.PASS),
                (2, "93000", 480.00, AgentVerdict.INDETERMINATE),
                (3, "99213", 150.00, AgentVerdict.INDETERMINATE),
            ]
        )

        decision, rule, _ = _decide(claims_lines, clinical)

        assert decision == BusinessDecision.ESCALATE
        assert rule == RULE_P3_AMBIGUITY

    def test_disabled_threshold_keeps_escalation(self) -> None:
        claims_lines = [(1, "70553", 1500.00), (2, "93000", 480.00), (3, "99213", 150.00)]

        decision, rule, _ = _decide(
            claims_lines, _clinical(_three_lines_two_supported()), threshold=None
        )

        assert decision == BusinessDecision.ESCALATE
        assert rule == RULE_P3_AMBIGUITY

    def test_custom_threshold_is_respected(self) -> None:
        claims_lines = [(1, "70553", 1500.00), (2, "93000", 480.00), (3, "99213", 150.00)]

        decision, rule, _ = _decide(
            claims_lines, _clinical(_three_lines_two_supported()), threshold=0.7
        )

        assert decision == BusinessDecision.ESCALATE
        assert rule == RULE_P3_AMBIGUITY

    def test_fraud_review_requirement_is_not_cleared(self) -> None:
        claims_lines = [(1, "70553", 1500.00), (2, "93000", 480.00), (3, "99213", 150.00)]

        decision, rule, _ = _decide(
            claims_lines,
            _clinical(_three_lines_two_supported()),
            fraud_review_required=True,
        )

        assert decision == BusinessDecision.ESCALATE
        assert rule == RULE_P4_HIGH_FRAUD


class TestIsClinicalSupportExempt:
    def _context(
        self,
        clinical: ClinicalAlignmentOutput | None,
        threshold: float | None = 0.5,
    ) -> DecisionInputContext:
        base = _base_context(
            case_id="CASE-PREDICATE",
            claims=_claims([(1, "70553", 1500.00), (2, "93000", 480.00), (3, "99213", 150.00)]),
            policy=_policy_covering(["70553", "93000", "99213"]),
            clinical=_clinical(_three_lines_two_supported()),
        )
        return base.model_copy(
            update={"clinical_output": clinical, "clinical_support_approval_min_ratio": threshold}
        )

    def test_majority_supported_is_exempt(self) -> None:
        clinical = _clinical(_three_lines_two_supported())

        assert is_clinical_support_exempt(self._context(clinical)) is True

    def test_missing_clinical_output_is_not_exempt(self) -> None:
        assert is_clinical_support_exempt(self._context(None)) is False

    def test_disabled_threshold_is_not_exempt(self) -> None:
        clinical = _clinical(_three_lines_two_supported())

        assert is_clinical_support_exempt(self._context(clinical, threshold=None)) is False

    @pytest.mark.parametrize(
        "update",
        [
            {"lines_contradicted_count": 1},
            {"lines_failed_necessity_count": 1},
            {"lines_unavailable_count": 1},
            {"schema_valid": False},
            {"ocr_quality_sufficient": False},
            {"total_lines_assessed": 0, "lines_fully_supported_count": 0},
        ],
    )
    def test_guard_conditions_block_exemption(self, update: dict[str, object]) -> None:
        clinical = _clinical(_three_lines_two_supported()).model_copy(update=update)

        assert is_clinical_support_exempt(self._context(clinical)) is False

    def test_non_indeterminate_verdict_is_not_exempt(self) -> None:
        clinical = _clinical(_three_lines_two_supported()).model_copy(
            update={"verdict": AgentVerdict.FAIL}
        )

        assert is_clinical_support_exempt(self._context(clinical)) is False


class TestClinicalSupportThresholdConfig:
    def test_default_ratio_is_half(self) -> None:
        settings = Settings(_env_file=None)

        assert settings.CLINICAL_SUPPORT_APPROVAL_MIN_RATIO == 0.5
        assert settings.CLINICAL_UNCERTAINTY_APPROVAL_ENABLED is True

    def test_ratio_outside_unit_interval_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            Settings(_env_file=None, CLINICAL_SUPPORT_APPROVAL_MIN_RATIO=1.5)


def _determined_line(ln: int, cpt: str, amount: float, outcome: str) -> LineClinicalEvidence:
    return make_clean_line_evidence(
        line_number=ln, service_code=cpt, billed_units=1.0, billed_amount=amount
    ).model_copy(
        update={
            "line_verdict": AgentVerdict.INDETERMINATE,
            "necessity_status": MedicalNecessityStatus.CRITERIA_INCOMPLETE,
            "determination_outcome": outcome,
        }
    )


class TestDeterminationOutcomeMapping:
    def test_uncertain_determination_maps_to_approvable_reason(self) -> None:
        line = _determined_line(1, "99213", 150.00, "UNCERTAIN")

        assert line.reason_codes == [IndeterminateReason.CLINICAL_DETERMINATION_UNCERTAIN]

    def test_not_established_determination_keeps_criteria_unsatisfied_reason(self) -> None:
        line = _determined_line(1, "99213", 150.00, "NOT_ESTABLISHED")

        assert line.reason_codes == [IndeterminateReason.GUIDELINE_CRITERIA_UNSATISFIED]

    def test_uncertain_lines_approve_with_discrepancy_notice(self) -> None:
        claims_lines = [(1, "99213", 150.00), (2, "99214", 200.00)]
        clinical = build_clinical_alignment_output(
            expected_line_numbers=[1, 2],
            line_assessments=[
                _determined_line(1, "99213", 150.00, "UNCERTAIN"),
                _determined_line(2, "99214", 200.00, "UNCERTAIN"),
            ],
            clinical_documents_present=["DOC-CLIN-01"],
        )

        decision, rule, justification = _decide(
            claims_lines, clinical, threshold=None, uncertainty_enabled=True
        )

        assert decision == BusinessDecision.APPROVE
        assert rule == RULE_P6_CLEAN_PASS
        assert "Clinical discrepancy notice" in justification

    def test_not_established_line_escalates_even_with_uncertainty_approval(self) -> None:
        claims_lines = [(1, "99213", 150.00), (2, "99214", 200.00)]
        clinical = build_clinical_alignment_output(
            expected_line_numbers=[1, 2],
            line_assessments=[
                _determined_line(1, "99213", 150.00, "UNCERTAIN"),
                _determined_line(2, "99214", 200.00, "NOT_ESTABLISHED"),
            ],
            clinical_documents_present=["DOC-CLIN-01"],
        )

        decision, _, _ = _decide(claims_lines, clinical, threshold=None, uncertainty_enabled=True)

        assert decision == BusinessDecision.ESCALATE
