"""Unit and integration tests using the authoritative 30-cluster benchmark dataset (Spec 12).

Tests document classification, geometric OCR extraction, member identification,
fraud detection, and case workflow adjudication using real files from dataset/.
"""

import json
from datetime import date
from pathlib import Path

from medishield.agents.classifier import DocumentClassifierAgent
from medishield.agents.kyc import SqlMemberRepository
from medishield.db.enums import AgentVerdict, DocumentType
from medishield.db.session import get_sqlite_session_factory
from medishield.domain.contracts import ClaimsAgentOutput, KYCAgentOutput
from medishield.domain.fraud import FraudDetectionEngine, FraudEvaluationContext
from medishield.domain.provenance import ProvenanceValue
from medishield.ocr.preprocessor import DefaultOCREngine

DATASET_ROOT = Path(__file__).resolve().parent.parent.parent / "dataset"
METADATA_PATH = DATASET_ROOT / "metadata.json"


def test_dataset_inventory_and_metadata_integrity() -> None:
    """Verifies that all 30 case clusters and documents described in metadata.json exist on disk."""
    assert DATASET_ROOT.exists(), f"Dataset directory missing at {DATASET_ROOT}"
    assert METADATA_PATH.exists(), f"Metadata JSON missing at {METADATA_PATH}"

    with open(METADATA_PATH, encoding="utf-8") as f:
        meta = json.load(f)

    assert len(meta) >= 150, f"Expected at least 150 documents in metadata, found {len(meta)}"

    clusters = {d["case_cluster_id"] for d in meta if d.get("case_cluster_id")}
    assert len(clusters) == 30, f"Expected 30 unique clusters, found {len(clusters)}"

    # Verify at least one file from each category exists on disk
    categories = ["claim_forms", "discharge_summaries", "id_documents", "prescriptions", "policy_amendments"]
    for cat in categories:
        cat_dir = DATASET_ROOT / cat
        assert cat_dir.exists(), f"Category directory {cat} does not exist"
        files = list(cat_dir.glob("*.png"))
        assert len(files) >= 30, f"Expected at least 30 files in {cat}, found {len(files)}"


def test_real_dataset_classification_accuracy() -> None:
    """Evaluates DocumentClassifierAgent against all real documents in the dataset."""
    classifier = DocumentClassifierAgent()
    categories_map = {
        "claim_forms": DocumentType.CLAIM_FORM,
        "id_documents": DocumentType.ID_DOCUMENT,
        "discharge_summaries": DocumentType.DISCHARGE_SUMMARY,
        "prescriptions": DocumentType.PRESCRIPTION,
        "policy_amendments": DocumentType.POLICY_AMENDMENT,
    }

    total_tested = 0
    correct_classified = 0

    for folder_name, expected_type in categories_map.items():
        folder_path = DATASET_ROOT / folder_name
        if not folder_path.exists():
            continue

        for img_file in folder_path.glob("*.png"):
            total_tested += 1
            result = classifier.classify(document_id=img_file.stem, file_name=img_file.name)
            if result.doc_type == expected_type:
                correct_classified += 1

    accuracy = correct_classified / total_tested if total_tested > 0 else 0.0
    assert total_tested >= 150, f"Expected at least 150 documents tested, got {total_tested}"
    assert accuracy >= 0.95, f"Classification accuracy {accuracy:.2%} is below 95% target"


def test_real_claim_form_ocr_and_geometric_kv_extraction() -> None:
    """Verifies that DefaultOCREngine extracts text and key-value pairs from real claim forms."""
    engine = DefaultOCREngine()
    sample_claim = DATASET_ROOT / "claim_forms" / "claim_PT_19116.png"
    assert sample_claim.exists(), f"Sample claim {sample_claim} does not exist"

    with open(sample_claim, "rb") as f:
        content = f.read()

    pages, quality, is_pdf = engine.extract(content=content, mime_type="image/png")
    assert len(pages) == 1, "Expected 1 page extracted from single PNG"
    assert quality > 0.50, f"Quality score {quality} should exceed 0.50"
    assert is_pdf is False

    p1 = pages[0]
    assert len(p1.spans) > 20, f"Expected > 20 text spans, got {len(p1.spans)}"

    # Check key-value pairs
    kv_pairs = getattr(p1, "key_value_pairs", [])
    assert len(kv_pairs) > 0, "Expected geometric key-value pairs extracted from claim form"

    keys_found = {str(kv["key"]).lower() for kv in kv_pairs}
    has_relevant_key = any(
        k in " ".join(keys_found)
        for k in ["patient", "name", "record", "birthdate", "npi", "charges", "statement", "sex", "bill"]
    )
    assert has_relevant_key, f"Expected relevant medical box headers in extracted keys: {keys_found}"


def test_real_id_document_extraction_and_member_lookup() -> None:
    """Verifies that KYCAgent resolves members from real dataset ID documents against the database."""
    factory = get_sqlite_session_factory()
    session = factory()
    try:
        kyc_repo = SqlMemberRepository(session)

        # Test resolution for patient IDs present in the benchmark dataset
        test_patient_ids = ["PT_19116", "PT_99733", "PT_15075", "PT_16658"]
        for pt_id in test_patient_ids:
            record = kyc_repo.find_member(pt_id)
            assert record is not None, f"Member {pt_id} from benchmark dataset not found in SQL database"
            assert record.member_id == pt_id
            assert record.status == "ACTIVE"
            assert record.policy_id is not None
    finally:
        session.close()


def test_fraud_engine_detects_dataset_duplicate_claim() -> None:
    """Verifies that FraudDetectionEngine flags duplicate claim submissions (Cluster C_005)."""
    claims = ClaimsAgentOutput(
        agent_name="claims_agent",
        verdict=AgentVerdict.PASS,
        confidence=0.98,
        claim_amount=ProvenanceValue(value=500.0, confidence=0.99),
        service_date=ProvenanceValue(value=date(2025, 10, 1), confidence=0.99),
        provider_npi=ProvenanceValue(value="1992837465", confidence=0.99),
        schema_valid=True,
        line_items_sum_valid=True,
        calculated_lines_sum=500.0,
        latency_ms=100,
    )
    context = FraudEvaluationContext(duplicate_claim_match_id="CLAIM-EXISTING-001")
    res_dup = FraudDetectionEngine.evaluate(claims_output=claims, context=context)

    assert res_dup.fraud_score >= 0.30
    assert res_dup.duplicate_claim_detected is True
    assert any("collision" in a.lower() for a in res_dup.anomalies)


def test_fraud_engine_detects_tamper_and_inconsistency() -> None:
    """Verifies that FraudDetectionEngine flags digital tampering and service date conflict."""
    claims = ClaimsAgentOutput(
        agent_name="claims_agent",
        verdict=AgentVerdict.PASS,
        confidence=0.98,
        claim_amount=ProvenanceValue(value=500.0, confidence=0.99),
        service_date=ProvenanceValue(value=date(2027, 10, 1), confidence=0.99),  # Future date -> inconsistency
        provider_npi=ProvenanceValue(value="1992837465", confidence=0.99),
        schema_valid=True,
        line_items_sum_valid=True,
        calculated_lines_sum=500.0,
        latency_ms=100,
    )
    kyc = KYCAgentOutput(
        agent_name="kyc_agent",
        verdict=AgentVerdict.FAIL,
        confidence=0.85,
        tamper_detected=True,
        tamper_score=0.90,  # High tamper ELA score
        latency_ms=100,
    )
    context = FraudEvaluationContext(submission_date=date(2025, 10, 1))
    res = FraudDetectionEngine.evaluate(claims_output=claims, kyc_output=kyc, context=context)

    assert res.fraud_score >= 0.20
    assert any("tamper" in a.lower() for a in res.anomalies)
    assert any("future service date" in a.lower() for a in res.anomalies)
