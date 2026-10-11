"""add stable policy-chunk provenance metadata

Revision ID: 002_policy_chunk_provenance
Revises: 001_initial_schema
"""

from alembic import op
import sqlalchemy as sa

revision = "002_policy_chunk_provenance"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("policy_chunks", sa.Column("chunk_hash", sa.String(64), nullable=False, server_default=""))
    op.add_column("policy_chunks", sa.Column("stable_chunk_id", sa.String(128), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("policy_chunks", "stable_chunk_id")
    op.drop_column("policy_chunks", "chunk_hash")
