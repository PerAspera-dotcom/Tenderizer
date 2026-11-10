"""CR-013 §5: manual_tender_drafts staging table for the PDF entry path

Additive. Created here (not left to store.init_db's create_all), so a
migrations-only build has it.

Revision ID: f3b9d2e6a1c4
Revises: e7f1c3a9b5d2
Create Date: 2026-11-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3b9d2e6a1c4'
down_revision: Union[str, Sequence[str], None] = 'e7f1c3a9b5d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'manual_tender_drafts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='uploading'),
        sa.Column('files', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('extracted_text', sa.Text(), nullable=True),
        sa.Column('extraction', sa.Text(), nullable=True),
        sa.Column('model', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=''),
        sa.Column('updated_at', sa.Text(), nullable=False, server_default=''),
        sa.Column('confirmed_by', sa.Text(), nullable=True),
        sa.Column('confirmed_at', sa.Text(), nullable=True),
        sa.Column('confirmed_pub_number', sa.Text(), nullable=True),
        sa.Column('confirmed_values', sa.Text(), nullable=True),
    )
    op.create_index('ix_manual_tender_drafts_tenant_status', 'manual_tender_drafts',
                    ['tenant_id', 'status'])


def downgrade() -> None:
    """Downgrade schema. Destroys draft history — like any drop."""
    op.drop_index('ix_manual_tender_drafts_tenant_status', table_name='manual_tender_drafts')
    op.drop_table('manual_tender_drafts')
