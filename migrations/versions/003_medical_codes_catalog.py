"""003_medical_codes_catalog

Revision ID: 003_medical_codes_catalog
Revises: 002_policy_chunk_provenance
Create Date: 2026-10-13 22:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_medical_codes_catalog"
down_revision: str | None = "002_policy_chunk_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    coding_system_enum = postgresql.ENUM(
        "CPT", "HCPCS", "ICD10_CM", "REV_CODE", "NDC",
        name="coding_system_enum",
    )
    if is_postgres:
        coding_system_enum.create(bind, checkfirst=True)

    op.create_table(
        "medical_codes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True) if is_postgres else sa.String(36),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()") if is_postgres else None,
        ),
        sa.Column("code", sa.String(32), nullable=False, index=True),
        sa.Column(
            "coding_system",
            coding_system_enum if is_postgres else sa.String(16),
            nullable=False,
            index=True,
        ),
        sa.Column("short_description", sa.String(255), nullable=False),
        sa.Column("long_description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("standard_charge", sa.Numeric(10, 2), nullable=True),
        sa.Column("requires_prior_auth", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("coding_system", "code", name="uq_medical_codes_system_code"),
    )
    op.create_index(
        "idx_medical_codes_active_system",
        "medical_codes",
        ["is_active", "coding_system"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.drop_index("idx_medical_codes_active_system", table_name="medical_codes")
    op.drop_table("medical_codes")

    if is_postgres:
        coding_system_enum = postgresql.ENUM(
            "CPT", "HCPCS", "ICD10_CM", "REV_CODE", "NDC",
            name="coding_system_enum",
        )
        coding_system_enum.drop(bind, checkfirst=True)

