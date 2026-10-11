import pytest
from datetime import date
from medishield.db.enums import AgentVerdict, BusinessDecision, EscalationSubReason
from medishield.domain.contracts import (
    KYCAgentOutput, ClaimsAgentOutput, PolicyCoverageOutput, FraudAgentOutput, OrchestratorOutput
)
from medishield.domain.decision import adjudicate_case, DecisionInputContext

from medishield.domain.contracts import ProvenanceValue

def _make_claims_output(verdict: AgentVerdict, confidence: float) -> ClaimsAgentOutput:
    return ClaimsAgentOutput(
        agent_name="claims",
        verdict=verdict,
        confidence=confidence,
        schema_valid=True,
        line_items_sum_valid=True,
        calculated_lines_sum=100.0,
        claim_amount=ProvenanceValue(value="100.0", is_grounded=True, confidence=1.0),
        service_date=ProvenanceValue(value="2026-01-01", is_grounded=True, confidence=1.0),
        provider_npi=ProvenanceValue(value="1234567890", is_grounded=True, confidence=1.0),
        latency_ms=10
    )

def _make_policy_output(verdict: AgentVerdict, confidence: float, exclusion_clause_verified: bool = False, applicable_service_codes: list[str] = None) -> PolicyCoverageOutput:
    return PolicyCoverageOutput(
        agent_name="policy",
        verdict=verdict,
        confidence=confidence,
        covered=(verdict == AgentVerdict.PASS),
        coverage_percentage=80.0,
        reasoning_summary="Test",
        exclusion_clause_verified=exclusion_clause_verified,
        applicable_service_codes=applicable_service_codes or [],
        latency_ms=10
    )

def _build_context(policy_verdict: AgentVerdict, verified_exclusion: bool = False, applicable: bool = False) -> DecisionInputContext:
    return DecisionInputContext(
        case_id="TEST-CASE",
        execution_id="TEST-EXEC",
        present_document_types=[],
        kyc_output=KYCAgentOutput(agent_name="kyc", verdict=AgentVerdict.PASS, confidence=0.9, latency_ms=10),
        claims_output=_make_claims_output(AgentVerdict.PASS, confidence=0.9),
        policy_output=_make_policy_output(
            policy_verdict,
            confidence=0.8,
            exclusion_clause_verified=verified_exclusion,
            applicable_service_codes=["12345"] if applicable else []
        ),
        fraud_output=None,
        security_tamper_score=0.0
    )


def test_p5_denial_requires_explicit_verified_exclusion():
    # Empty corpus or unverified CPT leads to AgentVerdict.INDETERMINATE usually, but if the agent emitted FAIL
    # without a verified exclusion, the decision engine MUST NOT reject it.
    ctx = _build_context(policy_verdict=AgentVerdict.FAIL, verified_exclusion=False, applicable=False)
    
    out = adjudicate_case(ctx)
    
    assert out.decision == BusinessDecision.ESCALATE
    assert out.rule_id_matched == "RULE-P3-AMBIGUITY"
    assert out.escalation_sub_reason == EscalationSubReason.AMBIGUITY
    assert "lacks a verified exclusion citation" in out.justification


def test_p5_recommends_denial_if_verified_exclusion():
    ctx = _build_context(policy_verdict=AgentVerdict.FAIL, verified_exclusion=True, applicable=True)
    
    out = adjudicate_case(ctx)
    
    assert out.decision == BusinessDecision.ESCALATE
    assert out.rule_id_matched == "RULE-P5-HARD-REJECT"
    assert out.escalation_sub_reason == EscalationSubReason.RECOMMEND_DENIAL
    assert out.decision_confidence < 1.0  # Must use min_confidence, not hardcoded 1.0
