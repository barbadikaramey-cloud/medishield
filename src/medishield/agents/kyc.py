import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

logger = logging.getLogger(__name__)

from medishield.core.tracing import trace_span
from medishield.db.enums import AgentVerdict
from medishield.domain.contracts import (
    KYCAgentOutput,
    OCRDocumentPayload,
    OCRTextSpan,
    TamperSignal,
)
from medishield.domain.faithfulness import verify_extracted_span
from medishield.domain.provenance import ProvenanceValue


@dataclass
class MemberRecord:
    """Authoritative member entity representation for KYC verification."""

    member_id: str
    first_name: str
    last_name: str
    date_of_birth: date
    ssn_last4: str | None = None
    status: str = "ACTIVE"
    policy_id: str | None = None
    policy_active_start: date | None = None
    policy_active_end: date | None = None


class MemberLookupProtocol(Protocol):
    """Protocol for member database verification."""

    def find_member(self, member_id: str) -> MemberRecord | None:
        """Finds member record by unique member identifier."""
        ...


def _get_default_seed_members() -> list[MemberRecord]:
    """Provides baseline member entities for evaluation and local environments."""
    records = [
        MemberRecord(
            member_id="MEM-12345",
            first_name="Alice",
            last_name="Smith",
            date_of_birth=date(1985, 5, 20),
            ssn_last4="4321",
            status="ACTIVE",
            policy_id="POL-CORP-2026",
            policy_active_start=date(2025, 1, 1),
            policy_active_end=date(2026, 12, 31),
        ),
        MemberRecord(
            member_id="MED-GLD-3592974",
            first_name="John",
            last_name="Johnson",
            date_of_birth=date(1958, 11, 8),
            ssn_last4="7658",
            status="ACTIVE",
            policy_id="MED-GLD-3592974",
            policy_active_start=date(2025, 7, 1),
            policy_active_end=date(2026, 7, 1),
        ),
        MemberRecord(
            member_id="PT_15075",
            first_name="John",
            last_name="Johnson",
            date_of_birth=date(1958, 11, 8),
            ssn_last4="7658",
            status="ACTIVE",
            policy_id="MED-GLD-3592974",
            policy_active_start=date(2025, 7, 1),
            policy_active_end=date(2026, 7, 1),
        ),
    ]
    seen_ids = {"MEM-12345", "MED-GLD-3592974", "PT_15075"}
    try:
        from medishield.db.seeder import FALLBACK_CLUSTERS, _parse_dob

        for c in FALLBACK_CLUSTERS:
            pid = c["patient_id"].upper()
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            raw_name = c["name"].strip()
            parts = raw_name.split()
            fn = parts[0] if parts else "Patient"
            ln = " ".join(parts[1:]) if len(parts) > 1 else "Unknown"
            ssn_raw = c.get("ssn", "000-00-0000")
            ssn_4 = ssn_raw.split("-")[-1] if "-" in ssn_raw else ssn_raw[-4:]
            records.append(
                MemberRecord(
                    member_id=c["patient_id"],
                    first_name=fn,
                    last_name=ln,
                    date_of_birth=_parse_dob(c["dob"]),
                    ssn_last4=ssn_4,
                    status="ACTIVE",
                    policy_id=c.get("policy"),
                    policy_active_start=date(2025, 1, 1),
                    policy_active_end=date(2026, 12, 31),
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Fallback cluster loading skipped: %s", exc)
    return records


class InMemoryMemberRepository:
    """In-memory member repository for testing and standalone evaluation."""

    def __init__(self, members: list[MemberRecord] | None = None) -> None:
        seed_list = members if members is not None else _get_default_seed_members()
        self._members: dict[str, MemberRecord] = {m.member_id: m for m in seed_list}

    def add_member(self, member: MemberRecord) -> None:
        self._members[member.member_id] = member

    def find_member(self, member_id: str) -> MemberRecord | None:
        with trace_span(
            name="db_member_lookup",
            run_type="retriever",
            component="member_repository",
            operation="find_member",
            inputs={},
        ) as span:
            clean_id = member_id.strip().upper()
            result: MemberRecord | None = None
            if clean_id in self._members:
                result = self._members[clean_id]
            else:
                for m in self._members.values():
                    if m.policy_id and m.policy_id.upper() == clean_id:
                        result = m
                        break
                    if m.member_id.upper() == clean_id:
                        result = m
                        break

            span.set_summary(
                status="SUCCESS",
                member_matched=bool(result is not None),
                is_complete=bool(result is not None),
            )
            return result


class SqlMemberRepository:
    """Authoritative SQL database member repository (Spec 05 § 4, ADR-008)."""

    def __init__(self, session_or_factory: Any) -> None:
        self._session_or_factory = session_or_factory

    def _get_session(self) -> Any:
        if callable(self._session_or_factory):
            return self._session_or_factory()
        return self._session_or_factory

    def find_member(self, member_id: str) -> MemberRecord | None:
        with trace_span(
            name="db_member_lookup",
            run_type="retriever",
            component="member_repository",
            operation="find_member",
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
                        member_matched=False,
                        is_complete=False,
                    )
                    return None

                enr = (
                    session.query(Enrollment)
                    .join(PolicyVersion, Enrollment.policy_version_id == PolicyVersion.id)
                    .join(Policy, PolicyVersion.policy_id == Policy.id)
                    .filter(Enrollment.member_id == m.id, Enrollment.status == "ACTIVE")
                    .first()
                )
                pol_id = None
                start_dt = None
                end_dt = None
                if enr:
                    pol_id = enr.policy_version.policy.policy_id
                    start_dt = enr.coverage_start_date
                    end_dt = enr.coverage_end_date

                rec = MemberRecord(
                    member_id=m.member_id,
                    first_name=m.first_name,
                    last_name=m.last_name,
                    date_of_birth=m.date_of_birth,
                    ssn_last4=m.ssn_last4,
                    status=m.status,
                    policy_id=pol_id,
                    policy_active_start=start_dt,
                    policy_active_end=end_dt,
                )
                span.set_summary(
                    status="SUCCESS",
                    member_matched=True,
                    is_complete=True,
                )
                return rec
            finally:
                if should_close:
                    session.close()


class KYCAgent:
    """
    Authoritative KYC Agent (Spec 05 § 4, Spec 06 § 2).
    Extracts member identity attributes from OCR payloads, verifies grounded text spans,
    matches member records against the database, and inspects image tamper indicators.
    """

    def __init__(self, member_repo: MemberLookupProtocol | None = None) -> None:
        self.member_repo = member_repo or InMemoryMemberRepository()

    def execute(
        self,
        case_id: str,
        document_payloads: list[OCRDocumentPayload],
        service_date: date | None = None,
        reference_date: date | None = None,
    ) -> KYCAgentOutput:
        """
        Executes KYC extraction, member verification, and tamper analysis.
        Returns typed KYCAgentOutput conforming strictly to Spec 05 § 4.
        """
        start_time = time.perf_counter()
        today = reference_date or datetime.now(UTC).date()

        # 1. Combine OCR spans across intake documents
        all_spans_with_doc: list[tuple[OCRDocumentPayload, int, OCRTextSpan]] = []
        for doc in document_payloads:
            for page in doc.pages:
                for span in page.spans:
                    all_spans_with_doc.append((doc, page.page_number, span))

        # Discover encounter service date from spans if not explicitly provided
        if service_date is None:
            svc_pattern = re.compile(
                r"(?:date\s*of\s*service|dates\s*of\s*service|admission\s*date|admit\s*date|service\s*date)\s*[:]?\s*"
                r"([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})",
                re.IGNORECASE,
            )
            for _, _, span in all_spans_with_doc:
                m_svc = svc_pattern.search(span.text)
                if m_svc:
                    cand = self._parse_date_string(m_svc.group(1).strip())
                    if cand and cand.year >= 2020:
                        service_date = cand
                        break

        # 2. Extract identity fields and anchor to OCR spans
        member_id_pv = self._extract_member_id(document_payloads, all_spans_with_doc)
        first_name_pv, last_name_pv = self._extract_names(document_payloads, all_spans_with_doc)
        dob_pv, extracted_dob = self._extract_dob(document_payloads, all_spans_with_doc)
        gov_id_pv, _exp_date_pv, extracted_exp = self._extract_gov_id_and_expiry(
            document_payloads, all_spans_with_doc
        )
        
        # Merge VLM enhancements (ADR-009)
        for doc in document_payloads:
            if hasattr(doc, "vlm_extraction") and doc.vlm_extraction:
                vlm = doc.vlm_extraction
                if vlm.get("member_id"):
                    val = str(vlm["member_id"]).upper().strip()
                    prov = member_id_pv.provenance if member_id_pv else None
                    member_id_pv = ProvenanceValue(value=val, confidence=0.99, provenance=prov)
                if vlm.get("first_name"):
                    val = str(vlm["first_name"]).capitalize().strip()
                    prov = first_name_pv.provenance if first_name_pv else None
                    first_name_pv = ProvenanceValue(value=val, confidence=0.99, provenance=prov)
                if vlm.get("last_name"):
                    val = str(vlm["last_name"]).capitalize().strip()
                    prov = last_name_pv.provenance if last_name_pv else None
                    last_name_pv = ProvenanceValue(value=val, confidence=0.99, provenance=prov)
                if vlm.get("date_of_birth"):
                    val = str(vlm["date_of_birth"]).strip()
                    prov = dob_pv.provenance if dob_pv else None
                    parsed = self._parse_date_string(val)
                    if parsed:
                        extracted_dob = parsed
                        dob_pv = ProvenanceValue(value=parsed.isoformat(), confidence=0.99, provenance=prov)
                if vlm.get("government_id_number"):
                    val = str(vlm["government_id_number"]).upper().strip()
                    prov = gov_id_pv.provenance if gov_id_pv else None
                    gov_id_pv = ProvenanceValue(value=val, confidence=0.99, provenance=prov)
                if vlm.get("government_id_expiration_date"):
                    val = str(vlm["government_id_expiration_date"]).strip()
                    parsed = self._parse_date_string(val)
                    if parsed:
                        extracted_exp = parsed
        
        # DEBUG: Print exact extracted IDs to terminal
        m_val = member_id_pv.value if member_id_pv else "None"
        g_val = gov_id_pv.value if gov_id_pv else "None"
        logger.info("=" * 60)
        logger.info("KYC EXTRACTION DEBUG FOR CASE: %s", case_id)
        logger.info("-> Extracted MEMBER ID (Used for DB lookup): %s", m_val)
        logger.info("-> Extracted GOV ID (Ignored for DB lookup): %s", g_val)
        logger.info("=" * 60)

        # 3. Detect physical and digital tampering indicators
        tamper_signals, tamper_score = self._assess_tampering(all_spans_with_doc)
        tamper_detected = tamper_score >= 0.30

        # 4. Perform database checks against Member repository
        member_db_matched = False
        dob_matched = False
        policy_active = False
        flags: list[str] = []

        if member_id_pv is not None and member_id_pv.value:
            matched_member = self.member_repo.find_member(str(member_id_pv.value))
            if matched_member is not None:
                member_db_matched = True
                # Check date of birth match
                if extracted_dob is not None and matched_member.date_of_birth == extracted_dob:
                    dob_matched = True
                elif extracted_dob is not None:
                    flags.append("DEMOGRAPHIC_DOB_MISMATCH")

                # Check active coverage on service date
                if service_date is not None:
                    start_ok = (
                        matched_member.policy_active_start is None
                        or matched_member.policy_active_start <= service_date
                    )
                    end_ok = (
                        matched_member.policy_active_end is None
                        or matched_member.policy_active_end >= service_date
                    )
                    policy_active = start_ok and end_ok and matched_member.status == "ACTIVE"
                    if not policy_active:
                        flags.append("POLICY_INACTIVE_ON_SERVICE_DATE")
                else:
                    policy_active = matched_member.status == "ACTIVE"
            else:
                flags.append("MEMBER_NOT_FOUND_IN_DB")
        else:
            flags.append("MISSING_MEMBER_ID")

        # 5. Check ID expiration against encounter service_date (or today if no service date is discoverable)
        id_expired = False
        target_reference_date = service_date or today
        if extracted_exp is not None:
            if extracted_exp < target_reference_date:
                id_expired = True
                flags.append("EXPIRED_GOVERNMENT_ID")
            elif (extracted_exp - target_reference_date).days <= 60:
                flags.append("EXPIRING_SOON_GOVERNMENT_ID")
        if not id_expired:
            for _, _, span in all_spans_with_doc:
                s_lower = span.text.lower()
                if (
                    "cardexpired" in s_lower
                    or "card expired" in s_lower
                    or "expired-notvalid" in s_lower
                    or "**expired**" in s_lower
                    or "*expired*" in s_lower
                    or re.search(r"^\s*\**expired\**\s*$", s_lower)
                    or re.search(r"\bstatus\s*[:]?\s*expired\b", s_lower)
                ):
                    id_expired = True
                    flags.append("EXPIRED_GOVERNMENT_ID")
                    break

        # 6. Calculate calibrated agent confidence and final verdict
        verdict, confidence = self._adjudicate_verdict(
            member_id_pv=member_id_pv,
            first_name_pv=first_name_pv,
            last_name_pv=last_name_pv,
            dob_pv=dob_pv,
            member_db_matched=member_db_matched,
            dob_matched=dob_matched,
            id_expired=id_expired,
            tamper_score=tamper_score,
            flags=flags,
        )

        elapsed_ms = int((time.perf_counter() - start_time) * 1000)

        return KYCAgentOutput(
            agent_name="kyc_agent",
            verdict=verdict,
            confidence=confidence,
            member_id=member_id_pv,
            first_name=first_name_pv,
            last_name=last_name_pv,
            date_of_birth=dob_pv,
            government_id_number=gov_id_pv,
            member_db_matched=member_db_matched,
            dob_matched=dob_matched,
            id_expired=id_expired,
            id_expiration_date=extracted_exp,
            policy_active_on_service_date=policy_active,
            tamper_detected=tamper_detected,
            tamper_score=round(tamper_score, 2),
            tamper_signals=tamper_signals,
            flags=flags,
            latency_ms=elapsed_ms,
        )

    # ── Field Extraction Helpers with Span Grounding ─────────────────────────

    def _extract_member_id(
        self,
        docs: list[OCRDocumentPayload],
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> ProvenanceValue[str] | None:
        """Extracts and grounds member ID (e.g. MED-GLD-3592974, MEM-12345, or PT_15075)."""
        pattern = re.compile(
            r"\b((?:MED|MEM)-(?:GLD|SLV|[A-Z0-9]+)-[0-9]{4,10}|MEM(?:-[A-Z0-9]+)+|M-[0-9]{5,10}|PT[-_][0-9]{4,10})\b",
            re.IGNORECASE,
        )
        for doc, page_num, span in spans:
            match = pattern.search(span.text)
            if match:
                raw_val = match.group(1).upper()
                is_grounded, prov = verify_extracted_span(raw_val, span.span_id, page_num, doc)
                return ProvenanceValue(
                    value=raw_val,
                    confidence=0.98 if is_grounded else 0.50,
                    provenance=prov,
                )

        # Fallback key-value and adjacent-span search
        kv_pattern = re.compile(
            r"(?:member\s*id|insured(?:'s)?\s*(?:i\.?d\.?|l\.?d\.?|id)?\s*(?:number)?|policy\s*number|policy)\s*[:#]?",
            re.IGNORECASE,
        )
        val_candidate_pattern = re.compile(
            r"([A-Z0-9]+(?:-[A-Z0-9]+)+|PT[-_][0-9]{4,10})",
            re.IGNORECASE,
        )
        for i, (doc, page_num, span) in enumerate(spans):
            if kv_pattern.search(span.text):
                # Check current span after label
                rem = kv_pattern.split(span.text, maxsplit=1)
                if len(rem) > 1 and rem[1].strip():
                    v_match = val_candidate_pattern.search(rem[1])
                    if v_match:
                        raw_val = v_match.group(1).strip().upper()
                        is_grounded, prov = verify_extracted_span(raw_val, span.span_id, page_num, doc)
                        return ProvenanceValue(
                            value=raw_val,
                            confidence=0.95 if is_grounded else 0.50,
                            provenance=prov,
                        )
                # Check adjacent subsequent spans
                for j in range(i + 1, min(i + 4, len(spans))):
                    next_doc, next_page, next_span = spans[j]
                    if next_doc.document_id == doc.document_id:
                        v_match = val_candidate_pattern.search(next_span.text)
                        if v_match and not any(k in next_span.text.lower() for k in ["group", "same as", "name", "address"]):
                            raw_val = v_match.group(1).strip().upper()
                            is_grounded, prov = verify_extracted_span(raw_val, next_span.span_id, next_page, next_doc)
                            return ProvenanceValue(
                                value=raw_val,
                                confidence=0.95 if is_grounded else 0.50,
                                provenance=prov,
                            )
        return None

    def _extract_names(
        self,
        docs: list[OCRDocumentPayload],
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> tuple[ProvenanceValue[str] | None, ProvenanceValue[str] | None]:
        """Extracts patient / member first and last names."""
        name_pattern = re.compile(
            r"(?:patient(?:'s)?\s*name|member\s*name|insured(?:'s)?\s*name|policyholder)\s*[:]?\s*([A-Za-z]+)\s+([A-Za-z]+)",
            re.IGNORECASE,
        )
        for doc, page_num, span in spans:
            match = name_pattern.search(span.text)
            if match:
                first = match.group(1).strip().capitalize()
                last = match.group(2).strip().capitalize()

                _, prov_first = verify_extracted_span(first, span.span_id, page_num, doc)
                _, prov_last = verify_extracted_span(last, span.span_id, page_num, doc)

                first_pv: ProvenanceValue[str] = ProvenanceValue(value=first, confidence=0.95, provenance=prov_first)
                last_pv: ProvenanceValue[str] = ProvenanceValue(value=last, confidence=0.95, provenance=prov_last)
                return first_pv, last_pv

        # Multi-span search: label followed by name within 3 spans
        header_pattern = re.compile(
            r"(?:member\s*name|patient(?:'s)?\s*name|insured(?:'s)?\s*name|policyholder)",
            re.IGNORECASE,
        )
        person_name_pattern = re.compile(r"^([A-Za-z]{2,})\s+([A-Za-z]{2,})(?:\s+[A-Z]\.?)?$", re.IGNORECASE)
        for i, (doc, page_num, span) in enumerate(spans):
            if header_pattern.search(span.text):
                for j in range(i + 1, min(i + 4, len(spans))):
                    next_doc, next_page, next_span = spans[j]
                    if next_doc.document_id == doc.document_id:
                        text_cand = next_span.text.strip()
                        if any(w in text_cand.upper() for w in ["NAME", "MEMBER", "PATIENT", "INSURED", "POLICY", "SUMMARY", "COST", "CARD", "GROUP", "ADDRESS", "SAME AS"]):
                            continue
                        p_match = person_name_pattern.match(text_cand)
                        if p_match:
                            first = p_match.group(1).strip().capitalize()
                            last = p_match.group(2).strip().capitalize()
                            _, prov_first = verify_extracted_span(first, next_span.span_id, next_page, next_doc)
                            _, prov_last = verify_extracted_span(last, next_span.span_id, next_page, next_doc)
                            return (
                                ProvenanceValue(value=first, confidence=0.95, provenance=prov_first),
                                ProvenanceValue(value=last, confidence=0.95, provenance=prov_last),
                            )
        return None, None

    def _extract_dob(
        self,
        docs: list[OCRDocumentPayload],
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> tuple[ProvenanceValue[str] | None, date | None]:
        """Extracts date of birth and parses into standard date object."""
        dob_label_pattern = re.compile(r"\b(?:dob|birth\s*date|date\s*of\s*birth)\b", re.IGNORECASE)
        date_pattern = re.compile(r"\b([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})\b")

        # Geometric KVP association is stronger than searching nearby flattened spans.
        for doc in docs:
            for page in doc.pages:
                for pair in page.key_value_pairs:
                    label = re.sub(r"^\s*\d{1,2}[a-z]?\s*", "", str(pair.get("key", "")), flags=re.IGNORECASE)
                    if not dob_label_pattern.search(label):
                        continue
                    raw_date = str(pair.get("value", "")).strip()
                    match = date_pattern.search(raw_date)
                    if not match:
                        continue
                    raw_date = match.group(1)
                    parsed_date = self._parse_date_string(raw_date)
                    if parsed_date is None:
                        continue
                    is_grounded, provenance = verify_extracted_span(
                        raw_date, pair.get("val_span_id"), page.page_number, doc
                    )
                    if is_grounded:
                        return (
                            ProvenanceValue(
                                value=parsed_date.isoformat(),
                                confidence=0.98,
                                provenance=provenance,
                            ),
                            parsed_date,
                        )

        dob_pattern = re.compile(
            r"(?:dob|birth\s*date|date\s*of\s*birth)\s*[:]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})",
            re.IGNORECASE,
        )
        for doc, page_num, span in spans:
            match = dob_pattern.search(span.text)
            if match:
                raw_date = match.group(1).strip()
                parsed_date = self._parse_date_string(raw_date)
                is_grounded, prov = verify_extracted_span(raw_date, span.span_id, page_num, doc)
                pv: ProvenanceValue[str] = ProvenanceValue(
                    value=parsed_date.isoformat() if parsed_date else raw_date,
                    confidence=0.95 if is_grounded else 0.50,
                    provenance=prov,
                )
                return pv, parsed_date

        # Proximity search within adjacent spans
        dob_header_pattern = re.compile(r"(?:dob|birth\s*date|date\s*of\s*birth)", re.IGNORECASE)
        date_pattern = re.compile(r"\b([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})\b")
        for i, (doc, page_num, span) in enumerate(spans):
            if dob_header_pattern.search(span.text):
                for j in range(i + 1, min(i + 4, len(spans))):
                    next_doc, next_page, next_span = spans[j]
                    if next_doc.document_id == doc.document_id:
                        # Avoid confusing DOB with admission/service dates
                        if any(k in next_span.text.lower() for k in ["admit", "admission", "discharge", "service", "thru"]):
                            continue
                        d_match = date_pattern.search(next_span.text)
                        if d_match:
                            raw_date = d_match.group(1).strip()
                            parsed_date = self._parse_date_string(raw_date)
                            is_grounded, prov = verify_extracted_span(raw_date, next_span.span_id, next_page, next_doc)
                            pv_prox: ProvenanceValue[str] = ProvenanceValue(
                                value=parsed_date.isoformat() if parsed_date else raw_date,
                                confidence=0.95 if is_grounded else 0.50,
                                provenance=prov,
                            )
                            return pv_prox, parsed_date
        return None, None

    def _extract_gov_id_and_expiry(
        self,
        docs: list[OCRDocumentPayload],
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> tuple[ProvenanceValue[str] | None, ProvenanceValue[str] | None, date | None]:
        """Extracts government ID number (Driver's License / Passport) and expiration date."""
        id_pattern = re.compile(r"(?:dl\s+number|license\s+no|id\s+number|passport\s+no)\s*[:#]?\s*([A-Z0-9]+)", re.IGNORECASE)
        exp_pattern = re.compile(
            r"(?:exp\s*[:.]?|exp\s+date|expiration\s*[:.]?|expiration\s+date|expires\s*[:.]?)\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})",
            re.IGNORECASE,
        )

        id_pv: ProvenanceValue[str] | None = None
        exp_pv: ProvenanceValue[str] | None = None
        parsed_exp: date | None = None

        for doc, page_num, span in spans:
            if id_pv is None:
                match_id = id_pattern.search(span.text)
                if match_id:
                    raw_id = match_id.group(1).strip().upper()
                    _, prov_id = verify_extracted_span(raw_id, span.span_id, page_num, doc)
                    id_pv = ProvenanceValue(value=raw_id, confidence=0.95, provenance=prov_id)

            if exp_pv is None:
                match_exp = exp_pattern.search(span.text)
                if match_exp:
                    raw_exp = match_exp.group(1).strip()
                    parsed_exp = self._parse_date_string(raw_exp)
                    _, prov_exp = verify_extracted_span(raw_exp, span.span_id, page_num, doc)
                    exp_pv = ProvenanceValue(
                        value=parsed_exp.isoformat() if parsed_exp else raw_exp,
                        confidence=0.95,
                        provenance=prov_exp,
                    )

        if exp_pv is None:
            exp_header_pattern = re.compile(r"\b(?:exp\b|exp\s*[:.]|expires|expiration)\b", re.IGNORECASE)
            date_pattern = re.compile(r"\b([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})\b")
            for i, (doc, page_num, span) in enumerate(spans):
                if exp_header_pattern.search(span.text):
                    for j in range(i + 1, min(i + 4, len(spans))):
                        next_doc, next_page, next_span = spans[j]
                        if next_doc.document_id == doc.document_id:
                            d_match = date_pattern.search(next_span.text)
                            if d_match:
                                raw_exp = d_match.group(1).strip()
                                parsed_exp = self._parse_date_string(raw_exp)
                                _, prov_exp = verify_extracted_span(raw_exp, next_span.span_id, next_page, next_doc)
                                exp_pv = ProvenanceValue(
                                    value=parsed_exp.isoformat() if parsed_exp else raw_exp,
                                    confidence=0.95,
                                    provenance=prov_exp,
                                )
                                break
                    if exp_pv is not None:
                        break

        return id_pv, exp_pv, parsed_exp

    def _assess_tampering(
        self,
        spans: list[tuple[OCRDocumentPayload, int, OCRTextSpan]],
    ) -> tuple[list[TamperSignal], float]:
        """Inspects document signals for physical alteration or digital spoofing."""
        signals: list[TamperSignal] = []
        score = 0.0

        for _, _, span in spans:
            text = span.text.lower()
            if "ela_anomaly" in text or "spliced" in text:
                signals.append(
                    TamperSignal(
                        signal_type="ELA_ANOMALY",
                        severity="HIGH",
                        description="Error level analysis detected non-uniform compression boundary around text.",
                        confidence=0.90,
                    )
                )
                score = max(score, 0.85)

            if "font_inconsistency" in text or "mismatched font" in text:
                signals.append(
                    TamperSignal(
                        signal_type="FONT_INCONSISTENCY",
                        severity="MEDIUM",
                        description="Character baseline and font metrics mismatch adjacent layout text.",
                        confidence=0.80,
                    )
                )
                score = max(score, 0.60)

            if "clone_stamp" in text:
                signals.append(
                    TamperSignal(
                        signal_type="CLONE_STAMP",
                        severity="HIGH",
                        description="Repetitive pixel patterns detected indicating digital clone tool erasure.",
                        confidence=0.88,
                    )
                )
                score = max(score, 0.85)

        return signals, score

    def _adjudicate_verdict(
        self,
        member_id_pv: ProvenanceValue[str] | None,
        first_name_pv: ProvenanceValue[str] | None,
        last_name_pv: ProvenanceValue[str] | None,
        dob_pv: ProvenanceValue[str] | None,
        member_db_matched: bool,
        dob_matched: bool,
        id_expired: bool,
        tamper_score: float,
        flags: list[str],
    ) -> tuple[AgentVerdict, float]:
        """Adjudicates KYC verdict and confidence according to canonical rules."""
        # 1. Severe failure cases (Rule 14 & ADR-002: hard rejections)
        if "MEMBER_NOT_FOUND_IN_DB" in flags or "DEMOGRAPHIC_DOB_MISMATCH" in flags:
            return AgentVerdict.FAIL, 0.95

        if id_expired or tamper_score >= 0.80:
            return AgentVerdict.FAIL, 0.90

        # 2. Indeterminate cases (missing required identity elements or ungrounded facts)
        if member_id_pv is None or member_id_pv.provenance is None:
            return AgentVerdict.INDETERMINATE, 0.40

        if member_id_pv.provenance.evidence_quality_score < 0.50:
            return AgentVerdict.INDETERMINATE, 0.45

        # 3. Clean pass
        if member_db_matched and (dob_matched or dob_pv is None):
            confidence = 0.95 if dob_matched else 0.85
            return AgentVerdict.PASS, confidence

        return AgentVerdict.INDETERMINATE, 0.50

    def _parse_date_string(self, raw_str: str) -> date | None:
        """Parses various date format strings into date object."""
        formats = ["%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%y"]
        for fmt in formats:
            try:
                return datetime.strptime(raw_str, fmt).replace(tzinfo=UTC).date()
            except ValueError:
                continue
        return None
