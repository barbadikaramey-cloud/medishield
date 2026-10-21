"""Persist policy rider terms and member rider applications.

Revision ID: 006_policy_riders_and_enrollment_applications
Revises: 005_clinical_criterion_evidence
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006_policy_riders_and_enrollment_applications"
down_revision: str | None = "005_clinical_criterion_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "policy_riders",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "policy_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("policy_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rider_code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("waiting_period_value", sa.Integer(), nullable=False),
        sa.Column("waiting_period_unit", sa.String(16), nullable=False),
        sa.Column("waiting_period_start_event", sa.String(64), nullable=False),
        sa.Column("benefit_limit_text", sa.String(256), nullable=True),
        sa.Column("covered_services", sa.JSON(), nullable=False),
        sa.Column("exclusions", sa.JSON(), nullable=False),
        sa.Column("source_section", sa.String(32), nullable=False),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_pdf_key", sa.String(512), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column(
            "source_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint("waiting_period_value >= 0", name="ck_policy_rider_wait_value"),
        sa.CheckConstraint(
            "waiting_period_unit IN ('DAY', 'MONTH')",
            name="ck_policy_rider_wait_unit",
        ),
        sa.UniqueConstraint(
            "policy_version_id", "rider_code", name="uq_policy_rider_version_code"
        ),
    )
    op.create_index(
        "ix_policy_riders_version", "policy_riders", ["policy_version_id"]
    )

    op.create_table(
        "enrollment_rider_applications",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "enrollment_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("enrollments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "policy_rider_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("policy_riders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="REQUESTED"
        ),
        sa.Column("requested_on", sa.Date(), nullable=True),
        sa.Column("requested_effective_date", sa.Date(), nullable=True),
        sa.Column("effective_start_date", sa.Date(), nullable=True),
        sa.Column("effective_end_date", sa.Date(), nullable=True),
        sa.Column("source_document_ref", sa.String(128), nullable=True),
        sa.Column("approval_reference", sa.String(128), nullable=True),
        sa.Column("approved_by", sa.String(64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint(
            "status IN ('REQUESTED', 'APPROVED', 'DENIED', 'WITHDRAWN')",
            name="ck_enrollment_rider_application_status",
        ),
        sa.CheckConstraint(
            "status != 'APPROVED' OR "
            "(approved_by IS NOT NULL AND approved_at IS NOT NULL "
            "AND effective_start_date IS NOT NULL)",
            name="ck_approved_rider_has_authorization",
        ),
        sa.UniqueConstraint("source_document_ref", name="uq_rider_application_source"),
    )
    op.create_index(
        "ix_enrollment_rider_applications_enrollment",
        "enrollment_rider_applications",
        ["enrollment_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_enrollment_rider_applications_enrollment",
        table_name="enrollment_rider_applications",
    )
    op.drop_table("enrollment_rider_applications")
    op.drop_index("ix_policy_riders_version", table_name="policy_riders")
    op.drop_table("policy_riders")