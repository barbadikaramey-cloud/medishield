"""Authoritative Medical Code & Terminology Catalog Repository (ADR-012).

Provides dynamic database and caching abstractions for clinical codes (CPT, HCPCS,
ICD-10-CM, Revenue) to support scale-out terminology management (100,000+ codes)
while preserving deterministic fallback for offline testing.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from medishield.db.enums import CodingSystem
from medishield.db.models import MedicalCode
from medishield.ocr.ocr_reconcile import SYNTHETIC_VALID_CPT_CODES


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

    def __init__(self, db: Session, cache_ttl_seconds: int = 3600) -> None:
        self.db = db
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cached_active_sets: dict[CodingSystem, frozenset[str]] = {}

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
        record = self.db.scalars(stmt).first()
        return record is not None

    def get_code_details(
        self,
        code: str,
        coding_system: CodingSystem = CodingSystem.CPT,
    ) -> MedicalCode | None:
        clean_code = code.strip().upper()
        stmt = select(MedicalCode).where(
            MedicalCode.code == clean_code,
            MedicalCode.coding_system == coding_system,
        )
        return self.db.scalars(stmt).first()

    def search_codes(
        self,
        query: str,
        coding_system: CodingSystem | None = None,
        limit: int = 20,
    ) -> list[MedicalCode]:
        q = f"%{query.strip().upper()}%"
        stmt = select(MedicalCode).where(
            or_(
                MedicalCode.code.ilike(q),
                MedicalCode.short_description.ilike(q),
            )
        )
        if coding_system is not None:
            stmt = stmt.where(MedicalCode.coding_system == coding_system)
        stmt = stmt.order_by(MedicalCode.code.asc()).limit(limit)
        return list(self.db.scalars(stmt).all())

    def get_active_codes(
        self,
        coding_system: CodingSystem = CodingSystem.CPT,
    ) -> frozenset[str]:
        if coding_system in self._cached_active_sets:
            return self._cached_active_sets[coding_system]
        stmt = select(MedicalCode.code).where(
            MedicalCode.coding_system == coding_system,
            MedicalCode.is_active.is_(True),
        )
        codes = frozenset(self.db.scalars(stmt).all())
        self._cached_active_sets[coding_system] = codes
        return codes

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
        record = self.get_code_details(clean_code, coding_system)
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
            self.db.add(record)
        self.db.flush()
        # Invalidate active set cache
        self._cached_active_sets.pop(coding_system, None)
        return record

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
        self.db.commit()
        for s in systems_affected:
            self._cached_active_sets.pop(s, None)
        return count


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
        for cpt in SYNTHETIC_VALID_CPT_CODES:
            self._insert_from_dict({
                "code": cpt,
                "coding_system": CodingSystem.CPT,
                "short_description": f"Benchmark Procedure {cpt}",
                "category": "Benchmark Evaluation",
                "is_active": True,
            })
        # Seed additional common benchmark procedure codes
        extra_benchmarks = ["27447", "43239", "71250", "29827", "99284", "99291", "70553", "72148", "33533"]
        for cpt in extra_benchmarks:
            self._insert_from_dict({
                "code": cpt,
                "coding_system": CodingSystem.CPT,
                "short_description": f"Benchmark Surgical/Diagnostic {cpt}",
                "category": "Benchmark Evaluation",
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

