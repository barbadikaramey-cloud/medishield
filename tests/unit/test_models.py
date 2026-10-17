import medishield.db.models  # noqa: F401
from medishield.db.session import Base


def test_all_17_tables_registered():
    expected_tables = {
        "cases",
        "documents",
        "document_pages",
        "members",
        "policies",
        "policy_versions",
        "enrollments",
        "policy_chunks",
        "claims",
        "claim_lines",
        "agent_runs",
        "decisions",
        "review_locks",
        "human_reviews",
        "audit_events",
        "idempotency_keys",
        "medical_codes",
    }
    registered_tables = set(Base.metadata.tables.keys())
    assert expected_tables.issubset(registered_tables), (

        f"Missing tables: {expected_tables - registered_tables}"
    )


def test_cases_table_structure():
    cases_table = Base.metadata.tables["cases"]
    assert "case_id" in cases_table.columns
    assert "workflow_state" in cases_table.columns
    assert "current_decision" in cases_table.columns
    assert "current_sub_reason" in cases_table.columns
    assert "decision_confidence" in cases_table.columns
    assert "missing_documents" in cases_table.columns
    assert "version" in cases_table.columns


def test_documents_and_pages_foreign_keys():
    docs_table = Base.metadata.tables["documents"]
    pages_table = Base.metadata.tables["document_pages"]

    # Check documents FK to cases
    fk_cases = [fk.target_fullname for fk in docs_table.foreign_keys]
    assert "cases.id" in fk_cases

    # Check pages FK to documents
    fk_docs = [fk.target_fullname for fk in pages_table.foreign_keys]
    assert "documents.id" in fk_docs


def test_claims_and_lines_foreign_keys():
    claims_table = Base.metadata.tables["claims"]
    lines_table = Base.metadata.tables["claim_lines"]

    assert "cases.id" in [fk.target_fullname for fk in claims_table.foreign_keys]
    assert "claims.id" in [fk.target_fullname for fk in lines_table.foreign_keys]


    audit_table = Base.metadata.tables["audit_events"]
    assert "prev_hash" in audit_table.columns
    assert "current_hash" in audit_table.columns
    assert "event_payload" in audit_table.columns
    assert "actor_type" in audit_table.columns


def test_medical_codes_table_structure():
    codes_table = Base.metadata.tables["medical_codes"]
    assert "code" in codes_table.columns
    assert "coding_system" in codes_table.columns
    assert "short_description" in codes_table.columns
    assert "is_active" in codes_table.columns
    assert "standard_charge" in codes_table.columns
    assert "requires_prior_auth" in codes_table.columns

