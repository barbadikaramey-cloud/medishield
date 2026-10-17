"""Authoritative runner that executes the full multi-agent workflow for all 30 benchmark cases one by one.

Performs true end-to-end adjudication:
1. Streams document rasters to LocalFilesystemStorageBackend.
2. Persists Case and Document rows in SQLite database.
3. Coordinates full DAG execution via process_case_workflow_task / CaseWorkflowCoordinator:
   - Phase 1: OCR Preprocessor, Split & Group, Classifier, Completeness
   - Phase 2: KYC Specialist, Claims Specialist, Policy Context Specialist
   - Phase 3: JoinClaimReady Barrier & Policy Coverage Specialist
   - Phase 4: Fraud Specialist
   - Phase 5: JoinAll Barrier & Faithfulness Gate
   - Phase 6: Deterministic Decision Engine (Rules P1-P8)
   - Phase 7: Cryptographic Chained Audit Persistence (HMAC-SHA256)
4. Persists terminal states, active decisions, and audit events to the database.
5. Computes empirical benchmark metrics, writes diagnostic summary, and regenerates reports.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

# Disable strict eval flag for local filesystem fallback
os.environ["MEDISHIELD_STRICT_EVAL"] = "0"
sys.stdout.reconfigure(line_buffering=True)

# Prevent macOS ARM-64 OpenCV / KleidiCV multithreading segmentation fault
try:
    import cv2
    cv2.setNumThreads(0)
except ImportError:
    pass

from sqlalchemy import text
from sqlalchemy.orm import Session

from medishield.core.config import settings
from medishield.db.enums import (
    DocumentType,
    WorkflowState,
)
from medishield.db.models import (
    Case,
    Decision,
    Document,
)
from medishield.db.seeder import seed_benchmark_dataset
from medishield.db.session import sync_engine
from medishield.intake.storage import LocalFilesystemStorageBackend
from medishield.worker.tasks import process_case_workflow_task

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_ROOT = REPO_ROOT / "dataset"
METADATA_PATH = DATASET_ROOT / "metadata.json"
DIAGNOSTIC_PATH = REPO_ROOT / "scripts" / "diagnostic_summary.json"
FAST_EVAL_PATH = REPO_ROOT / "scripts" / "fast_eval_results.json"

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger("medishield.workflow_runner")


def reset_case_persistence(session: Session) -> None:
    """Clears all case and document rows before running full workflow from scratch."""
    print("Clearing prior case data from database...", flush=True)
    session.execute(text("DELETE FROM review_locks"))
    session.execute(text("DELETE FROM human_reviews"))
    session.execute(text("DELETE FROM audit_events"))
    session.execute(text("DELETE FROM decisions"))
    session.execute(text("DELETE FROM documents"))
    session.execute(text("DELETE FROM claim_lines"))
    session.execute(text("DELETE FROM claims"))
    session.execute(text("DELETE FROM cases"))
    session.commit()
    print("Database cleared. Ready for fresh sequential ingestion.", flush=True)


def main() -> None:
    print("=" * 80)
    print("MEDISHIELD FULL MULTI-AGENT WORKFLOW — 30 CASES SEQUENTIAL RUNNER")
    print("=" * 80)

    # 1. Ensure master dataset seeded
    with Session(sync_engine) as sess:
        member_cnt = sess.execute(text("SELECT count(*) FROM members")).scalar()
        if member_cnt < 30:
            print("Seeding master members and policy entities...", flush=True)
            seed_benchmark_dataset(sess)

        # Clear previous run
        reset_case_persistence(sess)

    # 2. Load dataset metadata & group by cluster
    with open(METADATA_PATH, encoding="utf-8") as f:
        metadata = json.load(f)

    clusters: dict[str, list[dict]] = defaultdict(list)
    for doc in metadata:
        cid = doc.get("case_cluster_id")
        if cid and cid.startswith("C_"):
            clusters[cid].append(doc)

    sorted_cids = sorted(clusters.keys())
    print(f"Loaded {len(sorted_cids)} patient clusters ({len(metadata)} total documents) from dataset/.\n", flush=True)

    storage = LocalFilesystemStorageBackend(base_directory=settings.STORAGE_DIR)

    eval_results: list[dict] = []
    first_failing_rules = Counter()
    all_failing_rules = Counter()
    blocker_cooccurrence = Counter()
    confidence_correct: list[float] = []
    confidence_wrong: list[float] = []

    approved_count = 0
    expected_approve_count = 0
    approved_of_expected_approve = 0
    false_approve_count = 0
    false_deny_count = 0

    total_start = time.perf_counter()

    for idx, cid in enumerate(sorted_cids, start=1):
        docs = clusters[cid]
        pt_id = next((d.get("patient_id") for d in docs if d.get("patient_id")), "UNKNOWN")
        policy_num = next((d.get("policy_number") for d in docs if d.get("policy_number")), "UNKNOWN")
        is_fraud = any(d.get("fraud_label", False) for d in docs)
        expected_decision = "ESCALATE" if is_fraud else "APPROVE"

        if not is_fraud:
            expected_approve_count += 1

        case_id = f"CASE-{cid.replace('_', '')}-{pt_id}"
        case_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"case_{case_id}")
        now = datetime.now(UTC)

        # Ingest documents into storage & DB
        with Session(sync_engine) as sess:
            db_case = Case(
                id=case_uuid,
                case_id=case_id,
                workflow_state=WorkflowState.RECEIVED,
                workflow_version="v1.0.0",
                is_complete=False,
                version=1,
                created_at=now,
                updated_at=now,
            )
            sess.add(db_case)
            sess.flush()

            for doc_idx, d in enumerate(docs, start=1):
                cat = d.get("category", "")
                doc_id = d.get("doc_id", f"DOC-{cid}-{doc_idx:02d}")
                file_path_raw = d.get("file_path", "")
                fname = Path(file_path_raw.replace("\\", "/")).name if file_path_raw else f"{doc_id}.png"

                fpath = DATASET_ROOT / cat / fname
                if not fpath.exists():
                    fpath = DATASET_ROOT / cat / f"{doc_id}.png"

                content = fpath.read_bytes() if fpath.exists() else b""
                obj_key = f"cases/{case_id}/raw/{doc_id}_{fname}"
                storage.put_bytes(obj_key, content, "image/png")

                doc_rec = Document(
                    id=uuid.uuid5(uuid.NAMESPACE_DNS, f"doc_{case_id}_{doc_id}"),
                    document_id=doc_id,
                    case_id=db_case.id,
                    file_name=fname,
                    object_key=obj_key,
                    mime_type="image/png",
                    checksum=uuid.uuid5(uuid.NAMESPACE_DNS, fname).hex,
                    size_bytes=len(content),
                    doc_type=DocumentType.UNKNOWN,
                    created_at=now,
                )
                sess.add(doc_rec)
            sess.commit()

        # Execute full workflow task
        t0 = time.perf_counter()
        with Session(sync_engine) as sess:
            process_case_workflow_task.run(
                case_id=case_id,
                trigger_reason="INITIAL_INTAKE",
                db_session=sess,
                storage=storage,
            )
        elapsed = time.perf_counter() - t0

        # Read persisted outcomes
        with Session(sync_engine) as sess:
            case_row = sess.query(Case).filter(Case.id == case_uuid).first()
            dec_row = (
                sess.query(Decision)
                .filter(Decision.case_id == case_uuid, Decision.is_active.is_(True))
                .first()
            )

            final_decision = case_row.current_decision.value if case_row and case_row.current_decision else "ESCALATE"
            sub_reason = case_row.current_sub_reason.value if case_row and case_row.current_sub_reason else None
            conf = float(case_row.decision_confidence) if case_row and case_row.decision_confidence else 0.50
            rule_id = dec_row.rule_id_matched if dec_row else "UNKNOWN"
            justification = dec_row.justification if dec_row else ""
            snapshot = (dec_row.agent_summary_snapshot or {}) if dec_row else {}

        kyc_snap = snapshot.get("kyc") or {}
        claims_snap = snapshot.get("claims") or {}
        pol_snap = snapshot.get("policy") or {}
        fraud_snap = snapshot.get("fraud") or {}
        clin_snap = snapshot.get("clinical_alignment") or snapshot.get("clinical") or {}
        consistency_snap = snapshot.get("consistency") or {}

        kyc_v = kyc_snap.get("verdict", "PASS")
        kyc_c = float(kyc_snap.get("confidence", 0.95))
        claims_v = claims_snap.get("verdict", "PASS")
        claims_c = float(claims_snap.get("confidence", 0.90))
        pol_v = pol_snap.get("verdict", "PASS")
        pol_c = float(pol_snap.get("confidence", 0.95))
        fraud_s = float(fraud_snap.get("fraud_score", 0.0))
        fraud_v = fraud_snap.get("verdict", "PASS")
        clin_v = clin_snap.get("verdict", "NONE")
        clin_c = clin_snap.get("confidence")
        explanation = snapshot.get("detailed_explanation") or justification

        # Determine failing checks
        failing_checks: list[str] = []
        if rule_id and rule_id != "RULE-P6-CLEAN-PASS":
            failing_checks.append(rule_id)

        first_failing_rules[rule_id] += 1
        for fc in failing_checks:
            all_failing_rules[fc] += 1

        is_approved = (final_decision == "APPROVE")
        if is_approved:
            approved_count += 1
            if not is_fraud:
                approved_of_expected_approve += 1
                confidence_correct.append(conf)
            else:
                false_approve_count += 1
                confidence_wrong.append(conf)
        else:
            if not is_fraud:
                false_deny_count += 1
                confidence_wrong.append(conf)
            else:
                confidence_correct.append(conf)

        print(
            f"[{idx:02d}/30] {cid} ({case_id}) -> {final_decision:<8} via {rule_id:<25} "
            f"[KYC: {kyc_v} | Claims: {claims_v} | Policy: {pol_v} | Fraud: {fraud_s:.2f} | Clin: {clin_v}] "
            f"(conf: {conf:.2f}, {elapsed:.2f}s)",
            flush=True,
        )

        eval_results.append({
            "cluster_id": cid,
            "patient_id": pt_id,
            "policy_number": policy_num,
            "final_decision": final_decision,
            "rule_id_matched": rule_id,
            "decision_confidence": conf,
            "escalation_sub_reason": sub_reason,
            "all_failing_checks": failing_checks,
            "kyc_verdict": kyc_v,
            "kyc_confidence": kyc_c,
            "claims_verdict": claims_v,
            "claims_confidence": claims_c,
            "policy_verdict": pol_v,
            "policy_confidence": pol_c,
            "fraud_score": fraud_s,
            "fraud_verdict": fraud_v,
            "clinical_verdict": clin_v,
            "clinical_confidence": clin_c,
            "expected_decision": expected_decision,
            "is_fraud_ground_truth": is_fraud,
            "justification": justification,
            "explanation": explanation,
            "consistency": consistency_snap,
        })

    total_duration = time.perf_counter() - total_start
    total_cases = len(eval_results)

    print("\n" + "=" * 80)
    print("EMPIRICAL BENCHMARK METRICS SUMMARY (FULL MULTI-AGENT WORKFLOW)")
    print("=" * 80)
    print(f"Total Cases Adjudicated               : {total_cases}")
    print(f"Total Approved (P6 STP)               : {approved_count} / {total_cases} ({approved_count/total_cases*100:.1f}%)")
    print(f"Ground Truth Expected Approvals       : {expected_approve_count}")
    print(f"Approved among Ground Truth Approvals : {approved_of_expected_approve} / {expected_approve_count} ({approved_of_expected_approve/max(expected_approve_count,1)*100:.1f}%)")
    print(f"False-Approve Rate                    : {false_approve_count} / {total_cases} ({false_approve_count/total_cases*100:.1f}%)")
    print(f"False-Recommend-Deny (Reject) Rate    : {false_deny_count} / {expected_approve_count} ({false_deny_count/max(expected_approve_count,1)*100:.1f}%)")
    print(f"Total Execution Time                  : {total_duration:.2f}s (avg: {total_duration/total_cases:.2f}s/case)")

    print("\n--- Distribution of Matched Rules ---")
    for rule, count in first_failing_rules.most_common():
        print(f"  {rule:<32}: {count:>2} cases ({count/total_cases*100:.1f}%)")

    # Save diagnostic summary
    diag_summary = {
        "total_cases": total_cases,
        "approved_count": approved_count,
        "expected_approve_count": expected_approve_count,
        "approved_of_expected_approve": approved_of_expected_approve,
        "false_approve_count": false_approve_count,
        "false_deny_count": false_deny_count,
        "first_failing_rules": dict(first_failing_rules),
        "all_failing_rules": dict(all_failing_rules),
        "blocker_cooccurrence": {f"{k[0]}___{k[1]}": v for k, v in blocker_cooccurrence.items()},
        "eval_results": eval_results,
        "confidence_correct": confidence_correct,
        "confidence_wrong": confidence_wrong,
        "execution_mode": "FULL_MULTI_AGENT_WORKFLOW",
        "total_duration_seconds": round(total_duration, 2),
    }

    with open(DIAGNOSTIC_PATH, "w", encoding="utf-8") as f:
        json.dump(diag_summary, f, indent=2, default=str)
    print(f"\nWrote full diagnostic summary to {DIAGNOSTIC_PATH}")

    # Also sync fast_eval_results.json for backwards-compatible consumers
    fast_eval_payload = {
        "metrics": {
            "total_cases": total_cases,
            "approved_count": approved_count,
            "approval_rate": round(approved_count / total_cases * 100.0, 1),
            "expected_approvals": expected_approve_count,
            "approved_of_expected": approved_of_expected_approve,
            "percent_approved_of_expected": round(approved_of_expected_approve / max(expected_approve_count, 1) * 100.0, 1),
            "false_approve_count": false_approve_count,
            "false_approve_rate": round(false_approve_count / total_cases * 100.0, 1),
            "false_deny_count": false_deny_count,
            "false_deny_rate": round(false_deny_count / max(expected_approve_count, 1) * 100.0, 1),
        },
        "first_failing_rules": dict(first_failing_rules),
        "all_failing_rules": dict(all_failing_rules),
        "blocker_pairs": {},
        "confidence_correct": confidence_correct,
        "confidence_wrong": confidence_wrong,
        "eval_results": eval_results,
    }
    with open(FAST_EVAL_PATH, "w", encoding="utf-8") as f:
        json.dump(fast_eval_payload, f, indent=2, default=str)
    print(f"Wrote evaluation metrics to {FAST_EVAL_PATH}")


if __name__ == "__main__":
    main()
