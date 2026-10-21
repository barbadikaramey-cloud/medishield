"""Add verified codes used by the synthetic claim corpus.

Revision ID: 007_verified_catalog_code_coverage
Revises: 006_policy_riders_and_enrollment_applications
Create Date: 2026-10-20

Code existence/descriptors verified against AAPC Codify code listings and CMS ICD-10 resources.
Sources:
- https://www.aapc.com/codes/cpt-codes/59400
- https://www.aapc.com/codes/icd-10-codes/M54.50
- https://www.aapc.com/codes/icd-10-codes/M54.51
- https://www.aapc.com/codes/icd-10-codes/M54.59
- https://www.aapc.com/codes/icd-10-codes/K21.00
- https://www.aapc.com/codes/icd-10-codes/K21.01
- https://www.cms.gov/medicare/coding-billing/icd-10-codes
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007_verified_catalog_code_coverage"
down_revision: str | None = "006_policy_riders_and_enrollment_applications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CODES = (
    ("59400", "CPT", "Obstetric care and vaginal delivery", "Maternity", None),
    ("M54.50", "ICD10_CM", "Low back pain, unspecified", "Dorsopathies", None),
    ("M54.51", "ICD10_CM", "Vertebrogenic low back pain", "Dorsopathies", None),
    ("M54.59", "ICD10_CM", "Other low back pain", "Dorsopathies", None),
    ("K21.00", "ICD10_CM", "GERD with esophagitis, without bleeding", "Digestive system", None),
    ("K21.01", "ICD10_CM", "GERD with esophagitis, with bleeding", "Digestive system", None),
)


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    medical_codes = sa.table(
        "medical_codes",
        sa.column("id", postgresql.UUID(as_uuid=True) if is_postgres else sa.String(36)),
        sa.column("code", sa.String(32)),
        sa.column(
            "coding_system",
            postgresql.ENUM(
                "CPT", "HCPCS", "ICD10_CM", "REV_CODE", "NDC",
                name="coding_system_enum",
                create_type=False,
            ) if is_postgres else sa.String(16),
        ),
        sa.column("short_description", sa.String(255)),
        sa.column("long_description", sa.Text()),
        sa.column("category", sa.String(64)),
        sa.column("standard_charge", sa.Numeric(10, 2)),
        sa.column("requires_prior_auth", sa.Boolean()),
        sa.column("effective_from", sa.Date()),
        sa.column("effective_to", sa.Date()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    for code, coding_system, description, category, requires_prior_auth in CODES:
        exists = bind.execute(
            sa.select(medical_codes.c.id).where(
                medical_codes.c.code == code,
                medical_codes.c.coding_system == coding_system,
            )
        ).first()
        if exists:
            continue
        bind.execute(
            medical_codes.insert().values(
                id=uuid.uuid4() if is_postgres else str(uuid.uuid4()),
                code=code,
                coding_system=coding_system,
                short_description=description,
                long_description=None,
                category=category,
                standard_charge=None,
                requires_prior_auth=requires_prior_auth if requires_prior_auth is not None else False,
                effective_from=None,
                effective_to=None,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    for code, coding_system, *_ in CODES:
        bind.execute(
            sa.text("DELETE FROM medical_codes WHERE code = :code AND coding_system = :coding_system"),
            {"code": code, "coding_system": coding_system},
        )
