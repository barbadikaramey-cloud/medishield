"""Unit tests for Medical Code Catalog Repository and Three-Tier RBAC (ADR-012)."""

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from medishield.api.deps import (
    ALL_ROLES,
    ROLE_ADMIN,
    ROLE_CLAIMER,
    ROLE_MANAGER,
    ROLE_REVIEWER,
    get_current_user,
)
from medishield.db.catalog import (
    InMemoryCodeCatalogRepository,
    SqlCodeCatalogRepository,
)
from medishield.db.enums import CodingSystem, UserRole
from medishield.db.models import Base, MedicalCode


def test_rbac_roles_and_token_resolution():
    """Verify all 3-tier user roles and bearer token resolution (ADR-012)."""
    assert ROLE_CLAIMER in ALL_ROLES
    assert ROLE_REVIEWER in ALL_ROLES
    assert ROLE_MANAGER in ALL_ROLES
    assert ROLE_ADMIN in ALL_ROLES
    assert len(ALL_ROLES) == 4

    assert UserRole.CLAIMER.value == ROLE_CLAIMER
    assert UserRole.ADMIN.value == ROLE_ADMIN

    # Test claimer token
    claimer_user = get_current_user(authorization="Bearer claimer-token")
    assert claimer_user.role == ROLE_CLAIMER
    assert claimer_user.user_id == "claimer-01"

    # Test reviewer token
    rev_user = get_current_user(authorization="Bearer reviewer-token")
    assert rev_user.role == ROLE_REVIEWER

    # Test admin token
    admin_user = get_current_user(authorization="Bearer admin-token")
    assert admin_user.role == ROLE_ADMIN


def test_in_memory_catalog_benchmarks():
    """Verify InMemoryCodeCatalogRepository correctly defaults to benchmark CPT codes."""
    repo = InMemoryCodeCatalogRepository()

    # 1. Canonical benchmark codes are active and valid
    assert repo.is_valid_code("99213", CodingSystem.CPT) is True
    assert repo.is_valid_code("27447", CodingSystem.CPT) is True
    assert repo.is_valid_code("43239", CodingSystem.CPT) is True
    assert repo.is_valid_code("85025", CodingSystem.CPT) is True

    # 2. Unknown code fails
    assert repo.is_valid_code("99999-FAKE", CodingSystem.CPT) is False

    # 3. Code details retrieval
    details = repo.get_code_details("99213", CodingSystem.CPT)
    assert details is not None
    assert isinstance(details, MedicalCode)
    assert details.code == "99213"
    assert details.coding_system == CodingSystem.CPT
    assert details.is_active is True


    # 4. Active codes set
    active_cpts = repo.get_active_codes(CodingSystem.CPT)
    assert "99213" in active_cpts
    assert "27447" in active_cpts
    assert len(active_cpts) >= 18


def test_in_memory_catalog_temporal_validity():
    """Verify effective_from and effective_to dates are respected."""
    repo = InMemoryCodeCatalogRepository(initial_codes=[])

    repo.upsert_code(
        code="99214",
        coding_system=CodingSystem.CPT,
        short_description="Office Visit Expired",
        effective_from=date(2023, 1, 1),
        effective_to=date(2023, 12, 31),
        is_active=True,
    )

    # Valid within 2023
    assert repo.is_valid_code("99214", CodingSystem.CPT, service_date=date(2023, 6, 15)) is True
    # Invalid outside date range
    assert repo.is_valid_code("99214", CodingSystem.CPT, service_date=date(2024, 1, 1)) is False
    assert repo.is_valid_code("99214", CodingSystem.CPT, service_date=date(2022, 12, 31)) is False


def test_sql_code_catalog_repository():
    """Verify SqlCodeCatalogRepository CRUD, search, and bulk operations in SQLite."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()

    repo = SqlCodeCatalogRepository(db=session)

    # 1. Upsert a single code
    code_rec = repo.upsert_code(
        code="99215",
        coding_system=CodingSystem.CPT,
        short_description="Office Outpatient Visit High",
        standard_charge=Decimal("250.00"),
        requires_prior_auth=True,
        is_active=True,
    )
    assert code_rec.code == "99215"
    session.commit()

    # 2. Check validity and details
    assert repo.is_valid_code("99215", CodingSystem.CPT) is True
    assert repo.is_valid_code("00000", CodingSystem.CPT) is False

    details = repo.get_code_details("99215", CodingSystem.CPT)
    assert details is not None
    assert details.standard_charge == Decimal("250.00")
    assert details.requires_prior_auth is True

    # 3. Bulk upsert
    bulk_items = [
        {
            "code": "71045",
            "coding_system": "CPT",
            "short_description": "Chest X-ray Single View",
            "standard_charge": "120.00",
            "is_active": True,
        },
        {
            "code": "J02.9",
            "coding_system": "ICD10_CM",
            "short_description": "Acute pharyngitis unspecified",
            "is_active": True,
        },
    ]
    count = repo.bulk_upsert(bulk_items)
    assert count == 2

    # Check ICD10 code
    assert repo.is_valid_code("J02.9", CodingSystem.ICD10_CM) is True
    assert repo.is_valid_code("71045", CodingSystem.CPT) is True

    # 4. Search
    results = repo.search_codes("Chest")
    assert len(results) == 1
    assert results[0].code == "71045"

    session.close()

