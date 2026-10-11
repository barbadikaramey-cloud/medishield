"""Authoritative Dataset & Master Entity Seeder for SQLite & PostgreSQL (ADR-008).

Seeds master Member records, Policies, Policy Versions, Enrollments, and Policy Chunks
for all 30 benchmark patient clusters (including PT_16658 / Cluster C_012).
"""

import logging
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from medishield.db.models import (
    Enrollment,
    Member,
    Policy,
    PolicyChunk,
    PolicyVersion,
)
from medishield.db.session import get_sqlite_session_factory

logger = logging.getLogger(__name__)

# Baseline default test members (preserving full backwards compatibility with unit tests)
BASELINE_MEMBERS: list[dict[str, Any]] = [
    {
        "member_id": "MEM-12345",
        "first_name": "Alice",
        "last_name": "Smith",
        "dob": "05/20/1985",
        "ssn_last4": "4321",
        "policy_id": "POL-CORP-2026",
    },
    {
        "member_id": "MED-GLD-3592974",
        "first_name": "John",
        "last_name": "Johnson",
        "dob": "11/08/1958",
        "ssn_last4": "7658",
        "policy_id": "MED-GLD-3592974",
    },
    {
        "member_id": "PT_15075",
        "first_name": "John",
        "last_name": "Johnson",
        "dob": "11/08/1958",
        "ssn_last4": "7658",
        "policy_id": "MED-GLD-3592974",
    },
]


def _load_clusters_from_generator() -> list[dict[str, Any]]:
    """Loads 30 benchmark patient clusters from canonical scripts.scripts.generate_docs."""
    try:
        import sys
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        scripts_dir = repo_root / "scripts" / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

        import random

        import generate_docs  # type: ignore[import-not-found]

        generate_docs.rng = random.Random(42)
        clusters, _ = generate_docs.generate_patient_clusters(30)
        return list(clusters)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not dynamically import generate_patient_clusters: %s; using static fallback", exc)
        return []


# Fallback static definition for all 30 benchmark clusters if Pillow/generator unavailable
FALLBACK_CLUSTERS: list[dict[str, Any]] = [
    {"cluster_id": "C_001", "patient_id": "PT_19116", "name": "Christopher Harris I.", "dob": "04/25/1954", "ssn": "489-22-6881", "policy": "MED-GLD-6770619"},
    {"cluster_id": "C_002", "patient_id": "PT_99733", "name": "Jessica Jackson Y.", "dob": "02/28/1963", "ssn": "384-68-6977", "policy": "MED-GLD-3728882"},
    {"cluster_id": "C_003", "patient_id": "PT_62350", "name": "Patricia Miller H.", "dob": "01/26/1997", "ssn": "790-95-5858", "policy": "MED-GLD-4567281"},
    {"cluster_id": "C_004", "patient_id": "PT_69470", "name": "Elizabeth Taylor Q.", "dob": "02/25/1976", "ssn": "118-24-9694", "policy": "MED-GLD-2065818"},
    {"cluster_id": "C_005", "patient_id": "PT_20322", "name": "Sarah White Q.", "dob": "03/17/1993", "ssn": "173-67-2708", "policy": "MED-GLD-3564217"},
    {"cluster_id": "C_006", "patient_id": "PT_39451", "name": "Sandra Hall C.", "dob": "09/25/1993", "ssn": "480-14-1629", "policy": "MED-GLD-5446912"},
    {"cluster_id": "C_007", "patient_id": "PT_71993", "name": "Mary Davis S.", "dob": "04/19/1980", "ssn": "235-98-3567", "policy": "MED-GLD-4841005"},
    {"cluster_id": "C_008", "patient_id": "PT_82132", "name": "Richard Martinez D.", "dob": "11/14/1951", "ssn": "887-16-6466", "policy": "MED-GLD-2016911"},
    {"cluster_id": "C_009", "patient_id": "PT_17665", "name": "Mary Davis C.", "dob": "04/06/1993", "ssn": "732-23-4245", "policy": "MED-GLD-3762152"},
    {"cluster_id": "C_010", "patient_id": "PT_20745", "name": "John Johnson X.", "dob": "01/02/1965", "ssn": "214-72-8706", "policy": "MED-GLD-1954280"},
    {"cluster_id": "C_011", "patient_id": "PT_47353", "name": "Nancy Martin I.", "dob": "11/23/1958", "ssn": "339-50-8438", "policy": "MED-GLD-8670955"},
    {"cluster_id": "C_012", "patient_id": "PT_16658", "name": "Karen Harris T.", "dob": "11/17/1996", "ssn": "779-23-3200", "policy": "MED-GLD-5437001"},
    {"cluster_id": "C_013", "patient_id": "PT_57795", "name": "Betty Clark I.", "dob": "01/11/1947", "ssn": "368-30-8239", "policy": "MED-GLD-8175395"},
    {"cluster_id": "C_014", "patient_id": "PT_15075", "name": "John Johnson L.", "dob": "11/08/1958", "ssn": "153-27-7658", "policy": "MED-GLD-3592974"},
    {"cluster_id": "C_015", "patient_id": "PT_24208", "name": "Joseph Anderson G.", "dob": "08/12/1997", "ssn": "136-16-8692", "policy": "MED-GLD-7685149"},
    {"cluster_id": "C_016", "patient_id": "PT_50538", "name": "Daniel Taylor X.", "dob": "06/14/1995", "ssn": "802-99-9730", "policy": "MED-GLD-1744682"},
    {"cluster_id": "C_017", "patient_id": "PT_72021", "name": "Christopher Harris M.", "dob": "05/18/1989", "ssn": "917-80-7798", "policy": "MED-GLD-6049101"},
    {"cluster_id": "C_018", "patient_id": "PT_62383", "name": "David Garcia V.", "dob": "05/17/1950", "ssn": "165-27-2481", "policy": "MED-GLD-4768844"},
    {"cluster_id": "C_019", "patient_id": "PT_81810", "name": "Mary Davis Y.", "dob": "02/25/2000", "ssn": "875-98-3806", "policy": "MED-GLD-8793853"},
    {"cluster_id": "C_020", "patient_id": "PT_30028", "name": "Barbara Anderson X.", "dob": "08/15/2000", "ssn": "219-48-9366", "policy": "MED-GLD-5013879"},
    {"cluster_id": "C_021", "patient_id": "PT_67125", "name": "Robert Williams N.", "dob": "06/18/1971", "ssn": "161-55-9345", "policy": "MED-GLD-1327691"},
    {"cluster_id": "C_022", "patient_id": "PT_54236", "name": "Margaret Lee K.", "dob": "11/26/1987", "ssn": "173-16-8290", "policy": "MED-GLD-1452421"},
    {"cluster_id": "C_023", "patient_id": "PT_24993", "name": "Anthony Moore I.", "dob": "07/09/1993", "ssn": "798-50-6819", "policy": "MED-GLD-6871360"},
    {"cluster_id": "C_024", "patient_id": "PT_99687", "name": "Richard Martinez O.", "dob": "05/25/1989", "ssn": "578-83-5002", "policy": "MED-GLD-6218367"},
    {"cluster_id": "C_025", "patient_id": "PT_45400", "name": "Michael Brown Z.", "dob": "01/12/1981", "ssn": "761-94-6886", "policy": "MED-GLD-6724909"},
    {"cluster_id": "C_026", "patient_id": "PT_88139", "name": "Paul Walker O.", "dob": "12/19/1972", "ssn": "208-86-6398", "policy": "MED-GLD-8563202"},
    {"cluster_id": "C_027", "patient_id": "PT_55056", "name": "Thomas Jackson R.", "dob": "09/07/1945", "ssn": "168-96-8208", "policy": "MED-GLD-8987516"},
    {"cluster_id": "C_028", "patient_id": "PT_39816", "name": "Nancy Martin L.", "dob": "08/09/1989", "ssn": "726-57-5507", "policy": "MED-GLD-3005795"},
    {"cluster_id": "C_029", "patient_id": "PT_74454", "name": "Karen Harris A.", "dob": "09/05/1990", "ssn": "439-86-7788", "policy": "MED-GLD-9235577"},
    {"cluster_id": "C_030", "patient_id": "PT_41439", "name": "Patricia Miller E.", "dob": "10/10/1954", "ssn": "394-42-5369", "policy": "MED-GLD-9767312"},
]


def _parse_dob(dob_str: str) -> date:
    """Parses mm/dd/yyyy format to date object."""
    try:
        parts = dob_str.split("/")
        if len(parts) == 3:
            return date(int(parts[2]), int(parts[0]), int(parts[1]))
        return date(1980, 1, 1)
    except (ValueError, IndexError):
        return date(1980, 1, 1)


def seed_benchmark_dataset(session: Session) -> int:
    """
    Seeds all 30 benchmark patients, policies, enrollments, and clauses into the database.
    Returns the number of seeded members. Idempotent: checks for existence before adding.
    """
    seeded_count = 0
    seen_member_ids: set[str] = {
        row[0].upper() for row in session.query(Member.member_id).all()
    }
    seen_policy_ids: set[str] = {
        row[0].upper() for row in session.query(Policy.policy_id).all()
    }
    policy_version_map: dict[str, uuid.UUID] = {}
    for pv in session.query(PolicyVersion).all():
        if pv.policy:
            policy_version_map[pv.policy.policy_id.upper()] = pv.id

    # 1. Load dynamic or fallback clusters
    clusters = _load_clusters_from_generator()
    if not clusters:
        clusters = FALLBACK_CLUSTERS

    # 2. Seed Baseline Members
    for b in BASELINE_MEMBERS:
        m_id_str = b["member_id"].upper()
        if m_id_str in seen_member_ids:
            continue
        seen_member_ids.add(m_id_str)

        m_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"member_{b['member_id']}")
        m = Member(
            id=m_id,
            member_id=b["member_id"],
            first_name=b["first_name"],
            last_name=b["last_name"],
            date_of_birth=_parse_dob(b["dob"]),
            ssn_last4=b["ssn_last4"],
            status="ACTIVE",
        )
        session.add(m)
        seeded_count += 1

        # Seed policy if needed
        pol_id_str = b["policy_id"]
        pol_clean = pol_id_str.upper()
        if pol_clean not in seen_policy_ids:
            seen_policy_ids.add(pol_clean)
            pol_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"policy_{pol_id_str}")
            pol = Policy(
                id=pol_uuid,
                policy_id=pol_id_str,
                plan_name="Corporate Master Health Plan",
                policy_type="COMPREHENSIVE",
                is_active=True,
            )
            session.add(pol)

            pv_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"pv_{pol_id_str}_v1.0")
            pv = PolicyVersion(
                id=pv_uuid,
                policy_id=pol_uuid,
                version_tag="v1.0",
                effective_start_date=date(2025, 1, 1),
                effective_end_date=date(2026, 12, 31),
                source_pdf_key=f"policies/{pol_id_str}.pdf",
                vector_collection_name=f"policy_{pol_id_str.lower().replace('-', '_')}",
                is_published=True,
            )
            session.add(pv)
            policy_version_map[pol_clean] = pv_uuid

            # Link enrollment
            enr_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"enr_{b['member_id']}_{pol_id_str}")
            enr = Enrollment(
                id=enr_uuid,
                member_id=m_id,
                policy_version_id=pv_uuid,
                coverage_start_date=date(2025, 1, 1),
                coverage_end_date=date(2026, 12, 31),
                status="ACTIVE",
            )
            session.add(enr)
        else:
            pv_uuid = policy_version_map.get(pol_clean)
            if pv_uuid:
                enr_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"enr_{b['member_id']}_{pol_id_str}")
                enr = Enrollment(
                    id=enr_uuid,
                    member_id=m_id,
                    policy_version_id=pv_uuid,
                    coverage_start_date=date(2025, 1, 1),
                    coverage_end_date=date(2026, 12, 31),
                    status="ACTIVE",
                )
                session.add(enr)

    # 3. Seed 30 Benchmark Patient Clusters
    for c in clusters:
        patient_id = c["patient_id"]
        pid_clean = patient_id.upper()
        if pid_clean in seen_member_ids:
            continue
        seen_member_ids.add(pid_clean)

        raw_name = c["name"].strip()
        name_parts = raw_name.split()
        first_name = name_parts[0] if name_parts else "Patient"
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else "Unknown"

        ssn_raw = c.get("ssn", "000-00-0000")
        ssn_last4 = ssn_raw.split("-")[-1] if "-" in ssn_raw else ssn_raw[-4:]

        m_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"member_{patient_id}")
        member = Member(
            id=m_uuid,
            member_id=patient_id,
            first_name=first_name,
            last_name=last_name,
            date_of_birth=_parse_dob(c["dob"]),
            ssn_last4=ssn_last4,
            status="ACTIVE",
        )
        session.add(member)
        seeded_count += 1

        # Seed Policy for patient
        policy_code = c["policy"]
        pol_clean = policy_code.upper()
        if pol_clean not in seen_policy_ids:
            seen_policy_ids.add(pol_clean)
            pol_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"policy_{policy_code}")
            pol = Policy(
                id=pol_uuid,
                policy_id=policy_code,
                plan_name="Gold Comprehensive Healthcare Plan",
                policy_type="COMPREHENSIVE",
                is_active=True,
            )
            session.add(pol)

            pv_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"pv_{policy_code}_v1.0")
            pv = PolicyVersion(
                id=pv_uuid,
                policy_id=pol_uuid,
                version_tag="v1.0",
                effective_start_date=date(2025, 1, 1),
                effective_end_date=date(2026, 12, 31),
                source_pdf_key=f"policies/{policy_code}.pdf",
                vector_collection_name=f"policy_{policy_code.lower().replace('-', '_')}",
                is_published=True,
            )
            session.add(pv)

            # Seed standard Gold series policy chunks for this policy version
            sec31_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"chk_sec31_{policy_code}")
            sec31 = PolicyChunk(
                id=sec31_uuid,
                policy_version_id=pv_uuid,
                chunk_index=0,
                section_number="SEC-3.1",
                section_title="Outpatient Evaluation and Management",
                chunk_text=(
                    "Outpatient office visits, endoscopic evaluation, and routine diagnostic evaluations are covered benefits. "
                    "The insurer pays 80% of allowed charges after the annual deductible of $500.00 is satisfied. "
                    "Co-payment of 20% applies to in-network professional services."
                ),
                page_number=12,
                vector_id=f"vec_sec31_{policy_code}",
            )
            session.add(sec31)

            sec54_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"chk_sec54_{policy_code}")
            sec54 = PolicyChunk(
                id=sec54_uuid,
                policy_version_id=pv_uuid,
                chunk_index=1,
                section_number="SEC-5.4",
                section_title="Inpatient Surgical and Major Procedures",
                chunk_text=(
                    "Major surgical procedures including laparoscopic cholecystectomy, knee arthroplasty, and esophagogastroduodenoscopy "
                    "require prior authorization submitted at least 48 hours in advance. Covered at 90% after deductible when prior authorization is approved."
                ),
                page_number=25,
                vector_id=f"vec_sec54_{policy_code}",
            )
            session.add(sec54)

            sec92_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"chk_sec92_{policy_code}")
            sec92 = PolicyChunk(
                id=sec92_uuid,
                policy_version_id=pv_uuid,
                chunk_index=2,
                section_number="SEC-9.2",
                section_title="Cosmetic and Elective Resurfacing Exclusion",
                chunk_text=(
                    "Cosmetic surgery, dermabrasion, and elective non-reconstructive procedures are strictly excluded "
                    "from policy coverage. No benefit is payable for services billed under CPT 15780, 21120, or 17000."
                ),
                page_number=44,
                vector_id=f"vec_sec92_{policy_code}",
            )
            session.add(sec92)

            policy_version_map[pol_clean] = pv_uuid

            enr_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"enr_{patient_id}_{policy_code}")
            enr = Enrollment(
                id=enr_uuid,
                member_id=m_uuid,
                policy_version_id=pv_uuid,
                coverage_start_date=date(2025, 1, 1),
                coverage_end_date=date(2026, 12, 31),
                status="ACTIVE",
            )
            session.add(enr)
        else:
            pv_uuid = policy_version_map.get(pol_clean)
            if pv_uuid:
                enr_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"enr_{patient_id}_{policy_code}")
                enr = Enrollment(
                    id=enr_uuid,
                    member_id=m_uuid,
                    policy_version_id=pv_uuid,
                    coverage_start_date=date(2025, 1, 1),
                    coverage_end_date=date(2026, 12, 31),
                    status="ACTIVE",
                )
                session.add(enr)

    session.commit()
    logger.info("Successfully seeded %d benchmark members into database", seeded_count)
    return seeded_count


def seed_demo_cases_if_empty(session: Session) -> int:
    """Seeds initial demonstration cases into SQLite if cases table is empty (ADR-007, ADR-008)."""
    from datetime import UTC
    from decimal import Decimal

    from medishield.db.enums import (
        ActorType,
        BusinessDecision,
        DocumentType,
        EscalationSubReason,
        WorkflowState,
    )
    from medishield.db.models import Case, Decision, Document

    if session.query(Case).count() > 0:
        return 0

    now = datetime.now(UTC)
    c1_id = uuid.uuid4()
    c1 = Case(
        id=c1_id,
        case_id="CASE-20261004-902144",
        workflow_state=WorkflowState.AWAITING_HUMAN,
        workflow_version="v1.0.0",
        current_decision=BusinessDecision.ESCALATE,
        current_sub_reason=EscalationSubReason.FRAUD_REVIEW,
        decision_confidence=Decimal("0.88"),
        is_complete=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    d1 = Document(
        id=uuid.uuid4(),
        document_id="DOC-902144-01",
        case_id=c1_id,
        file_name="hospital_discharge_summary.pdf",
        object_key="raw/CASE-20261004-902144/DOC-902144-01.pdf",
        mime_type="application/pdf",
        checksum="a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
        size_bytes=245000,
        doc_type=DocumentType.DISCHARGE_SUMMARY,
        created_at=now,
    )
    d2 = Document(
        id=uuid.uuid4(),
        document_id="DOC-902144-02",
        case_id=c1_id,
        file_name="itemized_claim_form.pdf",
        object_key="raw/CASE-20261004-902144/DOC-902144-02.pdf",
        mime_type="application/pdf",
        checksum="b2c3d4e5f6a10718293a4b5c6d7e8f90123456789abcdef0123456789abcdef1",
        size_bytes=180000,
        doc_type=DocumentType.CLAIM_FORM,
        created_at=now,
    )
    dec1 = Decision(
        id=uuid.uuid4(),
        case_id=c1_id,
        decision=BusinessDecision.ESCALATE,
        escalation_sub_reason=EscalationSubReason.FRAUD_REVIEW,
        actor_type=ActorType.SYSTEM,
        actor_id="system-decision-engine",
        decision_confidence=Decimal("0.88"),
        rule_id_matched="RULE-P4-HIGH-FRAUD",
        justification="Fraud Escalation (P4): High fraud risk score (0.88 >= 0.70). Anomalies: Duplicate billing detected across providers.",
        agent_summary_snapshot={
            "fraud": {
                "verdict": "FAIL",
                "confidence": 0.88,
                "fraud_score": 0.88,
                "risk_level": "HIGH",
                "anomalies": ["Duplicate billing detected across providers"],
            }
        },
        case_version_at_decision=1,
        is_active=True,
        created_at=now,
    )
    session.add_all([c1, d1, d2, dec1])

    c2_id = uuid.uuid4()
    c2 = Case(
        id=c2_id,
        case_id="CASE-20261004-811092",
        workflow_state=WorkflowState.COMPLETED,
        workflow_version="v1.0.0",
        current_decision=BusinessDecision.APPROVE,
        decision_confidence=Decimal("0.99"),
        is_complete=True,
        version=1,
        created_at=now,
        updated_at=now,
    )
    d3 = Document(
        id=uuid.uuid4(),
        document_id="DOC-811092-01",
        case_id=c2_id,
        file_name="pharmacy_prescription.png",
        object_key="raw/CASE-20261004-811092/DOC-811092-01.png",
        mime_type="image/png",
        checksum="c3d4e5f6a1b20718293a4b5c6d7e8f90123456789abcdef0123456789abcdef2",
        size_bytes=85000,
        doc_type=DocumentType.PRESCRIPTION,
        created_at=now,
    )
    dec2 = Decision(
        id=uuid.uuid4(),
        case_id=c2_id,
        decision=BusinessDecision.APPROVE,
        escalation_sub_reason=None,
        actor_type=ActorType.SYSTEM,
        actor_id="system-decision-engine",
        decision_confidence=Decimal("0.99"),
        rule_id_matched="RULE-P6-CLEAN-PASS",
        justification="Clean Straight-Through Pass (P6): All identity, claims arithmetic, and policy coverage criteria passed with low fraud risk.",
        agent_summary_snapshot={
            "fraud": {
                "verdict": "PASS",
                "confidence": 0.99,
                "fraud_score": 0.05,
                "risk_level": "LOW",
                "anomalies": [],
            }
        },
        case_version_at_decision=1,
        is_active=True,
        created_at=now,
    )
    session.add_all([c2, d3, dec2])
    session.commit()
    logger.info("Successfully seeded 2 demo cases into database")
    return 2


def seed_benchmark_cluster_cases(session: Session) -> int:
    """Seeds representative cases from the 30-cluster dataset into the database."""
    from datetime import UTC
    from decimal import Decimal

    from medishield.db.enums import (
        ActorType,
        BusinessDecision,
        DocumentType,
        EscalationSubReason,
        WorkflowState,
    )
    from medishield.db.models import Case, Decision, Document

    existing_case_ids = {c[0] for c in session.query(Case.case_id).all()}

    now = datetime.now(UTC)
    seeded = 0

    benchmark_cases = [
        {
            "case_id": "CASE-C001-PT19116",
            "state": WorkflowState.COMPLETED,
            "decision": BusinessDecision.APPROVE,
            "sub_reason": None,
            "confidence": Decimal("0.99"),
            "rule": "RULE-P6-CLEAN-PASS",
            "justification": "Clean Straight-Through Pass (P6): Identity, hospital claim arithmetic ($1,250.00), and policy coverage verified with zero fraud flags.",
            "fraud": {"verdict": "PASS", "confidence": 0.99, "fraud_score": 0.05, "risk_level": "LOW", "anomalies": []},
            "docs": [
                ("claim_PT_19116.png", DocumentType.CLAIM_FORM, 185000),
                ("id_PT_19116.png", DocumentType.ID_DOCUMENT, 92000),
                ("discharge_PT_19116.png", DocumentType.DISCHARGE_SUMMARY, 210000),
                ("rx_PT_19116.png", DocumentType.PRESCRIPTION, 88000),
                ("amend_PT_19116.png", DocumentType.POLICY_AMENDMENT, 95000),
            ],
        },
        {
            "case_id": "CASE-C005-PT20322",
            "state": WorkflowState.AWAITING_HUMAN,
            "decision": BusinessDecision.ESCALATE,
            "sub_reason": EscalationSubReason.FRAUD_REVIEW,
            "confidence": Decimal("0.85"),
            "rule": "RULE-P4-HIGH-FRAUD",
            "justification": "Fraud Escalation (P4): Duplicate claim submission collision detected with prior service episode in active policy window.",
            "fraud": {"verdict": "FAIL", "confidence": 0.85, "fraud_score": 0.85, "risk_level": "HIGH", "anomalies": ["Duplicate claim hash collision with active billing episode"]},
            "docs": [
                ("claim_PT_20322.png", DocumentType.CLAIM_FORM, 172000),
                ("id_PT_20322.png", DocumentType.ID_DOCUMENT, 91000),
                ("discharge_PT_20322.png", DocumentType.DISCHARGE_SUMMARY, 198000),
            ],
        },
        {
            "case_id": "CASE-C012-PT16658",
            "state": WorkflowState.COMPLETED,
            "decision": BusinessDecision.APPROVE,
            "sub_reason": None,
            "confidence": Decimal("0.98"),
            "rule": "RULE-P6-CLEAN-PASS",
            "justification": "Clean Straight-Through Pass (P6): Orthopedic surgical pre-authorization matched policy rider MED-GLD-5437001.",
            "fraud": {"verdict": "PASS", "confidence": 0.98, "fraud_score": 0.04, "risk_level": "LOW", "anomalies": []},
            "docs": [
                ("claim_PT_16658.png", DocumentType.CLAIM_FORM, 190000),
                ("id_PT_16658.png", DocumentType.ID_DOCUMENT, 89000),
                ("discharge_PT_16658.png", DocumentType.DISCHARGE_SUMMARY, 220000),
                ("rx_PT_16658.png", DocumentType.PRESCRIPTION, 84000),
            ],
        },
        {
            "case_id": "CASE-C014-PT15075",
            "state": WorkflowState.AWAITING_INFO,
            "decision": BusinessDecision.ESCALATE,
            "sub_reason": EscalationSubReason.MISSING_INFORMATION,
            "confidence": Decimal("0.72"),
            "rule": "RULE-P2-MISSING-MANDATORY-DOCS",
            "justification": "Awaiting Supplementary Documentation: Hospital discharge summary required to substantiate inpatient diagnosis code.",
            "fraud": {"verdict": "PASS", "confidence": 0.90, "fraud_score": 0.10, "risk_level": "LOW", "anomalies": []},
            "docs": [
                ("claim_PT_15075.png", DocumentType.CLAIM_FORM, 165000),
                ("id_PT_15075.png", DocumentType.ID_DOCUMENT, 90000),
            ],
        },
        {
            "case_id": "CASE-C002-PT99733",
            "state": WorkflowState.COMPLETED,
            "decision": BusinessDecision.APPROVE,
            "sub_reason": None,
            "confidence": Decimal("0.99"),
            "rule": "RULE-P6-CLEAN-PASS",
            "justification": "Clean Straight-Through Pass (P6): Outpatient emergency care claim validated against policy MED-GLD-3728882.",
            "fraud": {"verdict": "PASS", "confidence": 0.99, "fraud_score": 0.03, "risk_level": "LOW", "anomalies": []},
            "docs": [
                ("claim_PT_99733.png", DocumentType.CLAIM_FORM, 178000),
                ("id_PT_99733.png", DocumentType.ID_DOCUMENT, 93000),
                ("rx_PT_99733.png", DocumentType.PRESCRIPTION, 81000),
            ],
        },
    ]

    for bc in benchmark_cases:
        if bc["case_id"] in existing_case_ids:
            continue

        c_id = uuid.uuid4()
        c = Case(
            id=c_id,
            case_id=bc["case_id"],
            workflow_state=bc["state"],
            workflow_version="v1.0.0",
            current_decision=bc["decision"],
            current_sub_reason=bc["sub_reason"],
            decision_confidence=bc["confidence"],
            is_complete=(bc["state"] == WorkflowState.COMPLETED),
            missing_documents=["DISCHARGE_SUMMARY"] if bc["state"] == WorkflowState.AWAITING_INFO else [],
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(c)

        for fname, dtype, size in bc["docs"]:
            d_id = uuid.uuid4()
            d = Document(
                id=d_id,
                document_id=f"DOC-{fname.replace('.png', '')}",
                case_id=c_id,
                file_name=fname,
                object_key=f"raw/{bc['case_id']}/{fname}",
                mime_type="image/png",
                checksum=uuid.uuid5(uuid.NAMESPACE_DNS, fname).hex,
                size_bytes=size,
                doc_type=dtype,
                created_at=now,
            )
            session.add(d)

        dec_id = uuid.uuid4()
        dec = Decision(
            id=dec_id,
            case_id=c_id,
            decision=bc["decision"],
            escalation_sub_reason=bc["sub_reason"],
            actor_type=ActorType.SYSTEM,
            actor_id="system-decision-engine",
            decision_confidence=bc["confidence"],
            rule_id_matched=bc["rule"],
            justification=bc["justification"],
            agent_summary_snapshot={"fraud": bc["fraud"]},
            case_version_at_decision=1,
            is_active=True,
            created_at=now,
        )
        session.add(dec)
        seeded += 1

    session.commit()
    logger.info("Successfully seeded %d benchmark cases from dataset into database", seeded)
    return seeded


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    session_factory = get_sqlite_session_factory()
    with session_factory() as sess:
        count = seed_benchmark_dataset(sess)
        demo_count = seed_demo_cases_if_empty(sess)
        bench_count = seed_benchmark_cluster_cases(sess)
        print(f"Database seeder complete: {count} members seeded, {demo_count} demo cases, {bench_count} benchmark cases.")

