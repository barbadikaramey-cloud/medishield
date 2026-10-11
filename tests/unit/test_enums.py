from medishield.db.enums import (
    ActorType,
    AgentVerdict,
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
    WorkflowState,
)


def test_workflow_state_values():
    expected = {
        "RECEIVED", "SCANNING", "SPLIT", "CLASSIFYING",
        "ANALYZING", "AWAITING_INFO", "AWAITING_HUMAN",
        "COMPLETED", "FAILED"
    }
    actual = {state.value for state in WorkflowState}
    assert actual == expected
    assert WorkflowState.RECEIVED == "RECEIVED"


def test_business_decision_values():
    expected = {"APPROVE", "REJECT", "ESCALATE"}
    actual = {decision.value for decision in BusinessDecision}
    assert actual == expected


def test_escalation_sub_reason_values():
    expected = {
        "MISSING_INFORMATION", "HUMAN_REVIEW", "FRAUD_REVIEW",
        "SECURITY_REVIEW", "TECHNICAL_FAILURE", "AMBIGUITY", "RECOMMEND_DENIAL"
    }
    actual = {reason.value for reason in EscalationSubReason}
    assert actual == expected


def test_agent_verdict_values():
    expected = {"PASS", "FAIL", "INDETERMINATE", "CONFLICT", "UNAVAILABLE"}
    actual = {verdict.value for verdict in AgentVerdict}
    assert actual == expected


def test_document_type_values():
    expected = {
        "CLAIM_FORM", "ID_DOCUMENT", "DISCHARGE_SUMMARY",
        "PRESCRIPTION", "POLICY_AMENDMENT", "UNKNOWN"
    }
    actual = {doc_type.value for doc_type in DocumentType}
    assert actual == expected


def test_actor_type_values():
    expected = {"SYSTEM", "HUMAN"}
    actual = {actor.value for actor in ActorType}
    assert actual == expected
