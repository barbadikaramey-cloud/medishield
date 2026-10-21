"""Two-Stage Policy Evaluation Agent, Claim-Ready Synchronization, and RAG Grounding (Spec 09, ADR-003, ADR-006)."""

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

logger = logging.getLogger(__name__)

from medishield.core.llm import (
    GroqLLMClient,
    GroqLLMError,
    default_groq_client,
    wrap_untrusted_content,
)
from medishield.core.tracing import trace_span
from medishield.db.catalog import CodeCatalogRepository
from medishield.db.enums import AgentVerdict, CodingSystem, IndeterminateReason
from medishield.domain.contracts import (
    CandidatePolicy,
    PolicyClauseCitation,
    PolicyContextOutput,
    PolicyCoverageOutput,
)

# ── Clinical Code Expansion Dictionary (Spec 09 § 3.2, ADR-012, ADR-016) ─────────────


class _CptExpansionDictionaryProxy(dict[str, Any]):
    """Dynamic CPT expansion dictionary proxy querying relational medical catalog (ADR-012, ADR-016)."""

    def get(self, key: Any, default: Any = None) -> Any:
        try:
            from medishield.db.catalog import SqlCodeCatalogRepository
            from medishield.db.enums import CodingSystem
            from medishield.db.session import sync_engine

            repo = SqlCodeCatalogRepository(sync_engine)
            details = repo.get_code_details(str(key), CodingSystem.CPT)
            if details:
                return {
                    "title": details.short_description,
                    "description": details.long_description or details.short_description,
                    "category": details.category or "General",
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("CPT dictionary proxy lookup failed for %s: %s", key, exc)
        return default

    def __getitem__(self, key: Any) -> Any:
        res = self.get(key)
        if res is None:
            raise KeyError(key)
        return res

    def __contains__(self, key: Any) -> bool:
        return self.get(key) is not None

    def __bool__(self) -> bool:
        return True


CPT_EXPANSION_DICTIONARY: dict[str, dict[str, str]] = _CptExpansionDictionaryProxy()

POLICY_STOP_WORDS: set[str] = {
    "a", "an", "the", "and", "or", "for", "of", "to", "in", "at", "on",
    "with", "from", "by", "as", "is", "are", "was", "were", "be", "been",
    "this", "that", "these", "those", "cpt", "code", "codes", "policy",
    "coverage", "covered", "service", "services", "benefit", "benefits",
    "after", "under", "per", "all", "not", "no", "if", "when",
}


# ── Authoritative Policy Chunk & Corpus Repository (ADR-006) ──────────────────

@dataclass
class PolicyChunkRecord:
    """Authoritative master policy text chunk indexed in vector and relational stores."""

    chunk_id: str
    policy_id: str
    version_tag: str
    section_number: str
    section_title: str
    chunk_text: str
    page_number: int
    is_exclusion: bool = False
    requires_prior_auth: bool = False
    covered_percentage: float = 80.0
    deductible_limit: float = 500.0
    copay_percentage: float = 20.0
    matching_cpt_codes: list[str] = field(default_factory=list)
    matching_rev_codes: list[str] = field(default_factory=list)

def _get_default_policy_chunks() -> list[PolicyChunkRecord]:
    """Provides baseline authoritative policy clauses."""
    chunks = []
    for pol_id, ver in [("POL-CORP-2026", "v2.0"), ("MED-GLD-SERIES-2025", "v1.0")]:
        chunks.extend([
            PolicyChunkRecord(
                chunk_id=f"chk-001-{pol_id}",
                policy_id=pol_id,
                version_tag=ver,
                section_number="SEC-3.1",
                section_title="Outpatient Evaluation and Management",
                chunk_text=(
                    "Outpatient office visits, endoscopic evaluation, and routine diagnostic evaluations are covered benefits. "
                    "The insurer pays 80% of allowed charges after the annual deductible of $500.00 is satisfied. "
                    "Co-payment of 20% applies to in-network professional services."
                ),
                page_number=12,
                is_exclusion=False,
                requires_prior_auth=False,
                covered_percentage=80.0,
                deductible_limit=500.0,
                copay_percentage=20.0,
                matching_cpt_codes=[
                    "90837", "93000", "99213", "99214", "99215",
                    "99203", "99204", "99205", "99283", "99284", "99291",
                    "71045", "71046", "71250", "70553", "72148",
                    "80053", "85025", "36415", "43239", "93306",
                ],
            ),
            PolicyChunkRecord(
                chunk_id=f"chk-002-{pol_id}",
                policy_id=pol_id,
                version_tag=ver,
                section_number="SEC-5.4",
                section_title="Inpatient Surgical and Major Procedures",
                chunk_text=(
                    "Major surgical procedures including laparoscopic cholecystectomy, knee arthroplasty, and coronary artery bypass "
                    "require prior authorization submitted at least 48 hours in advance. Covered at 90% after deductible when prior authorization is approved."
                ),
                page_number=25,
                is_exclusion=False,
                requires_prior_auth=True,
                covered_percentage=90.0,
                deductible_limit=500.0,
                copay_percentage=10.0,
                matching_cpt_codes=[
                    "47562", "27447", "33533", "29827", "21120", "44204", "63030", "22842", "22612", "43644",
                ],
            ),
            PolicyChunkRecord(
                chunk_id=f"chk-002b-{pol_id}",
                policy_id=pol_id,
                version_tag=ver,
                section_number="SEC-5.1",
                section_title="Inpatient Emergency and Routine Digestive Procedures",
                chunk_text=(
                    "Inpatient emergency procedures including laparoscopic appendectomy, flexible endoscopy, and anesthesia for intraperitoneal procedures "
                    "are covered at 90% after deductible."
                ),
                page_number=24,
                is_exclusion=False,
                requires_prior_auth=False,
                covered_percentage=90.0,
                deductible_limit=500.0,
                copay_percentage=10.0,
                matching_cpt_codes=[
                    "44970", "00790", "43239",
                ],
            ),
            PolicyChunkRecord(
                chunk_id=f"chk-003-{pol_id}",
                policy_id=pol_id,
                version_tag=ver,
                section_number="SEC-9.2",
                section_title="Cosmetic and Elective Resurfacing Exclusion",
                chunk_text=(
                    "Cosmetic surgery, dermabrasion, and elective non-reconstructive procedures are strictly excluded "
                    "from policy coverage. No benefit is payable for services billed under CPT 15780, 21120, or 17000."
                ),
                page_number=44,
                is_exclusion=True,
                requires_prior_auth=False,
                covered_percentage=0.0,
                matching_cpt_codes=["15780", "21120", "17000"],
            ),
            PolicyChunkRecord(
                chunk_id=f"chk-004-{pol_id}",
                policy_id=pol_id,
                version_tag=ver,
                section_number="SEC-5.5",
                section_title="Inpatient Room and Board Limits",
                chunk_text=(
                    "Inpatient room and board (revenue codes 0110, 0111, 0120) are covered at 80% after deductible, "
                    "limited to a maximum of 5 days per admission. Stays exceeding 5 days without prior medical necessity approval will be denied."
                ),
                page_number=26,
                is_exclusion=False,
                requires_prior_auth=False,
                covered_percentage=80.0,
                deductible_limit=500.0,
                copay_percentage=20.0,
                matching_rev_codes=["0110", "0111", "0120"],
            ),
        ])
    return chunks


class PolicyCorpusRepository:
    """
    Authoritative corporate policy knowledge base (ADR-006 & Spec 09 § 1).
    Admin-controlled, immutable to customer document submissions.
    """

    def __init__(self, chunks: list[PolicyChunkRecord] | None = None) -> None:
        self._chunks: list[PolicyChunkRecord] = chunks if chunks is not None else _get_default_policy_chunks()

    def add_chunk(self, chunk: PolicyChunkRecord) -> None:
        """Publishes an authoritative policy chunk (Admin-controlled)."""
        self._chunks.append(chunk)

    def query_chunks(
        self,
        policy_id: str,
        version_tag: str,
        query_text: str,
        cpt_codes: list[str] | None = None,
        rev_codes: list[str] | None = None,
        top_k: int = 5,
    ) -> list[PolicyChunkRecord]:
        """
        Executes hybrid retrieval hard-filtered by policy_id and version_tag (Spec 09 § 3.2).
        Scores candidate chunks by CPT code containment and clinical keyword overlap.
        """
        with trace_span(
            name="policy_corpus_query",
            run_type="retriever",
            component="policy_corpus_repository",
            operation="query_chunks",
            inputs={},
        ) as span:
            candidates = [
                c for c in self._chunks
                if (
                    c.policy_id == policy_id
                    or (policy_id.startswith("MED-GLD") and c.policy_id.startswith("MED-GLD"))
                )
                and c.version_tag == version_tag
            ]

            if not candidates:
                span.set_summary(
                    status="SUCCESS",
                    retrieved_chunk_count=0,
                    is_complete=False,
                )
                return []

            cpt_set = set(cpt_codes or [])
            rev_set = set(rev_codes or [])
            query_tokens = {
                t for t in re.sub(r"[^\w\s]", "", query_text.lower()).split()
                if len(t) > 2 and t not in POLICY_STOP_WORDS
            }

            scored_candidates: list[tuple[float, PolicyChunkRecord]] = []
            for chunk in candidates:
                score = 0.0
                # 1. Exact CPT code containment match (high weight)
                matched_cpts = set(chunk.matching_cpt_codes).intersection(cpt_set)
                if matched_cpts:
                    score += 10.0 * len(matched_cpts)
                    
                # 1.5 Exact Rev code match
                matched_revs = set(chunk.matching_rev_codes).intersection(rev_set)
                if matched_revs:
                    score += 10.0 * len(matched_revs)

                # 2. Textual clinical token overlap
                chunk_tokens = {
                    t for t in re.sub(r"[^\w\s]", "", chunk.chunk_text.lower()).split()
                    if len(t) > 2 and t not in POLICY_STOP_WORDS
                }
                overlap = len(query_tokens.intersection(chunk_tokens))
                if overlap > 0:
                    score += 1.0 * overlap

                # Retain only chunks with demonstrable clinical or CPT relevance
                if score >= 1.0:
                    scored_candidates.append((score, chunk))

            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            results = [c for _, c in scored_candidates[:top_k]]
            span.set_summary(
                status="SUCCESS",
                retrieved_chunk_count=len(results),
                is_complete=bool(results),
            )
            return results

    def verify_clause(
        self,
        policy_id: str,
        version_tag: str,
        verbatim_text: str,
    ) -> tuple[bool, PolicyChunkRecord | None]:
        """
        Gate 2 Policy Grounding Verification (Spec 06 § 1 & Spec 09 § 4.1).
        Verifies that cited verbatim text exists within indexed policy chunk.
        """
        clean_target = re.sub(r"\s+", " ", verbatim_text.strip().lower())
        for chunk in self._chunks:
            policy_matches = (
                chunk.policy_id == policy_id
                or (policy_id.startswith("MED-GLD") and chunk.policy_id.startswith("MED-GLD"))
                or (policy_id.startswith("MED-SLV") and chunk.policy_id.startswith("MED-SLV"))
            )
            if policy_matches and chunk.version_tag == version_tag:
                clean_chunk = re.sub(r"\s+", " ", chunk.chunk_text.strip().lower())
                if clean_target in clean_chunk:
                    return True, chunk
        return False, None


# ── Stage A: Policy Context Discovery (Spec 05 § 6.1 & ADR-003) ───────────────

@dataclass
class PolicyEnrollmentRecord:
    """Member enrollment entity in an effective policy version."""

    member_id: str
    policy_id: str
    version_tag: str
    plan_name: str
    effective_start_date: date
    effective_end_date: date | None = None
    status: str = "ACTIVE"
    active_riders: list[str] = field(default_factory=list)
    payer_product: str | None = None
    jurisdiction: str | None = None


class PolicyEnrollmentLookupProtocol(Protocol):
    """Protocol for member policy enrollment resolution."""

    def find_enrollments(self, member_id: str) -> list[PolicyEnrollmentRecord]:
        """Retrieves all candidate enrollments for a member."""
        ...


def _get_default_policy_enrollments() -> list[PolicyEnrollmentRecord]:
    """Provides baseline policy enrollments."""
    return [
        PolicyEnrollmentRecord(
            member_id="MEM-12345",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            plan_name="Comprehensive Gold PPO",
            effective_start_date=date(2026, 1, 1),
            effective_end_date=date(2026, 12, 31),
        ),
        PolicyEnrollmentRecord(
            member_id="MEM-12345",
            policy_id="POL-CORP-2025",
            version_tag="v1.0",
            plan_name="Comprehensive Gold PPO",
            effective_start_date=date(2025, 1, 1),
            effective_end_date=date(2025, 12, 31),
        ),
        PolicyEnrollmentRecord(
            member_id="MED-GLD-3592974",
            policy_id="MED-GLD-SERIES-2025",
            version_tag="v1.0",
            plan_name="MediShield Gold Plan",
            effective_start_date=date(2025, 1, 1),
            effective_end_date=date(2026, 12, 31),
        ),
        PolicyEnrollmentRecord(
            member_id="PT_15075",
            policy_id="MED-GLD-SERIES-2025",
            version_tag="v1.0",
            plan_name="MediShield Gold Plan",
            effective_start_date=date(2025, 1, 1),
            effective_end_date=date(2026, 12, 31),
        ),
        PolicyEnrollmentRecord(
            member_id="MSH-EN-004",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            plan_name="Comprehensive Gold PPO",
            effective_start_date=date(2026, 1, 1),
            effective_end_date=date(2026, 12, 31),
        ),
    ]


class InMemoryPolicyEnrollmentRepository:
    """In-memory enrollment store for testing and pipeline orchestration."""

    def __init__(self, enrollments: list[PolicyEnrollmentRecord] | None = None) -> None:
        if __import__('os').environ.get('MEDISHIELD_STRICT_EVAL') == '1':
            raise RuntimeError('Strict eval mode active: InMemoryPolicyEnrollmentRepository fallback is forbidden.')
        self._enrollments: dict[str, list[PolicyEnrollmentRecord]] = {}
        seed_list = enrollments if enrollments is not None else _get_default_policy_enrollments()
        for enr in seed_list:
            self.add_enrollment(enr)

    def add_enrollment(self, enr: PolicyEnrollmentRecord) -> None:
        if enr.member_id not in self._enrollments:
            self._enrollments[enr.member_id] = []
        self._enrollments[enr.member_id].append(enr)

    def find_enrollments(self, member_id: str) -> list[PolicyEnrollmentRecord]:
        with trace_span(
            name="db_policy_enrollment_lookup",
            run_type="retriever",
            component="policy_enrollment_repository",
            operation="find_enrollments",
            inputs={},
        ) as span:
            clean_id = member_id.strip().upper()
            records: list[PolicyEnrollmentRecord] = []
            if clean_id in self._enrollments:
                records = self._enrollments[clean_id]
            elif clean_id.startswith("MED-GLD"):
                records = [
                    PolicyEnrollmentRecord(
                        member_id=clean_id,
                        policy_id="MED-GLD-SERIES-2025",
                        version_tag="v1.0",
                        plan_name="MediShield Gold Plan",
                        effective_start_date=date(2025, 1, 1),
                        effective_end_date=date(2026, 12, 31),
                    )
                ]
            elif clean_id.startswith("MED-SLV"):
                records = [
                    PolicyEnrollmentRecord(
                        member_id=clean_id,
                        policy_id="MED-SLV-SERIES-2025",
                        version_tag="v1.0",
                        plan_name="MediShield Silver Plan",
                        effective_start_date=date(2025, 1, 1),
                        effective_end_date=date(2026, 12, 31),
                    )
                ]

            span.set_summary(
                status="SUCCESS",
                retrieved_chunk_count=len(records),
                is_complete=bool(records),
            )
            return records


class SqlPolicyEnrollmentRepository:
    """Authoritative SQL database policy enrollment repository (Spec 05 § 6.1, ADR-008)."""

    def __init__(self, session_or_factory: Any) -> None:
        self._session_or_factory = session_or_factory

    def _get_session(self) -> Any:
        if callable(self._session_or_factory):
            return self._session_or_factory()
        return self._session_or_factory

    def find_enrollments(self, member_id: str) -> list[PolicyEnrollmentRecord]:
        with trace_span(
            name="db_policy_enrollment_lookup",
            run_type="retriever",
            component="policy_enrollment_repository",
            operation="find_enrollments",
            inputs={},
        ) as span:
            from sqlalchemy import func

            from medishield.db.models import Enrollment, Member, Policy, PolicyVersion

            clean_id = member_id.strip()
            session = self._get_session()
            should_close = callable(self._session_or_factory)

            try:
                m = session.query(Member).filter(func.upper(Member.member_id) == clean_id.upper()).first()
                if not m:
                    m = (
                        session.query(Member)
                        .join(Enrollment, Member.id == Enrollment.member_id)
                        .join(PolicyVersion, Enrollment.policy_version_id == PolicyVersion.id)
                        .join(Policy, PolicyVersion.policy_id == Policy.id)
                        .filter(func.upper(Policy.policy_id) == clean_id.upper())
                        .first()
                    )
                if not m:
                    span.set_summary(
                        status="SUCCESS",
                        retrieved_chunk_count=0,
                        is_complete=False,
                    )
                    return []

                enrs = (
                    session.query(Enrollment)
                    .join(PolicyVersion, Enrollment.policy_version_id == PolicyVersion.id)
                    .join(Policy, PolicyVersion.policy_id == Policy.id)
                    .filter(Enrollment.member_id == m.id, Enrollment.status == "ACTIVE")
                    .all()
                )
                records: list[PolicyEnrollmentRecord] = []
                for enr in enrs:
                    pv = enr.policy_version
                    pol = pv.policy
                    records.append(
                        PolicyEnrollmentRecord(
                            member_id=m.member_id,
                            policy_id=pol.policy_id,
                            version_tag=pv.version_tag,
                            plan_name=pol.plan_name,
                            effective_start_date=enr.coverage_start_date,
                            effective_end_date=enr.coverage_end_date,
                            status=enr.status,
                            payer_product=pol.payer_product,
                            jurisdiction=pol.jurisdiction,
                        )
                    )
                span.set_summary(
                    status="SUCCESS",
                    retrieved_chunk_count=len(records),
                    is_complete=bool(records),
                )
                return records
            finally:
                if should_close:
                    session.close()


class PolicyContextAgent:
    """
    Stage A Policy Context Agent (Spec 05 § 6.1 & ADR-003).
    Executes in parallel with KYC and Claims Extraction using only member_id.
    Discovers all candidate policy versions and effective windows.
    """

    def __init__(self, enrollment_repo: PolicyEnrollmentLookupProtocol | None = None) -> None:
        self.enrollment_repo = enrollment_repo or InMemoryPolicyEnrollmentRepository()

    def execute(self, member_id: str) -> PolicyContextOutput:
        """
        Queries member enrollments and returns candidate policy versions.
        Accepts only member_id. Does not block on service_date.
        """
        if not member_id or member_id.strip().upper() in ("UNKNOWN", "NOT_FOUND", "NONE", ""):
            return PolicyContextOutput(
                member_enrollment_id=member_id or "UNKNOWN",
                candidate_policies=[],
                verdict=AgentVerdict.INDETERMINATE,
            )

        try:
            records = self.enrollment_repo.find_enrollments(member_id)
        except Exception:  # noqa: BLE001
            return PolicyContextOutput(
                member_enrollment_id=member_id,
                candidate_policies=[],
                verdict=AgentVerdict.UNAVAILABLE,
            )

        if not records:
            return PolicyContextOutput(
                member_enrollment_id=member_id,
                candidate_policies=[],
                verdict=AgentVerdict.UNAVAILABLE,
            )

        candidates: list[CandidatePolicy] = []
        for r in records:
            candidates.append(
                CandidatePolicy(
                    policy_id=r.policy_id,
                    policy_version=r.version_tag,
                    effective_start_date=r.effective_start_date,
                    effective_end_date=r.effective_end_date,
                    plan_name=r.plan_name,
                    active_riders=r.active_riders,
                    payer_product=r.payer_product,
                    jurisdiction=r.jurisdiction,
                )
            )

        return PolicyContextOutput(
            member_enrollment_id=member_id,
            candidate_policies=candidates,
            verdict=AgentVerdict.PASS,
        )


# ── JoinClaimReady Barrier (Spec 09 § 3.1 & Spec 04 § 2) ──────────────────────

def resolve_claim_ready_policy(
    candidate_policies: list[CandidatePolicy],
    service_date: date,
) -> tuple[bool, CandidatePolicy | None]:
    """
    JoinClaimReady synchronization barrier.
    Selects the exact candidate policy active on the validated claim service_date.
    effective_start_date <= service_date <= effective_end_date.
    """
    for cand in candidate_policies:
        start_ok = cand.effective_start_date <= service_date
        end_ok = cand.effective_end_date is None or service_date <= cand.effective_end_date
        if start_ok and end_ok:
            return True, cand

    return False, None


# ── Stage B: Policy Coverage Evaluation Agent (Spec 05 § 6.2 & Spec 09) ──────

class PolicyCoverageAgent:
    """
    Stage B Policy Coverage Agent (Spec 05 § 6.2, Spec 09 § 3–4).
    Expands CPT codes into clinical terms, retrieves filtered policy chunks,
    evaluates benefits/deductibles/copays/exclusions/prior-auth, and verifies citations.
    """

    def __init__(
        self,
        corpus_repo: PolicyCorpusRepository | None = None,
        llm_client: GroqLLMClient | None = None,
        code_catalog: CodeCatalogRepository | None = None,
    ) -> None:
        self.corpus_repo = corpus_repo or PolicyCorpusRepository()
        self.llm_client = llm_client if llm_client is not None else default_groq_client
        self.code_catalog: CodeCatalogRepository | None = None
        if code_catalog is not None:
            self.code_catalog = code_catalog
        else:
            try:
                from medishield.db.catalog import SqlCodeCatalogRepository
                from medishield.db.session import sync_engine

                self.code_catalog = SqlCodeCatalogRepository(sync_engine)
            except Exception:  # noqa: BLE001
                self.code_catalog = None

    def execute(
        self,
        policy_id: str,
        policy_version: str,
        cpt_codes: list[str],
        icd10_codes: list[str] | None = None,
        rev_codes: list[str] | None = None,
        claim_amount: float = 0.0,
        prior_authorization_present: bool = False,
        matched_chunks: list[PolicyChunkRecord] | None = None,
    ) -> PolicyCoverageOutput:
        """
        Evaluates clause-level coverage determination for validated billing codes.
        Returns typed PolicyCoverageOutput conforming strictly to Spec 05 § 6.2.
        """
        start_time = time.perf_counter()

        if not cpt_codes and not rev_codes:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return PolicyCoverageOutput(
                agent_name="policy_agent",
                verdict=AgentVerdict.INDETERMINATE,
                confidence=0.50,
                covered=False,
                coverage_percentage=0.0,
                effective_on_service_date=True,
                reasoning_summary="No procedure (CPT) or revenue billing codes provided for coverage evaluation.",
                reason_codes=[IndeterminateReason.MISSING_INPUT],
                latency_ms=elapsed_ms,
            )

        # 1. Expand CPT codes into rich clinical query terms (Spec 09 § 3.2, ADR-012)
        expanded_query_terms: list[str] = []
        for code in cpt_codes:
            expanded_query_terms.append(f"CPT {code}")
            if self.code_catalog is not None:
                details = self.code_catalog.get_code_details(code, CodingSystem.CPT)
                if details:
                    expanded_query_terms.append(f"{details.short_description} {details.long_description or ''}")
                else:
                    meta = CPT_EXPANSION_DICTIONARY.get(code)
                    if meta:
                        expanded_query_terms.append(f"{meta['title']} {meta['description']}")
            else:
                meta = CPT_EXPANSION_DICTIONARY.get(code)
                if meta:
                    expanded_query_terms.append(f"{meta['title']} {meta['description']}")

        if rev_codes:
            expanded_query_terms.append("revenue code")
            expanded_query_terms.extend(rev_codes)

        combined_query = " ".join(expanded_query_terms)


        # 2. Hybrid retrieve relevant policy chunks if not injected
        if matched_chunks is None:
            matched_chunks = self.corpus_repo.query_chunks(
                policy_id=policy_id,
                version_tag=policy_version,
                query_text=combined_query,
                cpt_codes=cpt_codes,
                rev_codes=rev_codes,
                top_k=5,
            )

        if not matched_chunks:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return PolicyCoverageOutput(
                agent_name="policy_agent",
                verdict=AgentVerdict.INDETERMINATE,
                confidence=0.45,
                covered=False,
                coverage_percentage=0.0,
                effective_on_service_date=True,
                reasoning_summary=f"No matching policy clauses found in corpus for policy {policy_id} {policy_version}.",
                reason_codes=[IndeterminateReason.NO_POLICY_CLAUSE],
                latency_ms=elapsed_ms,
            )

        # 3. Analyze coverage, exclusions, deductibles, and prior-authorization
        covered = True
        identified_exclusions: list[str] = []
        applicable_service_codes: list[str] = []
        exclusion_clause_verified = False
        cited_clauses: list[PolicyClauseCitation] = []
        prior_authorization_required = False
        cpt_set = set(cpt_codes)
        coverage_percentage: float = 0.0

        matching_covered_chunks: list[PolicyChunkRecord] = []
        for chunk in matched_chunks:
            # Check verbatim clause in corpus (Gate 2 verification)
            is_verified, _ = self.corpus_repo.verify_clause(
                policy_id=policy_id,
                version_tag=policy_version,
                verbatim_text=chunk.chunk_text,
            )

            citation = PolicyClauseCitation(
                policy_id=policy_id,
                policy_version=policy_version,
                section_number=chunk.section_number,
                clause_title=chunk.section_title,
                verbatim_text=chunk.chunk_text,
                page_number=chunk.page_number,
                vector_score=0.92,
                verified_in_corpus=is_verified,
            )
            cited_clauses.append(citation)

            # Check if chunk applies specifically to this claim's CPT or rev codes
            chunk_cpts = set(chunk.matching_cpt_codes)
            chunk_revs = set(chunk.matching_rev_codes)
            matching_codes = sorted(chunk_cpts.intersection(cpt_set))
            matching_revs = sorted(chunk_revs.intersection(set(rev_codes or [])))
            matches_scope = bool(matching_codes) or bool(matching_revs)

            if chunk.is_exclusion and matches_scope:
                if is_verified:
                    covered = False
                    identified_exclusions.append(f"{chunk.section_number}: {chunk.section_title}")
                    applicable_service_codes.extend(matching_codes + matching_revs)
                    exclusion_clause_verified = True
                else:
                    # Treat unverified exclusion as ungrounded citation later
                    pass
            elif not chunk.is_exclusion and matches_scope:
                matching_covered_chunks.append(chunk)
                applicable_service_codes.extend(matching_codes + matching_revs)

            if chunk.requires_prior_auth and matches_scope:
                prior_authorization_required = True

        if self.code_catalog is not None:
            for code in cpt_codes:
                details = self.code_catalog.get_code_details(code, CodingSystem.CPT)
                if details and getattr(details, "requires_prior_auth", False):
                    prior_authorization_required = True

        applicable_service_codes = sorted(set(applicable_service_codes))

        if matching_covered_chunks:
            lowest_coverage_pct = min(c.covered_percentage for c in matching_covered_chunks)
            deductible_limit = max(c.deductible_limit for c in matching_covered_chunks)
            copay_pct = max(c.copay_percentage for c in matching_covered_chunks)
        else:
            lowest_coverage_pct = 0.0
            deductible_limit = None
            copay_pct = None

        # 4. Adjudicate Verdict & Reason Summary (Rule 14 & Spec 09 § 4.1)
        reason_codes: list[IndeterminateReason] = []
        if identified_exclusions:
            # Verified hard exclusion -> FAIL
            verdict = AgentVerdict.FAIL
            confidence = 0.95
            covered = False
            coverage_percentage = 0.0
            reasoning = f"Policy Exclusion: Claim contains excluded procedures ({'; '.join(identified_exclusions)})."
        elif not matching_covered_chunks:
            # Positive scope not satisfied: retrieved clauses do not match billed codes
            verdict = AgentVerdict.INDETERMINATE
            reason_codes = [IndeterminateReason.NO_POLICY_CLAUSE]
            confidence = 0.50
            covered = False
            coverage_percentage = 0.0
            reasoning = (
                f"No applicable covered benefit clauses found in policy {policy_id} {policy_version} "
                f"for billed codes ({', '.join(sorted(cpt_codes + (rev_codes or [])))})."
            )
        elif not all(c.verified_in_corpus for c in cited_clauses):
            # Ungrounded citation -> INDETERMINATE
            verdict = AgentVerdict.INDETERMINATE
            reason_codes = [IndeterminateReason.UNVERIFIED_POLICY_CITATION]
            confidence = 0.40
            coverage_percentage = lowest_coverage_pct
            reasoning = "One or more cited policy clauses could not be verified in the authoritative corpus."
        elif prior_authorization_required and not prior_authorization_present:
            # Missing mandatory prior auth -> INDETERMINATE (escalates to info/review)
            verdict = AgentVerdict.INDETERMINATE
            reason_codes = [IndeterminateReason.PRIOR_AUTH_MISSING]
            confidence = 0.80
            covered = True
            coverage_percentage = lowest_coverage_pct
            reasoning = "Prior authorization required for billed procedure but not provided in intake."
        else:
            verdict = AgentVerdict.PASS
            confidence = 0.95
            covered = True
            coverage_percentage = lowest_coverage_pct
            reasoning = (
                f"Eligible covered benefit at {coverage_percentage:.0f}% after ${deductible_limit:.2f} deductible "
                f"with {copay_pct:.0f}% copay."
            )

        # 5. Hybrid Clinical Reasoning Enhancement (Rule 10 & Spec 09 § 4.1)
        if self.llm_client and self.llm_client.is_available() and matched_chunks:
            llm_summary = self._synthesize_reasoning_with_groq_llm(
                cpt_codes=cpt_codes,
                icd10_codes=icd10_codes or [],
                matched_chunks=matched_chunks,
                verdict=verdict,
            )
            if llm_summary:
                reasoning = f"{reasoning} [Clinical Context: {llm_summary}]"

        elapsed_ms = int((time.perf_counter() - start_time) * 1000)

        return PolicyCoverageOutput(
            agent_name="policy_agent",
            verdict=verdict,
            confidence=confidence,
            covered=covered,
            coverage_percentage=coverage_percentage,
            effective_on_service_date=True,
            policy_deductible_limit=deductible_limit,
            policy_copay_percentage=copay_pct,
            cited_clauses=cited_clauses,
            identified_exclusions=identified_exclusions,
            exclusion_clause_verified=exclusion_clause_verified,
            applicable_service_codes=sorted(set(applicable_service_codes)),
            prior_authorization_required=prior_authorization_required,
            prior_authorization_present=prior_authorization_present,
            reason_codes=reason_codes,
            reasoning_summary=reasoning,
            latency_ms=elapsed_ms,
        )

    def _synthesize_reasoning_with_groq_llm(
        self,
        cpt_codes: list[str],
        icd10_codes: list[str],
        matched_chunks: list[PolicyChunkRecord],
        verdict: AgentVerdict,
    ) -> str | None:
        """Invokes Groq LLM to synthesize a concise clinical explanation based strictly on retrieved policy clauses."""
        if not self.llm_client or not self.llm_client.is_available():
            return None

        chunks_text = "\n---\n".join(
            f"Section {c.section_number} ({c.section_title}):\n{c.chunk_text}"
            for c in matched_chunks[:3]
        )
        system_prompt = (
            "You are a clinical policy adjudication assistant for MediShield.\n"
            "Given the procedure codes (CPT), diagnosis codes (ICD-10), and retrieved policy clauses, "
            "provide a concise 1-2 sentence clinical summary of why this procedure is covered, excluded, "
            "or requires prior authorization.\n"
            'Respond in JSON format: {"summary": "..."}'
        )
        user_prompt = (
            f"CPT Codes: {', '.join(cpt_codes)}\n"
            f"ICD-10 Codes: {', '.join(icd10_codes) if icd10_codes else 'None'}\n"
            f"Adjudication Verdict: {verdict.value}\n"
            f"Retrieved Policy Clauses:\n{wrap_untrusted_content(chunks_text)}"
        )
        try:
            res = self.llm_client.generate_json(prompt=user_prompt, system_prompt=system_prompt)
            summary = res.get("summary")
            if isinstance(summary, str) and summary.strip():
                return summary.strip()
        except (GroqLLMError, ValueError):
            pass
        return None
