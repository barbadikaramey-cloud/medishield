"""004_clinical_guidelines_and_indications

Revision ID: 004_clinical_guidelines_and_indications
Revises: 003_medical_codes_catalog
Create Date: 2026-10-18 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004_clinical_guidelines_and_indications"
down_revision: str | None = "003_medical_codes_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # 1. clinical_guidelines table
    op.create_table(
        "clinical_guidelines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True) if is_postgres else sa.String(36),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()") if is_postgres else None,
        ),
        sa.Column("guideline_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("service_code", sa.String(32), nullable=False, index=True),
        sa.Column("guideline_version", sa.String(32), nullable=False, server_default=sa.text("'2026.1'")),
        sa.Column("guideline_title", sa.String(255), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("requires_prior_auth", sa.Boolean(), nullable=False, server_default=sa.text("false")),
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
    )
    op.create_index(
        "idx_guidelines_service_code",
        "clinical_guidelines",
        ["service_code", "is_active"],
    )

    # 2. guideline_criteria table
    op.create_table(
        "guideline_criteria",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True) if is_postgres else sa.String(36),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()") if is_postgres else None,
        ),
        sa.Column(
            "guideline_id",
            postgresql.UUID(as_uuid=True) if is_postgres else sa.String(36),
            sa.ForeignKey("clinical_guidelines.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("criterion_id", sa.String(64), nullable=False, index=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # 3. diagnosis_indications table
    op.create_table(
        "diagnosis_indications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True) if is_postgres else sa.String(36),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()") if is_postgres else None,
        ),
        sa.Column("service_code", sa.String(32), nullable=False, unique=True, index=True),
        sa.Column("procedure_name", sa.String(255), nullable=False),
        sa.Column("anatomy", sa.String(255), nullable=False),
        sa.Column(
            "valid_icd_prefixes",
            postgresql.JSONB(astext_type=sa.Text()) if is_postgres else sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb") if is_postgres else None,
        ),
        sa.Column(
            "contraindicated_prefixes",
            postgresql.JSONB(astext_type=sa.Text()) if is_postgres else sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb") if is_postgres else None,
        ),
        sa.Column(
            "keywords",
            postgresql.JSONB(astext_type=sa.Text()) if is_postgres else sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb") if is_postgres else None,
        ),
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
    )


def downgrade() -> None:
    op.drop_table("diagnosis_indications")
    op.drop_table("guideline_criteria")
    op.drop_table("clinical_guidelines")

