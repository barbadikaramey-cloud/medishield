"""Unit tests for Two-Stage Policy Evaluation Agent and RAG Grounding (FEAT-009, Spec 09, ADR-003, ADR-006)."""

from datetime import date

from medishield.agents.policy import (
    InMemoryPolicyEnrollmentRepository,
    PolicyChunkRecord,
    PolicyContextAgent,
    PolicyCorpusRepository,
    PolicyCoverageAgent,
    PolicyEnrollmentRecord,
    resolve_claim_ready_policy,
)
from medishield.db.enums import AgentVerdict
from medishield.domain.contracts import CandidatePolicy

# ── Stage A: Policy Context Agent Tests ──────────────────────────────────────


def test_policy_context_agent_pass_found_enrollments() -> None:
    repo = InMemoryPolicyEnrollmentRepository([
        PolicyEnrollmentRecord(
            member_id="MEM-001",
            policy_id="POL-CORP-2026",
            version_tag="v2.1",
            plan_name="Comprehensive Gold PPO",
            effective_start_date=date(2026, 1, 1),
            effective_end_date=date(2026, 12, 31),
            active_riders=["DENTAL", "VISION"],
        ),
        PolicyEnrollmentRecord(
            member_id="MEM-001",
            policy_id="POL-CORP-2025",
            version_tag="v1.0",
            plan_name="Comprehensive Gold PPO",
            effective_start_date=date(2025, 1, 1),
            effective_end_date=date(2025, 12, 31),
        ),
    ])
    agent = PolicyContextAgent(enrollment_repo=repo)

    result = agent.execute(member_id="MEM-001")

    assert result.verdict == AgentVerdict.PASS
    assert result.member_enrollment_id == "MEM-001"
    assert len(result.candidate_policies) == 2
    assert result.candidate_policies[0].policy_id == "POL-CORP-2026"
    assert result.candidate_policies[0].policy_version == "v2.1"
    assert "DENTAL" in result.candidate_policies[0].active_riders


def test_policy_context_agent_unavailable_unknown_member() -> None:
    repo = InMemoryPolicyEnrollmentRepository()
    agent = PolicyContextAgent(enrollment_repo=repo)

    result = agent.execute(member_id="MEM-NONEXISTENT")

    assert result.verdict == AgentVerdict.UNAVAILABLE
    assert len(result.candidate_policies) == 0


def test_policy_context_agent_indeterminate_empty_member_id() -> None:
    repo = InMemoryPolicyEnrollmentRepository()
    agent = PolicyContextAgent(enrollment_repo=repo)

    result = agent.execute(member_id="")

    assert result.verdict == AgentVerdict.INDETERMINATE
    assert len(result.candidate_policies) == 0


# ── JoinClaimReady Synchronization Barrier Tests ──────────────────────────────


def test_resolve_claim_ready_policy_matching_date() -> None:
    candidates = [
        CandidatePolicy(
            policy_id="POL-2025",
            policy_version="v1.0",
            effective_start_date=date(2025, 1, 1),
            effective_end_date=date(2025, 12, 31),
            plan_name="Silver Plan",
        ),
        CandidatePolicy(
            policy_id="POL-2026",
            policy_version="v2.0",
            effective_start_date=date(2026, 1, 1),
            effective_end_date=date(2026, 12, 31),
            plan_name="Gold Plan",
        ),
    ]

    # Service date in 2026
    ok, matched = resolve_claim_ready_policy(candidates, service_date=date(2026, 6, 15))
    assert ok is True
    assert matched is not None
    assert matched.policy_id == "POL-2026"
    assert matched.policy_version == "v2.0"

    # Service date in 2025
    ok, matched_2025 = resolve_claim_ready_policy(candidates, service_date=date(2025, 3, 10))
    assert ok is True
    assert matched_2025 is not None
    assert matched_2025.policy_id == "POL-2025"


def test_resolve_claim_ready_policy_open_ended_end_date() -> None:
    candidates = [
        CandidatePolicy(
            policy_id="POL-OPEN",
            policy_version="v1.0",
            effective_start_date=date(2026, 1, 1),
            effective_end_date=None,  # Active indefinitely
            plan_name="Continuous Health Plan",
        )
    ]

    ok, matched = resolve_claim_ready_policy(candidates, service_date=date(2026, 11, 20))
    assert ok is True
    assert matched is not None
    assert matched.policy_id == "POL-OPEN"


def test_resolve_claim_ready_policy_out_of_bounds() -> None:
    candidates = [
        CandidatePolicy(
            policy_id="POL-2026",
            policy_version="v2.0",
            effective_start_date=date(2026, 1, 1),
            effective_end_date=date(2026, 12, 31),
            plan_name="Gold Plan",
        )
    ]

    # Date before coverage start
    ok_before, matched_before = resolve_claim_ready_policy(candidates, service_date=date(2025, 12, 31))
    assert ok_before is False
    assert matched_before is None

    # Date after coverage expiration
    ok_after, matched_after = resolve_claim_ready_policy(candidates, service_date=date(2027, 1, 1))
    assert ok_after is False
    assert matched_after is None


# ── Stage B: Policy Coverage Agent & RAG Evaluation Tests ────────────────────


def _build_test_policy_corpus() -> PolicyCorpusRepository:
    """Helper populating authoritative policy corpus."""
    repo = PolicyCorpusRepository()
    # 1. Outpatient Diagnostic & Evaluation Clause (Covered)
    repo.add_chunk(
        PolicyChunkRecord(
            chunk_id="chk-001",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            section_number="SEC-3.1",
            section_title="Outpatient Evaluation and Management",
            chunk_text=(
                "Outpatient office visits and routine diagnostic evaluations are covered benefits. "
                "The insurer pays 80% of allowed charges after the annual deductible of $500.00 is satisfied. "
                "Co-payment of 20% applies to in-network professional services."
            ),
            page_number=12,
            is_exclusion=False,
            requires_prior_auth=False,
            covered_percentage=80.0,
            deductible_limit=500.0,
            copay_percentage=20.0,
            matching_cpt_codes=["99213", "99214", "93000"],
        )
    )

    # 2. Inpatient Surgical with Prior Authorization (Covered if Prior Auth)
    repo.add_chunk(
        PolicyChunkRecord(
            chunk_id="chk-002",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            section_number="SEC-5.4",
            section_title="Inpatient Surgical and Major Procedures",
            chunk_text=(
                "Major elective surgical procedures including laparoscopic cholecystectomy require prior authorization "
                "submitted at least 48 hours in advance. Covered at 90% after deductible when prior authorization is approved."
            ),
            page_number=25,
            is_exclusion=False,
            requires_prior_auth=True,
            covered_percentage=90.0,
            deductible_limit=500.0,
            copay_percentage=10.0,
            matching_cpt_codes=["47562"],
        )
    )

    # 3. Cosmetic & Elective Exclusion (Excluded)
    repo.add_chunk(
        PolicyChunkRecord(
            chunk_id="chk-003",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            section_number="SEC-9.2",
            section_title="Cosmetic and Elective Resurfacing Exclusion",
            chunk_text=(
                "Cosmetic surgery, dermabrasion, and elective non-reconstructive procedures are strictly excluded "
                "from policy coverage. No benefit is payable for services billed under CPT 15780."
            ),
            page_number=44,
            is_exclusion=True,
            requires_prior_auth=False,
            covered_percentage=0.0,
            matching_cpt_codes=["15780"],
        )
    )

    return repo


def test_policy_coverage_agent_pass_clean_benefit() -> None:
    corpus = _build_test_policy_corpus()
    agent = PolicyCoverageAgent(corpus_repo=corpus)

    result = agent.execute(
        policy_id="POL-CORP-2026",
        policy_version="v2.0",
        cpt_codes=["99213", "93000"],
        claim_amount=350.00,
    )

    assert result.verdict == AgentVerdict.PASS
    assert result.covered is True
    assert result.coverage_percentage == 80.0
    assert result.confidence >= 0.90
    assert len(result.cited_clauses) >= 1
    assert all(c.verified_in_corpus for c in result.cited_clauses)
    assert len(result.identified_exclusions) == 0


def test_policy_coverage_agent_fail_exclusion_clause() -> None:
    corpus = _build_test_policy_corpus()
    agent = PolicyCoverageAgent(corpus_repo=corpus)

    result = agent.execute(
        policy_id="POL-CORP-2026",
        policy_version="v2.0",
        cpt_codes=["15780"],  # Cosmetic dermabrasion
        claim_amount=1200.00,
    )

    assert result.verdict == AgentVerdict.FAIL
    assert result.covered is False
    assert result.coverage_percentage == 0.0
    assert len(result.identified_exclusions) >= 1
    assert "SEC-9.2: Cosmetic and Elective Resurfacing Exclusion" in result.identified_exclusions
    assert "Policy Exclusion" in result.reasoning_summary
    assert result.exclusion_clause_verified is True
    assert result.applicable_service_codes == ["15780"]


def test_exclusion_without_billed_code_is_not_a_policy_fail() -> None:
    corpus = _build_test_policy_corpus()
    agent = PolicyCoverageAgent(corpus_repo=corpus)

    result = agent.execute(
        policy_id="POL-CORP-2026",
        policy_version="v2.0",
        cpt_codes=["99999"],
    )

    assert result.verdict == AgentVerdict.INDETERMINATE
    assert result.exclusion_clause_verified is False


def test_policy_coverage_agent_prior_auth_missing_indeterminate() -> None:
    corpus = _build_test_policy_corpus()
    agent = PolicyCoverageAgent(corpus_repo=corpus)

    result = agent.execute(
        policy_id="POL-CORP-2026",
        policy_version="v2.0",
        cpt_codes=["47562"],  # Requires prior auth
        claim_amount=4500.00,
        prior_authorization_present=False,
    )

    assert result.verdict == AgentVerdict.INDETERMINATE
    assert result.prior_authorization_required is True
    assert result.prior_authorization_present is False
    assert "Prior authorization required" in result.reasoning_summary


def test_policy_coverage_agent_prior_auth_satisfied_pass() -> None:
    corpus = _build_test_policy_corpus()
    agent = PolicyCoverageAgent(corpus_repo=corpus)

    result = agent.execute(
        policy_id="POL-CORP-2026",
        policy_version="v2.0",
        cpt_codes=["47562"],
        claim_amount=4500.00,
        prior_authorization_present=True,
    )

    assert result.verdict == AgentVerdict.PASS
    assert result.prior_authorization_required is True
    assert result.prior_authorization_present is True
    assert result.covered is True
    assert result.coverage_percentage in (80.0, 90.0)


def test_policy_coverage_agent_no_cpt_codes_indeterminate() -> None:
    corpus = _build_test_policy_corpus()
    agent = PolicyCoverageAgent(corpus_repo=corpus)

    result = agent.execute(
        policy_id="POL-CORP-2026",
        policy_version="v2.0",
        cpt_codes=[],
    )

    assert result.verdict == AgentVerdict.INDETERMINATE
    assert "No procedure (CPT)" in result.reasoning_summary


def test_policy_coverage_agent_unmatched_codes_indeterminate() -> None:
    corpus = _build_test_policy_corpus()
    agent = PolicyCoverageAgent(corpus_repo=corpus)

    result = agent.execute(
        policy_id="POL-CORP-2026",
        policy_version="v2.0",
        cpt_codes=["99999"],  # Code not in test corpus
    )

    assert result.verdict == AgentVerdict.INDETERMINATE
    assert "No matching policy clauses found" in result.reasoning_summary


def test_gate_2_grounding_verification() -> None:
    corpus = _build_test_policy_corpus()

    # Exact clause verified
    is_valid, chunk = corpus.verify_clause(
        policy_id="POL-CORP-2026",
        version_tag="v2.0",
        verbatim_text="Outpatient office visits and routine diagnostic evaluations are covered benefits.",
    )
    assert is_valid is True
    assert chunk is not None
    assert chunk.section_number == "SEC-3.1"

    # Fabricated / altered clause rejected
    is_valid_fake, fake_chunk = corpus.verify_clause(
        policy_id="POL-CORP-2026",
        version_tag="v2.0",
        verbatim_text="All elective cosmetic treatments are 100% covered without limits.",
    )
    assert is_valid_fake is False
    assert fake_chunk is None


def test_admin_publishing_firewall_adr006() -> None:
    """Verifies that corpus only accepts chunks from authoritative repository (ADR-006)."""
    corpus = PolicyCorpusRepository()

    # Customer uploads cannot insert into authoritative corpus
    # Only admin PolicyChunkRecord instances can be indexed
    admin_chunk = PolicyChunkRecord(
        chunk_id="auth-chk-1",
        policy_id="POL-ADMIN",
        version_tag="v1.0",
        section_number="SEC-1.0",
        section_title="Authoritative Benefit",
        chunk_text="This is an administrator-published policy clause.",
        page_number=1,
    )
    corpus.add_chunk(admin_chunk)

    results = corpus.query_chunks(
        policy_id="POL-ADMIN",
        version_tag="v1.0",
        query_text="administrator-published",
    )
    assert len(results) == 1
    assert results[0].chunk_id == "auth-chk-1"


def test_policy_context_agent_indeterminate_unknown_member_id() -> None:
    repo = InMemoryPolicyEnrollmentRepository()
    agent = PolicyContextAgent(enrollment_repo=repo)

    result = agent.execute(member_id="UNKNOWN")
    assert result.verdict == AgentVerdict.INDETERMINATE
    assert len(result.candidate_policies) == 0

    result2 = agent.execute(member_id="NOT_FOUND")
    assert result2.verdict == AgentVerdict.INDETERMINATE


def test_policy_coverage_agent_ungrounded_citations_no_unbound_error() -> None:
    # Empty corpus where no chunks are verified
    empty_corpus = PolicyCorpusRepository(chunks=[])
    agent = PolicyCoverageAgent(corpus_repo=empty_corpus)

    # Chunks passed directly to simulate unverified retrieved chunks
    unverified_chunk = PolicyChunkRecord(
        chunk_id="test-chunk-1",
        policy_id="POL-TEST",
        version_tag="v1.0",
        section_number="SEC-1.1",
        section_title="Unverified Benefit",
        chunk_text="This text will not verify in empty corpus.",
        page_number=5,
        is_exclusion=False,
        requires_prior_auth=False,
        covered_percentage=80.0,
        matching_cpt_codes=["99213"],
    )

    result = agent.execute(
        policy_id="POL-TEST",
        policy_version="v1.0",
        cpt_codes=["99213"],
        matched_chunks=[unverified_chunk],
    )

    assert result.verdict == AgentVerdict.INDETERMINATE
    assert result.confidence == 0.40
    assert result.coverage_percentage == 80.0
    assert "could not be verified in the authoritative corpus" in result.reasoning_summary


def test_policy_coverage_agent_unrelated_chunk_fails_positive_scope() -> None:
    """Proves that a retrieved non-exclusion clause (e.g. SEC-5.5 Room and Board) cannot cover an unrelated procedure (e.g. CPT 21120)."""
    room_board_chunk = PolicyChunkRecord(
        chunk_id="chk-rb-01",
        policy_id="POL-CORP-2026",
        version_tag="v2.0",
        section_number="SEC-5.5",
        section_title="Inpatient Room and Board Limits",
        chunk_text="Inpatient room and board (revenue codes 0110, 0111, 0120) are covered at 80% after deductible.",
        page_number=26,
        is_exclusion=False,
        requires_prior_auth=False,
        covered_percentage=80.0,
        matching_rev_codes=["0110", "0111", "0120"],
        matching_cpt_codes=[],
    )
    corpus = PolicyCorpusRepository(chunks=[room_board_chunk])
    agent = PolicyCoverageAgent(corpus_repo=corpus)

    # Claim for procedure 21120 (unrelated to room and board)
    result = agent.execute(
        policy_id="POL-CORP-2026",
        policy_version="v2.0",
        cpt_codes=["21120"],
        claim_amount=1500.00,
        matched_chunks=[room_board_chunk],
    )

    # Must be INDETERMINATE, not PASS!
    assert result.verdict == AgentVerdict.INDETERMINATE
    assert result.covered is False
    assert result.coverage_percentage == 0.0
    assert "No applicable covered benefit clauses found" in result.reasoning_summary
    assert result.applicable_service_codes == []
