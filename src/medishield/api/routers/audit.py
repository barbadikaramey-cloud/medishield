"""Cryptographic Audit Export & Operational Telemetry Router (Spec 08 § 5, FEAT-012)."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from medishield.api.deps import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_REVIEWER,
    AuthUser,
    get_db,
    require_role,
)
from medishield.api.schemas.audit import (
    AuditEventItem,
    AuditExportResponse,
    BenchmarkEvaluationResponse,
    CalibrationBinSchema,
    CalibrationMetricsResponse,
    LLMRuntimeTelemetryResponse,
    OperationalMetricsResponse,
    SelectiveCoverageSchema,
)
from medishield.benchmarks.evaluator import run_benchmark_evaluation
from medishield.core.llm import llm_runtime_telemetry
from medishield.core.tracing import trace_span
from medishield.db.audit import GENESIS_HASH, verify_audit_chain
from medishield.db.enums import ActorType, BusinessDecision, WorkflowState
from medishield.db.models import AgentRun, AuditEvent, Case, Decision

router = APIRouter(tags=["audit-and-metrics"])


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile over an ascending list; None when empty."""
    if not sorted_values:
        return None
    rank = max(1, -(-len(sorted_values) * int(pct) // 100))
    return round(sorted_values[min(rank, len(sorted_values)) - 1], 1)


@router.get(
    "/audit/cases/{case_id}",
    response_model=AuditExportResponse,
    summary="Export and Cryptographically Verify Case Audit Trail",
)
def export_and_verify_audit_trail(
    case_id: str,
    current_user: AuthUser = Depends(require_role(ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> AuditExportResponse:
    """
    Exports immutable audit ledger and verifies HMAC-SHA256 hash chaining on the fly (Spec 08 § 5.2).
    Traverses from genesis hash (0x0...0) to terminal hash, asserting bit-level tamper resistance.
    """
    case = db.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    with trace_span(
        name="db_audit_trail_retrieval",
        run_type="retriever",
        inputs={},
        component="audit_repository",
        operation="get_audit_trail",
    ) as span:
        events = (
            db.query(AuditEvent)
            .filter(AuditEvent.case_id == case.id)
            .order_by(AuditEvent.created_at.asc())
            .all()
        )
        chain_intact = verify_audit_chain(events)
        if span:
            span.set_summary(
                status="SUCCESS",
                retrieved_chunk_count=len(events),
                is_complete=chain_intact,
            )
    terminal_hash = events[-1].current_hash if events else GENESIS_HASH

    items = [
        AuditEventItem(
            id=str(e.id),
            event_type=e.event_type,
            actor_type=e.actor_type.value if hasattr(e.actor_type, "value") else str(e.actor_type),
            actor_id=e.actor_id,
            event_payload=e.event_payload,
            prev_hash=e.prev_hash,
            current_hash=e.current_hash,
            created_at=e.created_at,
        )
        for e in events
    ]

    return AuditExportResponse(
        case_id=case_id,
        chain_intact=chain_intact,
        total_events=len(items),
        genesis_hash=GENESIS_HASH,
        terminal_hash=terminal_hash,
        events=items,
    )


@router.get(
    "/metrics/operational",
    response_model=OperationalMetricsResponse,
    summary="Operational Telemetry & STP Metrics",
)
def get_operational_metrics(
    current_user: AuthUser = Depends(require_role(ROLE_MANAGER, ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> OperationalMetricsResponse:
    """
    Operational queue monitoring and Straight-Through Processing (STP) tracking (Spec 08 § 5.1).
    Restricted to OPERATIONS_MANAGER and ADMINISTRATOR.
    """
    now = datetime.now(UTC)
    total_cases = db.query(Case).count()
    awaiting_human = db.query(Case).filter(Case.workflow_state == WorkflowState.AWAITING_HUMAN).count()
    awaiting_info = db.query(Case).filter(Case.workflow_state == WorkflowState.AWAITING_INFO).count()
    completed = db.query(Case).filter(Case.workflow_state == WorkflowState.COMPLETED).count()
    intake_depth = db.query(Case).filter(Case.workflow_state == WorkflowState.RECEIVED).count()
    completed_24h = (
        db.query(Case)
        .filter(Case.workflow_state == WorkflowState.COMPLETED, Case.updated_at >= now - timedelta(hours=24))
        .count()
    )

    adjudicated = db.query(Case).filter(Case.current_decision.isnot(None)).count()
    approved_count = db.query(Case).filter(Case.current_decision == BusinessDecision.APPROVE).count()
    escalated_count = db.query(Case).filter(Case.current_decision == BusinessDecision.ESCALATE).count()
    rejected_count = db.query(Case).filter(Case.current_decision == BusinessDecision.REJECT).count()

    # Straight-through = APPROVE/REJECT whose active decision was issued by the system, not a human.
    stp_count = (
        db.query(Decision.case_id)
        .filter(
            Decision.is_active.is_(True),
            Decision.actor_type == ActorType.SYSTEM,
            Decision.decision.in_([BusinessDecision.APPROVE, BusinessDecision.REJECT]),
        )
        .distinct()
        .count()
    )

    # Execution span avoids overstating duration when specialist agents run concurrently.
    latest_exec = (
        db.query(
            AgentRun.case_id,
            AgentRun.execution_id,
            func.row_number()
            .over(partition_by=AgentRun.case_id, order_by=AgentRun.started_at.desc())
            .label("run_rank"),
        )
        .subquery()
    )
    latest_execution = (
        db.query(latest_exec.c.case_id, latest_exec.c.execution_id)
        .filter(latest_exec.c.run_rank == 1)
        .subquery()
    )
    execution_rows = (
        db.query(
            AgentRun.case_id,
            func.min(AgentRun.started_at).label("started_at"),
            func.max(AgentRun.completed_at).label("completed_at"),
        )
        .join(
            latest_execution,
            (AgentRun.case_id == latest_execution.c.case_id)
            & (AgentRun.execution_id == latest_execution.c.execution_id),
        )
        .group_by(AgentRun.case_id)
        .all()
    )
    latencies = sorted(
        max(0.0, (completed_at - started_at).total_seconds() * 1000)
        for _case_pk, started_at, completed_at in execution_rows
        if started_at is not None and completed_at is not None
    )

    def _rate(count: int) -> float | None:
        return round(count / adjudicated, 3) if adjudicated > 0 else None

    first_case = db.query(func.min(Case.created_at)).scalar()
    return OperationalMetricsResponse(
        timestamp=now,
        active_cases=total_cases - completed,
        cases_awaiting_human=awaiting_human,
        cases_awaiting_info=awaiting_info,
        completed_cases_24h=completed_24h,
        straight_through_processing_rate=_rate(stp_count),
        average_adjudication_latency_ms=round(sum(latencies) / len(latencies), 1) if latencies else None,
        latency_p50_ms=_percentile(latencies, 50),
        latency_p95_ms=_percentile(latencies, 95),
        intake_queue_depth=intake_depth,
        workflow_queue_depth=awaiting_human + awaiting_info,
        auto_approval_rate=_rate(approved_count),
        escalation_rate=_rate(escalated_count),
        rejection_rate=_rate(rejected_count),
        total_adjudicated_cases=adjudicated,
        insufficient_data=adjudicated == 0,
        llm_runtime=LLMRuntimeTelemetryResponse(**llm_runtime_telemetry.snapshot()),
        period_start=first_case.isoformat() if first_case else None,
        period_end=now.isoformat(),
    )


@router.get(
    "/metrics/benchmark",
    response_model=BenchmarkEvaluationResponse,
    summary="30-Cluster Real Dataset Benchmark & Accuracy Metrics",
)
def get_benchmark_metrics(
    current_user: AuthUser = Depends(require_role(ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),
) -> BenchmarkEvaluationResponse:
    """
    Returns automated evaluation metrics executed on the authoritative 30-cluster / 155-document dataset.
    Provides document classification accuracy, member resolution, geometric OCR extraction, and fraud tests.
    """
    res = run_benchmark_evaluation(force=False)
    return BenchmarkEvaluationResponse(**res)


@router.post(
    "/metrics/benchmark/run",
    response_model=BenchmarkEvaluationResponse,
    summary="Trigger Authoritative Dataset Benchmark Evaluation Run",
)
def trigger_benchmark_evaluation(
    current_user: AuthUser = Depends(require_role(ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),
) -> BenchmarkEvaluationResponse:
    """
    Re-executes the benchmark evaluation suite across all 155 real documents in dataset/.
    """
    res = run_benchmark_evaluation(force=True)
    return BenchmarkEvaluationResponse(**res)


@router.get(
    "/metrics/calibration",
    response_model=CalibrationMetricsResponse,
    summary="Orchestrator Confidence Calibration & Reliability Metrics (ADR-017)",
)
def get_calibration_metrics(
    strategy: str = "equal_width",
    bins_count: int = 10,
    current_user: AuthUser = Depends(require_role(ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> CalibrationMetricsResponse:
    """
    Computes a simulation-only calibration diagnostic from generated synthetic cases.
    These metrics do not represent live Orchestrator accuracy or metadata fraud-label performance.
    """
    from medishield.eval.calibration import (
        PlattCalibrator,
        compute_brier_score,
        compute_expected_calibration_error,
        generate_reliability_diagram_data,
    )
    from medishield.eval.generator import SyntheticDatasetGenerator
    from medishield.eval.harness import BenchmarkHarness

    # Generate benchmark scenarios for a simulation-only calibration diagnostic.
    gen = SyntheticDatasetGenerator(base_seed=42)
    all_cases = gen.generate_tier_2_dataset()

    # 2. Split 50/50 into calibration fit set and held-out test evaluation set (Prevent data leakage)
    split_idx = len(all_cases) // 2
    cal_cases = all_cases[:split_idx]
    test_cases = all_cases[split_idx:]

    harness = BenchmarkHarness()
    _, cal_preds = harness.run_benchmark(cal_cases)
    _, test_preds = harness.run_benchmark(test_cases)

    # 3. Fit Platt Calibrator strictly on calibration split
    y_cal_true = [1 if p.actual_decision == c.expected_business_decision else 0 for p, c in zip(cal_preds, cal_cases, strict=False)]
    y_cal_conf = [p.decision_confidence for p in cal_preds]

    calibrator = PlattCalibrator()
    calibrator.fit(y_cal_conf, y_cal_true)

    # 4. Evaluate raw vs calibrated on held-out test split
    y_test_true = [1 if p.actual_decision == c.expected_business_decision else 0 for p, c in zip(test_preds, test_cases, strict=False)]
    y_test_raw_conf = [p.decision_confidence for p in test_preds]
    y_test_cal_conf = calibrator.predict_proba(y_test_raw_conf).tolist()

    valid_strategy = "equal_frequency" if strategy == "equal_frequency" else "equal_width"
    n_bins = max(3, min(20, bins_count))

    raw_data = generate_reliability_diagram_data(
        y_true=y_test_true,
        y_prob=y_test_raw_conf,
        calibrated_prob=y_test_cal_conf,
        n_bins=n_bins,
        strategy=valid_strategy,
    )

    cal_brier = compute_brier_score(y_test_true, y_test_cal_conf)
    cal_ece, _, cal_bins = compute_expected_calibration_error(
        y_true=y_test_true,
        y_prob=y_test_cal_conf,
        n_bins=n_bins,
        strategy=valid_strategy,
    )

    interpretation = (
        "Points below diagonal indicate model overconfidence (predicted confidence > empirical accuracy); "
        "points above indicate underconfidence. Platt scaling applies parametric logistic correction to align scores."
    )

    return CalibrationMetricsResponse(
        strategy=valid_strategy,
        total_samples=raw_data.total_samples,
        evaluation_source="generated_synthetic_simulation",
        evaluation_label="Prediction correctness against generated expected_business_decision labels",
        evaluation_population=(
            f"Generated synthetic cases: {len(cal_cases)} fit cases and "
            f"{len(test_cases)} disjoint evaluation cases."
        ),
        brier_score=raw_data.brier_score,
        expected_calibration_error=raw_data.expected_calibration_error,
        maximum_calibration_error=raw_data.maximum_calibration_error,
        raw_confidence_mean=raw_data.raw_confidence_mean,
        calibrated_confidence_mean=raw_data.calibrated_confidence_mean,
        calibrated_brier_score=round(float(cal_brier), 4),
        calibrated_expected_calibration_error=round(float(cal_ece), 4),
        calibration_method="Platt Scaling (Logistic Sigmoid)",
        bins=[CalibrationBinSchema(**b.to_dict()) for b in raw_data.bins],
        calibrated_bins=[CalibrationBinSchema(**b.to_dict()) for b in cal_bins],
        selective_risk_coverage=[SelectiveCoverageSchema(**c) for c in raw_data.selective_risk_coverage],
        interpretation=(
            f"{interpretation} This curve is based on a generated simulation, not "
            "dataset/metadata.json fraud labels or independently reviewed case outcomes."
        ),
    )

