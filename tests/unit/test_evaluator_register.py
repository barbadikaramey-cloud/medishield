"""Unit tests for Evaluator Review Register and Metadata Governance (Spec Failure 7)."""

import json
from pathlib import Path


def test_evaluator_review_register_exists_and_conforms():
    """Verifies that the evaluator sidecar register documents disputed labels with PENDING disposition."""
    register_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "evaluator_review_register.json"
    assert register_path.exists(), "evaluator_review_register.json must exist in scripts/"

    with open(register_path, encoding="utf-8") as f:
        data = json.load(f)

    assert "version" in data
    assert "disputed_records" in data

    records = {r["patient_id"]: r for r in data["disputed_records"]}
    expected_disputed = {"PT_19116", "PT_69470", "PT_39451"}
    assert expected_disputed.issubset(set(records.keys()))

    # Verify PT_19116
    r_19116 = records["PT_19116"]
    assert r_19116["original_label"] == "readmission_30d"
    assert r_19116["case_cluster_id"] == "C_001"
    assert r_19116["doc_id"] == "discharge_PT_19116"
    assert r_19116["disposition"] == "PENDING"
    assert r_19116["benchmark_action"] == "EXCLUDE_FROM_SCORING"

    # Verify PT_69470
    r_69470 = records["PT_69470"]
    assert r_69470["original_label"] == "amount_under_10k"
    assert r_69470["case_cluster_id"] == "C_004"
    assert r_69470["doc_id"] == "claim_PT_69470"
    assert r_69470["disposition"] == "PENDING"
    assert r_69470["benchmark_action"] == "EXCLUDE_FROM_SCORING"

    # Verify PT_39451
    r_39451 = records["PT_39451"]
    assert r_39451["original_label"] == "name_mismatch"
    assert r_39451["case_cluster_id"] == "C_006"
    assert r_39451["doc_id"] == "id_PT_39451"
    assert r_39451["disposition"] == "PENDING"
    assert r_39451["benchmark_action"] == "EXCLUDE_FROM_SCORING"


def test_source_metadata_preserved_unchanged():
    """Verifies that dataset/metadata.json is preserved without silent edits."""
    metadata_path = Path(__file__).resolve().parent.parent.parent / "dataset" / "metadata.json"
    assert metadata_path.exists()

    with open(metadata_path, encoding="utf-8") as f:
        metadata = json.load(f)

    assert isinstance(metadata, list)
    assert len(metadata) >= 150

    # Ensure source metadata still retains original fraud_reason (not silently overwritten)
    pt_19116_items = [item for item in metadata if "19116" in str(item.get("doc_id", ""))]
    assert any(item.get("fraud_reason") == "readmission_30d" for item in pt_19116_items)

    pt_69470_items = [item for item in metadata if "69470" in str(item.get("doc_id", ""))]
    assert any(item.get("fraud_reason") == "amount_under_10k" for item in pt_69470_items)

    pt_39451_items = [item for item in metadata if "39451" in str(item.get("doc_id", ""))]
    assert any(item.get("fraud_reason") == "name_mismatch" for item in pt_39451_items)

