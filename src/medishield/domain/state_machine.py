"""Operational workflow state machine and lifecycle transition validator (Spec 02, Spec 04, ADR-001)."""

from medishield.db.enums import BusinessDecision, EscalationSubReason, WorkflowState


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal workflow state transition is attempted."""

    def __init__(self, from_state: WorkflowState, to_state: WorkflowState) -> None:
        super().__init__(
            f"Illegal workflow state transition: cannot transition from '{from_state.value}' "
            f"to '{to_state.value}'"
        )
        self.from_state = from_state
        self.to_state = to_state


# ── Allowed State Transition Graph (Spec 02 § 1 & Spec 04 § 2) ────────────────
ALLOWED_TRANSITIONS: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.RECEIVED: {
        WorkflowState.SCANNING,
        WorkflowState.FAILED,
    },
    WorkflowState.SCANNING: {
        WorkflowState.SPLIT,
        WorkflowState.FAILED,
    },
    WorkflowState.SPLIT: {
        WorkflowState.CLASSIFYING,
        WorkflowState.FAILED,
    },
    WorkflowState.CLASSIFYING: {
        WorkflowState.ANALYZING,
        WorkflowState.AWAITING_INFO,
        WorkflowState.FAILED,
    },
    WorkflowState.ANALYZING: {
        WorkflowState.AWAITING_INFO,
        WorkflowState.AWAITING_HUMAN,
        WorkflowState.COMPLETED,
        WorkflowState.FAILED,
    },
    WorkflowState.AWAITING_INFO: {
        WorkflowState.SCANNING,    # Additional documents uploaded; resume processing
        WorkflowState.COMPLETED,   # User cancelled or administratively closed
        WorkflowState.FAILED,
    },
    WorkflowState.AWAITING_HUMAN: {
        WorkflowState.COMPLETED,      # Human reviewer approved, rejected, or confirmed
        WorkflowState.AWAITING_INFO,  # Human reviewer requested additional documentation
        WorkflowState.FAILED,
    },
    # Terminal states have no outbound transitions
    WorkflowState.COMPLETED: set(),
    WorkflowState.FAILED: set(),
}


def can_transition(from_state: WorkflowState, to_state: WorkflowState) -> bool:
    """Evaluates whether transitioning from from_state to to_state is valid."""
    allowed = ALLOWED_TRANSITIONS.get(from_state, set())
    return to_state in allowed


def validate_transition(from_state: WorkflowState, to_state: WorkflowState) -> None:
    """Validates transition, raising InvalidStateTransitionError if illegal."""
    if not can_transition(from_state, to_state):
        raise InvalidStateTransitionError(from_state, to_state)


def resolve_decision_workflow_state(
    decision: BusinessDecision,
    sub_reason: EscalationSubReason | None = None,
) -> WorkflowState:
    """
    Maps a final BusinessDecision and optional EscalationSubReason
    to its resulting operational WorkflowState (Spec 02 § 1 & ADR-001).
    """
    if decision in (BusinessDecision.APPROVE, BusinessDecision.REJECT):
        return WorkflowState.COMPLETED

    if decision == BusinessDecision.ESCALATE:
        if sub_reason == EscalationSubReason.MISSING_INFORMATION:
            return WorkflowState.AWAITING_INFO
        return WorkflowState.AWAITING_HUMAN

    return WorkflowState.AWAITING_HUMAN


class WorkflowStateMachine:
    """Operational lifecycle manager for case processing states."""

    @classmethod
    def transition(
        cls,
        current_state: WorkflowState,
        target_state: WorkflowState,
    ) -> WorkflowState:
        """Validates and executes state transition."""
        validate_transition(current_state, target_state)
        return target_state

    @classmethod
    def transition_from_decision(
        cls,
        current_state: WorkflowState,
        decision: BusinessDecision,
        sub_reason: EscalationSubReason | None = None,
    ) -> WorkflowState:
        """Resolves target state from business decision and verifies transition validity."""
        target_state = resolve_decision_workflow_state(decision, sub_reason)
        validate_transition(current_state, target_state)
        return target_state

