"""005_clinical_criterion_evidence

Revision ID: 005_clinical_criterion_evidence
Revises: 004_clinical_guidelines_and_indications
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005_clinical_criterion_evidence"
down_revision: str | None = "004_clinical_guidelines_and_indications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    json_type = (
        postgresql.JSONB(astext_type=sa.Text())
        if bind.dialect.name == "postgresql"
        else sa.JSON()
    )

    op.add_column("clinical_guidelines", sa.Column("payer_product", sa.String(64), nullable=True))
    op.add_column("clinical_guidelines", sa.Column("jurisdiction", sa.String(64), nullable=True))
    op.add_column("clinical_guidelines", sa.Column("source_reference", sa.String(512), nullable=True))
    op.add_column("clinical_guidelines", sa.Column("source_chunk_id", sa.String(128), nullable=True))
    op.add_column("clinical_guidelines", sa.Column("source_verified", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.add_column("guideline_criteria", sa.Column("source_reference", sa.String(512), nullable=True))
    op.add_column("guideline_criteria", sa.Column("source_chunk_id", sa.String(128), nullable=True))
    op.add_column("guideline_criteria", sa.Column("source_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("guideline_criteria", sa.Column("requirement_type", sa.String(64), nullable=True))
    op.add_column("guideline_criteria", sa.Column("evaluation_parameters", json_type, nullable=True))
    op.add_column("diagnosis_indications", sa.Column("assertion_patterns", json_type, nullable=True))
    op.add_column("policies", sa.Column("payer_product", sa.String(64), nullable=True))
    op.add_column("policies", sa.Column("jurisdiction", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("guideline_criteria", "evaluation_parameters")
    op.drop_column("guideline_criteria", "requirement_type")
    op.drop_column("guideline_criteria", "source_verified")
    op.drop_column("guideline_criteria", "source_chunk_id")
    op.drop_column("guideline_criteria", "source_reference")
    op.drop_column("diagnosis_indications", "assertion_patterns")
    op.drop_column("policies", "jurisdiction")
    op.drop_column("policies", "payer_product")
    op.drop_column("clinical_guidelines", "source_verified")
    op.drop_column("clinical_guidelines", "source_chunk_id")
    op.drop_column("clinical_guidelines", "source_reference")
    op.drop_column("clinical_guidelines", "jurisdiction")
    op.drop_column("clinical_guidelines", "payer_product")
