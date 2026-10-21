"""Authoritative Medical Code & Terminology Catalog Repository (ADR-012).

Provides dynamic database and caching abstractions for clinical codes (CPT, HCPCS,
ICD-10-CM, Revenue) to support scale-out terminology management (100,000+ codes)
while preserving deterministic fallback for offline testing.
"""

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from medishield.db.enums import CodingSystem
from medishield.db.models import ClinicalGuideline, DiagnosisIndication, MedicalCode

logger = logging.getLogger(__name__)


@runtime_checkable
class ClinicalGuidelineRepository(Protocol):
    """Protocol for dynamic clinical guideline and indication repository (ADR-015, ADR-016)."""

    def get_guideline(self, service_code: str, service_date: date | None = None) -> dict[str, Any] | None:
        """Retrieve clinical practice guideline (LCD) and criteria for a service code."""
        ...

    def get_indication(self, service_code: str) -> dict[str, Any] | None:
        """Retrieve diagnosis-to-procedure clinical indication rule and anatomy."""
        ...

    def register_guideline(self, service_code: str, data: dict[str, Any]) -> None:
        """Dynamically register or override a clinical practice guideline."""
        ...

    def register_indication(self, service_code: str, data: dict[str, Any]) -> None:
        """Dynamically register or override a clinical indication rule."""
        ...


@runtime_checkable
class CodeCatalogRepository(Protocol):
    """Abstract interface for querying and managing clinical medical codes."""

    def is_valid_code(
        self,
        code: str,
        coding_system: CodingSystem = CodingSystem.CPT,
        service_date: date | None = None,
    ) -> bool:
        """Check if a code exists, is active, and is temporally valid."""
        ...

    def get_code_details(
        self,
        code: str,
        coding_system: CodingSystem = CodingSystem.CPT,
    ) -> MedicalCode | None:
        """Retrieve full clinical metadata and description for a code."""
        ...

    def search_codes(
        self,
        query: str,
        coding_system: CodingSystem | None = None,
        limit: int = 20,
    ) -> list[MedicalCode]:
        """Perform lexical prefix / description search over medical codes."""
        ...

    def get_active_codes(
        self,
        coding_system: CodingSystem = CodingSystem.CPT,
    ) -> frozenset[str]:
        """Return all active code strings for high-throughput in-memory validation."""
        ...

    def upsert_code(
        self,
        code: str,
        coding_system: CodingSystem,
        short_description: str,
        long_description: str | None = None,
        category: str | None = None,
        standard_charge: Decimal | None = None,
        requires_prior_auth: bool = False,
        effective_from: date | None = None,
        effective_to: date | None = None,
        is_active: bool = True,
    ) -> MedicalCode:
        """Create or update a medical code definition."""
        ...

    def bulk_upsert(self, items: list[dict[str, Any]]) -> int:
        """Bulk ingest multiple medical codes from CSV or external feed."""
        ...


class SqlCodeCatalogRepository:
    """Production SQL implementation backed by PostgreSQL / SQLite."""

    def __init__(self, db: Any, cache_ttl_seconds: int = 3600) -> None:
        self.db = db
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cached_active_sets: dict[CodingSystem, frozenset[str]] = {}

    def _get_session(self) -> tuple[Session, bool]:
        if isinstance(self.db, Session):
            return self.db, False
        if callable(self.db):
            return self.db(), True
        return Session(bind=self.db), True

    def is_valid_code(
        self,
        code: str,
        coding_system: CodingSystem = CodingSystem.CPT,
        service_date: date | None = None,
    ) -> bool:
        clean_code = code.strip().upper()
        # Fast path: check in cached active set if populated
        if coding_system in self._cached_active_sets:
            if clean_code not in self._cached_active_sets[coding_system]:
                return False
            if service_date is None:
                return True

        session, should_close = self._get_session()
        try:
            stmt = select(MedicalCode).where(
                MedicalCode.code == clean_code,
                MedicalCode.coding_system == coding_system,
                MedicalCode.is_active.is_(True),
            )
            if service_date is not None:
                stmt = stmt.where(
                    or_(MedicalCode.effective_from.is_(None), MedicalCode.effective_from <= service_date),
                    or_(MedicalCode.effective_to.is_(None), MedicalCode.effective_to >= service_date),
                )
            try:
                record = session.scalars(stmt).first()
            except OperationalError as exc:
                logger.warning("Medical code lookup unavailable: %s", exc)
                return False
            return record is not None
        finally:
            if should_close:
                session.close()

    def get_code_details(
        self,
        code: str,
        coding_system: CodingSystem = CodingSystem.CPT,
    ) -> MedicalCode | None:
        clean_code = code.strip().upper()
        session, should_close = self._get_session()
        try:
            stmt = select(MedicalCode).where(
                MedicalCode.code == clean_code,
                MedicalCode.coding_system == coding_system,
            )
            return session.scalars(stmt).first()
        finally:
            if should_close:
                session.close()

    def search_codes(
        self,
        query: str,
        coding_system: CodingSystem | None = None,
        limit: int = 20,
    ) -> list[MedicalCode]:
        q = f"%{query.strip().upper()}%"
        session, should_close = self._get_session()
        try:
            stmt = select(MedicalCode).where(
                or_(
                    MedicalCode.code.ilike(q),
                    MedicalCode.short_description.ilike(q),
                )
            )
            if coding_system is not None:
                stmt = stmt.where(MedicalCode.coding_system == coding_system)
            stmt = stmt.order_by(MedicalCode.code.asc()).limit(limit)
            return list(session.scalars(stmt).all())
        finally:
            if should_close:
                session.close()

    def get_active_codes(
        self,
        coding_system: CodingSystem = CodingSystem.CPT,
    ) -> frozenset[str]:
        if coding_system in self._cached_active_sets:
            return self._cached_active_sets[coding_system]
        session, should_close = self._get_session()
        try:
            stmt = select(MedicalCode.code).where(
                MedicalCode.coding_system == coding_system,
                MedicalCode.is_active.is_(True),
            )
            try:
                codes = frozenset(session.scalars(stmt).all())
            except OperationalError as exc:
                logger.warning("Medical code catalog unavailable: %s", exc)
                return frozenset()
            self._cached_active_sets[coding_system] = codes
            return codes
        finally:
            if should_close:
                session.close()

    def upsert_code(
        self,
        code: str,
        coding_system: CodingSystem,
        short_description: str,
        long_description: str | None = None,
        category: str | None = None,
        standard_charge: Decimal | None = None,
        requires_prior_auth: bool = False,
        effective_from: date | None = None,
        effective_to: date | None = None,
        is_active: bool = True,
    ) -> MedicalCode:
        clean_code = code.strip().upper()
        session, should_close = self._get_session()
        try:
            stmt = select(MedicalCode).where(
                MedicalCode.code == clean_code,
                MedicalCode.coding_system == coding_system,
            )
            record = session.scalars(stmt).first()
            now = datetime.now(UTC)
            if record:
                record.short_description = short_description
                record.long_description = long_description
                record.category = category
                record.standard_charge = standard_charge
                record.requires_prior_auth = requires_prior_auth
                record.effective_from = effective_from
                record.effective_to = effective_to
                record.is_active = is_active
                record.updated_at = now
            else:
                record = MedicalCode(
                    id=uuid.uuid4(),
                    code=clean_code,
                    coding_system=coding_system,
                    short_description=short_description,
                    long_description=long_description,
                    category=category,
                    standard_charge=standard_charge,
                    requires_prior_auth=requires_prior_auth,
                    effective_from=effective_from,
                    effective_to=effective_to,
                    is_active=is_active,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            session.commit()
            # Invalidate active set cache
            self._cached_active_sets.pop(coding_system, None)
            return record
        finally:
            if should_close:
                session.close()

    def bulk_upsert(self, items: list[dict[str, Any]]) -> int:
        count = 0
        systems_affected: set[CodingSystem] = set()
        for item in items:
            sys_val = item["coding_system"]
            if isinstance(sys_val, str):
                sys_enum = CodingSystem(sys_val)
            else:
                sys_enum = sys_val
            systems_affected.add(sys_enum)
            self.upsert_code(
                code=item["code"],
                coding_system=sys_enum,
                short_description=item["short_description"],
                long_description=item.get("long_description"),
                category=item.get("category"),
                standard_charge=Decimal(str(item["standard_charge"])) if item.get("standard_charge") is not None else None,
                requires_prior_auth=bool(item.get("requires_prior_auth", False)),
                effective_from=item.get("effective_from"),
                effective_to=item.get("effective_to"),
                is_active=bool(item.get("is_active", True)),
            )
            count += 1
        for s in systems_affected:
            self._cached_active_sets.pop(s, None)
        return count


class SqlClinicalGuidelineRepository:
    """Authoritative relational repository for clinical guidelines and indications (ADR-016)."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self._guidelines_cache: dict[str, dict[str, Any]] = {}
        self._indications_cache: dict[str, dict[str, Any]] = {}

    def _get_session(self) -> tuple[Session, bool]:
        if isinstance(self.db, Session):
            return self.db, False
        if callable(self.db):
            return self.db(), True
        return Session(bind=self.db), True

    def get_guideline(self, service_code: str, service_date: date | None = None) -> dict[str, Any] | None:
        code_key = service_code.strip().replace("REV_", "").upper()
        if code_key in self._guidelines_cache:
            entry = self._guidelines_cache[code_key]
            if service_date:
                eff = entry.get("effective_date")
                exp = entry.get("expiration_date")
                if eff and service_date < eff:
                    return None
                if exp and service_date > exp:
                    return None
            return entry

        from sqlalchemy.orm import selectinload

        session, should_close = self._get_session()
        try:
            stmt = (
                select(ClinicalGuideline)
                .options(selectinload(ClinicalGuideline.criteria))
                .where(
                    ClinicalGuideline.service_code == code_key,
                    ClinicalGuideline.is_active.is_(True),
                )
            )
            try:
                records = list(session.scalars(stmt).all())
            except OperationalError as exc:
                logger.warning("Clinical guideline lookup unavailable: %s", exc)
                return None
            if service_date:
                records = [
                    record for record in records
                    if record.effective_date <= service_date
                    and (record.expiration_date is None or record.expiration_date >= service_date)
                ]
            if len(records) != 1:
                if len(records) > 1:
                    logger.warning("Multiple active guideline versions found for service code %s", code_key)
                return None
            g = records[0]
            if not g:
                return None

            entry = {
                "guideline_id": g.guideline_id,
                "guideline_version": g.guideline_version,
                "guideline_title": g.guideline_title,
                "payer_product": g.payer_product,
                "jurisdiction": g.jurisdiction,
                "source_reference": g.source_reference,
                "source_chunk_id": g.source_chunk_id,
                "source_verified": g.source_verified,
                "effective_date": g.effective_date,
                "expiration_date": g.expiration_date,
                "requires_prior_auth": g.requires_prior_auth,
                "mandatory_criteria": [
                    {
                        "criterion_id": c.criterion_id,
                        "description": c.description,
                        "source_reference": c.source_reference,
                        "source_chunk_id": c.source_chunk_id,
                        "source_verified": c.source_verified,
                        "requirement_type": c.requirement_type,
                        "evaluation_parameters": c.evaluation_parameters,
                        "is_mandatory": c.is_mandatory,
                    }
                    for c in g.criteria
                ],
            }
            self._guidelines_cache[code_key] = entry
            if service_date:
                eff = entry.get("effective_date")
                exp = entry.get("expiration_date")
                if eff and service_date < eff:
                    return None
                if exp and service_date > exp:
                    return None
            return entry
        finally:
            if should_close:
                session.close()

    def is_valid_diagnosis_code(self, code: str, service_date: date | None = None) -> bool:
        """Check a normalized ICD-10-CM candidate against the versioned code catalog."""
        try:
            return SqlCodeCatalogRepository(self.db).is_valid_code(
                code,
                CodingSystem.ICD10_CM,
                service_date=service_date,
            )
        except OperationalError as exc:
            logger.warning("Diagnosis code validation unavailable: %s", exc)
            return False

    def get_indication(self, service_code: str) -> dict[str, Any] | None:
        code_key = service_code.strip().replace("REV_", "").upper()
        if code_key in self._indications_cache:
            return self._indications_cache[code_key]

        session, should_close = self._get_session()
        try:
            stmt = select(DiagnosisIndication).where(
                DiagnosisIndication.service_code == code_key,
                DiagnosisIndication.is_active.is_(True),
            )
            try:
                ind = session.scalars(stmt).first()
            except OperationalError as exc:
                logger.warning("Clinical indication lookup unavailable: %s", exc)
                return None
            if not ind:
                return None

            entry = {
                "name": ind.procedure_name,
                "valid_icd_prefixes": tuple(ind.valid_icd_prefixes or []),
                "anatomy": ind.anatomy,
                "contraindicated_incompatible_prefixes": tuple(ind.contraindicated_prefixes or []),
                "keywords": ind.keywords or [],
                "assertion_patterns": ind.assertion_patterns or {},
            }
            self._indications_cache[code_key] = entry
            return entry
        finally:
            if should_close:
                session.close()

    def register_guideline(self, service_code: str, data: dict[str, Any]) -> None:
        code_key = service_code.strip().replace("REV_", "").upper()
        self._guidelines_cache[code_key] = data

    def register_indication(self, service_code: str, data: dict[str, Any]) -> None:
        code_key = service_code.strip().replace("REV_", "").upper()
        self._indications_cache[code_key] = data


class InMemoryClinicalGuidelineRepository:
    """In-memory clinical guideline and indication repository for tests and offline evaluation."""

    def __init__(
        self,
        guidelines: dict[str, dict[str, Any]] | None = None,
        indications: dict[str, dict[str, Any]] | None = None,
        valid_diagnosis_codes: set[str] | None = None,
    ) -> None:
        self._guidelines: dict[str, dict[str, Any]] = dict(guidelines or {})
        self._indications: dict[str, dict[str, Any]] = dict(indications or {})
        self._valid_diagnosis_codes = {code.upper() for code in valid_diagnosis_codes or set()}

    def get_guideline(self, service_code: str, service_date: date | None = None) -> dict[str, Any] | None:
        code_key = service_code.strip().replace("REV_", "").upper()
        entry = self._guidelines.get(code_key)
        if not entry:
            return None
        if service_date:
            eff = entry.get("effective_date")
            exp = entry.get("expiration_date")
            if eff and service_date < eff:
                return None
            if exp and service_date > exp:
                return None
        return entry

    def get_indication(self, service_code: str) -> dict[str, Any] | None:
        code_key = service_code.strip().replace("REV_", "").upper()
        return self._indications.get(code_key)

    def is_valid_diagnosis_code(self, code: str, service_date: date | None = None) -> bool:
        return code.strip().upper() in self._valid_diagnosis_codes

    def normalize_diagnosis_code(self, raw_code: str, service_date: date | None = None) -> str | None:
        clean = "".join(character for character in raw_code.upper() if character.isalnum())
        matches = [
            code for code in self._valid_diagnosis_codes
            if "".join(character for character in code if character.isalnum()) == clean
        ]
        return matches[0] if len(matches) == 1 else None

    def get_active_code_details(
        self,
        code: str,
        coding_system: CodingSystem,
        service_date: date | None = None,
    ) -> dict[str, str] | None:
        key = code.strip().upper()
        if coding_system == CodingSystem.ICD10_CM:
            known = key in self._valid_diagnosis_codes
            description = key
        else:
            entry = self._indications.get(key)
            known = entry is not None
            description = (entry or {}).get("name", key)
        if not known:
            return None
        return {"code": key, "description": description, "coding_system": coding_system.value}

    def register_guideline(self, service_code: str, data: dict[str, Any]) -> None:
        code_key = service_code.strip().replace("REV_", "").upper()
        self._guidelines[code_key] = data

    def register_indication(self, service_code: str, data: dict[str, Any]) -> None:
        code_key = service_code.strip().replace("REV_", "").upper()
        self._indications[code_key] = data


class InMemoryCodeCatalogRepository:
    """Lightweight in-memory test fixture seeded with benchmark CPT procedure codes."""

    def __init__(self, initial_codes: Sequence[dict[str, Any]] | None = None) -> None:
        self._catalog: dict[tuple[CodingSystem, str], MedicalCode] = {}
        if initial_codes is not None:
            for item in initial_codes:
                self._insert_from_dict(item)
        else:
            self._seed_default_benchmarks()

    def _insert_from_dict(self, item: dict[str, Any]) -> None:
        sys_val = item["coding_system"]
        sys_enum = CodingSystem(sys_val) if isinstance(sys_val, str) else sys_val
        code = str(item["code"]).strip().upper()
        now = datetime.now(UTC)
        med_code = MedicalCode(

            id=uuid.uuid4(),
            code=code,
            coding_system=sys_enum,
            short_description=item.get("short_description", f"Procedure {code}"),
            long_description=item.get("long_description"),
            category=item.get("category", "General"),
            standard_charge=Decimal(str(item["standard_charge"])) if item.get("standard_charge") is not None else None,
            requires_prior_auth=bool(item.get("requires_prior_auth", False)),
            effective_from=item.get("effective_from"),
            effective_to=item.get("effective_to"),
            is_active=bool(item.get("is_active", True)),
            created_at=now,
            updated_at=now,
        )
        self._catalog[(sys_enum, code)] = med_code

    def _seed_default_benchmarks(self) -> None:
        # Seed all 18 canonical benchmark CPT codes
        benchmark_cpts = (
            "90837", "93000", "99213", "99214", "99215",
            "99203", "99204", "99205", "99283", "71045",
            "80053", "85025", "44970", "00790", "17000",
            "36415", "15780", "47562",
        )
        for cpt in benchmark_cpts:
            self._insert_from_dict({
                "code": cpt,
                "coding_system": CodingSystem.CPT,
                "short_description": f"Benchmark Procedure {cpt}",
                "category": "Benchmark Evaluation",
                "requires_prior_auth": False,
                "is_active": True,
            })

        # Major inpatient and surgical codes requiring mandatory prior authorization under policy terms
        major_pa_benchmarks = {
            "27447": "Total Knee Arthroplasty (TKA)",
            "47562": "Laparoscopic Cholecystectomy",
            "33533": "Coronary Artery Bypass Graft (CABG)",
            "33510": "Coronary Artery Bypass Single Graft",
            "29827": "Arthroscopic Rotator Cuff Repair",
            "21120": "Genioplasty / Facial Reconstruction",
            "44204": "Laparoscopic Partial Colectomy",
            "63030": "Laminotomy / Lumbar Discectomy",
            "22842": "Posterior Segmental Instrumentation",
            "22612": "Arthrodesis Posterior Lumbar",
            "43644": "Laparoscopic Gastric Bypass",
        }
        for cpt, desc in major_pa_benchmarks.items():
            self._insert_from_dict({
                "code": cpt,
                "coding_system": CodingSystem.CPT,
                "short_description": desc,
                "category": "Inpatient / Major Surgery",
                "requires_prior_auth": True,
                "is_active": True,
            })

        # Seed additional common benchmark procedure codes
        extra_benchmarks = [
            "43239", "71250", "99284", "99291", "70553", "72148",
            "93306", "71046", "36415", "15822", "58300", "86849", "15780",
        ]
        for cpt in extra_benchmarks:
            self._insert_from_dict({
                "code": cpt,
                "coding_system": CodingSystem.CPT,
                "short_description": f"Benchmark Surgical/Diagnostic {cpt}",
                "category": "Benchmark Evaluation",
                "requires_prior_auth": False,
                "is_active": True,
            })

    def is_valid_code(
        self,
        code: str,
        coding_system: CodingSystem = CodingSystem.CPT,
        service_date: date | None = None,
    ) -> bool:
        clean_code = code.strip().upper()
        record = self._catalog.get((coding_system, clean_code))
        if not record or not record.is_active:
            return False
        if service_date is not None:
            if record.effective_from and record.effective_from > service_date:
                return False
            if record.effective_to and record.effective_to < service_date:
                return False
        return True

    def get_code_details(
        self,
        code: str,
        coding_system: CodingSystem = CodingSystem.CPT,
    ) -> MedicalCode | None:
        clean_code = code.strip().upper()
        return self._catalog.get((coding_system, clean_code))

    def search_codes(
        self,
        query: str,
        coding_system: CodingSystem | None = None,
        limit: int = 20,
    ) -> list[MedicalCode]:
        q = query.strip().upper()
        results: list[MedicalCode] = []
        for (sys_enum, code), record in self._catalog.items():
            if coding_system is not None and sys_enum != coding_system:
                continue
            if q in code or (record.short_description and q in record.short_description.upper()):
                results.append(record)
            if len(results) >= limit:
                break
        return results

    def get_active_codes(
        self,
        coding_system: CodingSystem = CodingSystem.CPT,
    ) -> frozenset[str]:
        return frozenset(
            code for (sys_enum, code), rec in self._catalog.items()
            if sys_enum == coding_system and rec.is_active
        )

    def upsert_code(
        self,
        code: str,
        coding_system: CodingSystem,
        short_description: str,
        long_description: str | None = None,
        category: str | None = None,
        standard_charge: Decimal | None = None,
        requires_prior_auth: bool = False,
        effective_from: date | None = None,
        effective_to: date | None = None,
        is_active: bool = True,
    ) -> MedicalCode:
        self._insert_from_dict({
            "code": code,
            "coding_system": coding_system,
            "short_description": short_description,
            "long_description": long_description,
            "category": category,
            "standard_charge": standard_charge,
            "requires_prior_auth": requires_prior_auth,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "is_active": is_active,
        })
        return self._catalog[(coding_system, code.strip().upper())]

    def bulk_upsert(self, items: list[dict[str, Any]]) -> int:
        for item in items:
            self._insert_from_dict(item)
        return len(items)


class SqlClaimHistoryRepository:
    """Production SQL claim repository for cross-case duplicate and velocity checks (Spec 10 § 3, Spec 07 § 2)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def find_duplicate(
        self,
        case_id: str | None,
        member_id: str | None,
        provider_npi: str | None,
        service_date: Any | None,
        total_amount: float | Decimal | None,
    ) -> str | None:
        """Finds any prior claim matching member, service date, and total amount or provider."""
        from medishield.db.models import Claim

        if not member_id:
            return None

        clean_member_id = str(member_id).strip()
        stmt = select(Claim).where(Claim.member_id == clean_member_id)

        claims = self.db.scalars(stmt).all()
        for clm in claims:
            # Skip same case
            if case_id:
                try:
                    cid = uuid.UUID(str(case_id))
                    if clm.case_id == cid:
                        continue
                except (ValueError, TypeError):
                    if str(clm.case_id) == str(case_id):
                        continue

            date_match = False
            if service_date and clm.service_date:
                svc_str = str(service_date)[:10]
                clm_str = (
                    clm.service_date.isoformat()
                    if hasattr(clm.service_date, "isoformat")
                    else str(clm.service_date)[:10]
                )
                date_match = (svc_str == clm_str)

            amt_match = False
            if total_amount is not None and clm.total_amount is not None:
                amt_match = abs(float(clm.total_amount) - float(total_amount)) < 0.01

            npi_match = bool(provider_npi and clm.provider_npi == str(provider_npi).strip())

            if date_match and (amt_match or npi_match):
                return str(clm.claim_number or clm.case_id)

        return None


class InMemoryClaimHistoryRepository:
    """In-memory historical claim repository for offline duplicate and velocity tests."""

    def __init__(self, initial_claims: list[dict[str, Any]] | None = None) -> None:
        self._claims: list[dict[str, Any]] = list(initial_claims or [])

    def add_claim(self, claim_data: dict[str, Any]) -> None:
        self._claims.append(claim_data)

    def find_duplicate(
        self,
        case_id: str | None,
        member_id: str | None,
        provider_npi: str | None,
        service_date: Any | None,
        total_amount: float | Decimal | None,
    ) -> str | None:
        if not member_id:
            return None
        clean_member = str(member_id).strip().upper()
        for c in self._claims:
            if case_id and str(c.get("case_id")) == str(case_id):
                continue
            if str(c.get("member_id", "")).strip().upper() != clean_member:
                continue

            date_match = False
            if service_date and c.get("service_date"):
                date_match = str(service_date)[:10] == str(c["service_date"])[:10]

            amt_match = False
            if total_amount is not None and c.get("total_amount") is not None:
                amt_match = abs(float(c["total_amount"]) - float(total_amount)) < 0.01

            npi_match = bool(provider_npi and str(c.get("provider_npi")) == str(provider_npi).strip())

            if date_match and (amt_match or npi_match):
                return str(c.get("claim_number") or c.get("case_id"))

        return None


