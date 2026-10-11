"""001_initial_schema

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-10-02 16:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0. PostgreSQL Extensions
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')

    # 1. Enums
    workflow_state_enum = postgresql.ENUM(
        'RECEIVED', 'SCANNING', 'SPLIT', 'CLASSIFYING',
        'ANALYZING', 'AWAITING_INFO', 'AWAITING_HUMAN',
        'COMPLETED', 'FAILED',
        name='workflow_state_enum'
    )
    workflow_state_enum.create(op.get_bind(), checkfirst=True)

    business_decision_enum = postgresql.ENUM(
        'APPROVE', 'REJECT', 'ESCALATE',
        name='business_decision_enum'
    )
    business_decision_enum.create(op.get_bind(), checkfirst=True)

    escalation_sub_reason_enum = postgresql.ENUM(
        'MISSING_INFORMATION', 'HUMAN_REVIEW', 'FRAUD_REVIEW',
        'SECURITY_REVIEW', 'TECHNICAL_FAILURE', 'AMBIGUITY',
        name='escalation_sub_reason_enum'
    )
    escalation_sub_reason_enum.create(op.get_bind(), checkfirst=True)

    agent_verdict_enum = postgresql.ENUM(
        'PASS', 'FAIL', 'INDETERMINATE', 'CONFLICT', 'UNAVAILABLE',
        name='agent_verdict_enum'
    )
    agent_verdict_enum.create(op.get_bind(), checkfirst=True)

    document_type_enum = postgresql.ENUM(
        'CLAIM_FORM', 'ID_DOCUMENT', 'DISCHARGE_SUMMARY',
        'PRESCRIPTION', 'POLICY_AMENDMENT', 'UNKNOWN',
        name='document_type_enum'
    )
    document_type_enum.create(op.get_bind(), checkfirst=True)

    actor_type_enum = postgresql.ENUM(
        'SYSTEM', 'HUMAN',
        name='actor_type_enum'
    )
    actor_type_enum.create(op.get_bind(), checkfirst=True)

    # 2. cases
    op.create_table(
        'cases',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), primary_key=True),
        sa.Column('case_id', sa.String(32), nullable=False, unique=True),
        sa.Column('workflow_state', workflow_state_enum, server_default='RECEIVED', nullable=False),
        sa.Column('workflow_version', sa.String(16), server_default='v1.0.0', nullable=False),
        sa.Column('current_decision', business_decision_enum, nullable=True),
        sa.Column('current_sub_reason', escalation_sub_reason_enum, nullable=True),
        sa.Column('decision_confidence', sa.Numeric(4, 3), nullable=True),
        sa.Column('is_complete', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('missing_documents', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    )
    op.create_index('idx_cases_state', 'cases', ['workflow_state'])
    op.create_index('idx_cases_created', 'cases', [sa.text('created_at DESC')])

    # 3. documents
    op.create_table(
        'documents',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), primary_key=True),
        sa.Column('document_id', sa.String(36), nullable=False, unique=True),
        sa.Column('case_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('cases.id', ondelete='CASCADE'), nullable=False),
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('object_key', sa.Text(), nullable=False),
        sa.Column('mime_type', sa.String(64), nullable=False),
        sa.Column('checksum', sa.String(64), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('doc_type', document_type_enum, server_default='UNKNOWN', nullable=False),
        sa.Column('classification_confidence', sa.Numeric(4, 3), nullable=True),
        sa.Column('ocr_payload_uri', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    )
    op.create_index('idx_documents_case', 'documents', ['case_id'])

    # 4. document_pages
    op.create_table(
        'document_pages',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), primary_key=True),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('page_number', sa.Integer(), nullable=False),
        sa.Column('object_key', sa.Text(), nullable=False),
        sa.Column('width_px', sa.Integer(), nullable=False),
        sa.Column('height_px', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.UniqueConstraint('document_id', 'page_number', name='uq_document_pages_page')
    )

    # 5. members
    op.create_table(
        'members',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), primary_key=True),
        sa.Column('member_id', sa.String(32), nullable=False, unique=True),
        sa.Column('first_name', sa.String(64), nullable=False),
        sa.Column('last_name', sa.String(64), nullable=False),
        sa.Column('date_of_birth', sa.Date(), nullable=False),
        sa.Column('ssn_last4', sa.String(4), nullable=True),
        sa.Column('status', sa.String(16), server_default='ACTIVE', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    )

    # 6. policies
    op.create_table(
        'policies',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), primary_key=True),
        sa.Column('policy_id', sa.String(32), nullable=False, unique=True),
        sa.Column('plan_name', sa.String(128), nullable=False),
        sa.Column('policy_type', sa.String(32), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    )

    # 7. policy_versions
    op.create_table(
        'policy_versions',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), primary_key=True),
        sa.Column('policy_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('policies.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('version_tag', sa.String(16), nullable=False),
        sa.Column('effective_start_date', sa.Date(), nullable=False),
        sa.Column('effective_end_date', sa.Date(), nullable=True),
        sa.Column('source_pdf_key', sa.Text(), nullable=False),
        sa.Column('vector_collection_name', sa.String(64), nullable=False),
        sa.Column('is_published', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('published_by', sa.String(64), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.UniqueConstraint('policy_id', 'version_tag', name='uq_policy_version_tag')
    )

    # 8. enrollments
    op.create_table(
        'enrollments',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), primary_key=True),
        sa.Column('member_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('members.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('policy_version_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('policy_versions.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('coverage_start_date', sa.Date(), nullable=False),
        sa.Column('coverage_end_date', sa.Date(), nullable=True),
        sa.Column('status', sa.String(16), server_default='ACTIVE', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    )

    # 9. policy_chunks
    op.create_table(
        'policy_chunks',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), primary_key=True),
        sa.Column('policy_version_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('policy_versions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('section_number', sa.String(32), nullable=True),
        sa.Column('section_title', sa.String(255), nullable=True),
        sa.Column('chunk_text', sa.Text(), nullable=False),
        sa.Column('page_number', sa.Integer(), nullable=False),
        sa.Column('vector_id', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    )

    # 10. claims
    op.create_table(
        'claims',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), primary_key=True),
        sa.Column('case_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('cases.id', ondelete='CASCADE'), unique=True, nullable=False),
        sa.Column('claim_number', sa.String(32), nullable=False, unique=True),
        sa.Column('member_id', sa.String(32), nullable=True),
        sa.Column('provider_npi', sa.String(10), nullable=False),
        sa.Column('service_date', sa.Date(), nullable=False),
        sa.Column('total_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('icd10_codes', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('cpt_codes', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('schema_valid', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('validation_errors', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    )

    # 11. claim_lines
    op.create_table(
        'claim_lines',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), primary_key=True),
        sa.Column('claim_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('claims.id', ondelete='CASCADE'), nullable=False),
        sa.Column('line_number', sa.Integer(), nullable=False),
        sa.Column('cpt_code', sa.String(16), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('service_date', sa.Date(), nullable=False),
        sa.Column('quantity', sa.Numeric(6, 2), server_default='1.0', nullable=False),
        sa.Column('unit_charge', sa.Numeric(10, 2), nullable=False),
        sa.Column('total_charge', sa.Numeric(10, 2), nullable=False),
        sa.Column('provenance_span_id', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.UniqueConstraint('claim_id', 'line_number', name='uq_claim_lines_line_number')
    )

    # 12. agent_runs
    op.create_table(
        'agent_runs',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), primary_key=True),
        sa.Column('case_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('cases.id', ondelete='CASCADE'), nullable=False),
        sa.Column('execution_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('agent_name', sa.String(64), nullable=False),
        sa.Column('verdict', agent_verdict_enum, nullable=False),
        sa.Column('confidence', sa.Numeric(4, 3), nullable=False),
        sa.Column('output_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('error_code', sa.String(64), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('idx_agent_runs_case', 'agent_runs', ['case_id'])

    # 13. decisions
    op.create_table(
        'decisions',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), primary_key=True),
        sa.Column('case_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('cases.id', ondelete='CASCADE'), nullable=False),
        sa.Column('decision', business_decision_enum, nullable=False),
        sa.Column('escalation_sub_reason', escalation_sub_reason_enum, nullable=True),
        sa.Column('actor_type', actor_type_enum, nullable=False),
        sa.Column('actor_id', sa.String(64), nullable=False),
        sa.Column('decision_confidence', sa.Numeric(4, 3), nullable=False),
        sa.Column('rule_id_matched', sa.String(64), nullable=False),
        sa.Column('justification', sa.Text(), nullable=False),
        sa.Column('agent_summary_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('case_version_at_decision', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    )
    op.create_index('idx_decisions_case_active', 'decisions', ['case_id', 'is_active'])

    # 14. review_locks
    op.create_table(
        'review_locks',
        sa.Column('case_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('cases.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('reviewer_id', sa.String(64), nullable=False),
        sa.Column('locked_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    )

    # 15. human_reviews
    op.create_table(
        'human_reviews',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), primary_key=True),
        sa.Column('case_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('cases.id', ondelete='CASCADE'), nullable=False),
        sa.Column('reviewer_id', sa.String(64), nullable=False),
        sa.Column('action', sa.String(32), nullable=False),
        sa.Column('new_decision', business_decision_enum, nullable=True),
        sa.Column('reason_code', sa.String(64), nullable=False),
        sa.Column('remarks', sa.Text(), nullable=False),
        sa.Column('expected_case_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    )

    # 16. audit_events
    op.create_table(
        'audit_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('uuid_generate_v4()'), primary_key=True),
        sa.Column('case_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('cases.id', ondelete='CASCADE'), nullable=False),
        sa.Column('execution_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('event_type', sa.String(64), nullable=False),
        sa.Column('actor_type', actor_type_enum, nullable=False),
        sa.Column('actor_id', sa.String(64), nullable=False),
        sa.Column('event_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('prev_hash', sa.String(64), nullable=False),
        sa.Column('current_hash', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    )
    op.create_index('idx_audit_case', 'audit_events', ['case_id', sa.text('created_at ASC')])

    # 17. idempotency_keys
    op.create_table(
        'idempotency_keys',
        sa.Column('key', sa.String(128), primary_key=True),
        sa.Column('case_id', sa.String(32), nullable=False),
        sa.Column('response_code', sa.Integer(), nullable=False),
        sa.Column('response_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    # Drop tables in reverse order of dependencies
    op.drop_table('idempotency_keys')
    op.drop_index('idx_audit_case', table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_table('human_reviews')
    op.drop_table('review_locks')
    op.drop_index('idx_decisions_case_active', table_name='decisions')
    op.drop_table('decisions')
    op.drop_index('idx_agent_runs_case', table_name='agent_runs')
    op.drop_table('agent_runs')
    op.drop_table('claim_lines')
    op.drop_table('claims')
    op.drop_table('policy_chunks')
    op.drop_table('enrollments')
    op.drop_table('policy_versions')
    op.drop_table('policies')
    op.drop_table('members')
    op.drop_table('document_pages')
    op.drop_index('idx_documents_case', table_name='documents')
    op.drop_table('documents')
    op.drop_index('idx_cases_created', table_name='cases')
    op.drop_index('idx_cases_state', table_name='cases')
    op.drop_table('cases')

    # Drop enums
    op.execute('DROP TYPE IF EXISTS actor_type_enum CASCADE;')
    op.execute('DROP TYPE IF EXISTS document_type_enum CASCADE;')
    op.execute('DROP TYPE IF EXISTS agent_verdict_enum CASCADE;')
    op.execute('DROP TYPE IF EXISTS escalation_sub_reason_enum CASCADE;')
    op.execute('DROP TYPE IF EXISTS business_decision_enum CASCADE;')
    op.execute('DROP TYPE IF EXISTS workflow_state_enum CASCADE;')
