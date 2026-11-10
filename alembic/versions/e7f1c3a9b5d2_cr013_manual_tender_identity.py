"""CR-013 §3: manual tender identity — entry_method/origin/created/deleted
columns on tenders, tenant_origin_sites lookup, manual_tender_counters

Additive. tenant_origin_sites ships empty: its seed list is a separate,
client-confirmed step. Both new tables are created here (not left to
store.init_db's create_all), so a migrations-only build has them.

Revision ID: e7f1c3a9b5d2
Revises: d9a4f2c6b8e1
Create Date: 2026-11-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7f1c3a9b5d2'
down_revision: Union[str, Sequence[str], None] = 'd9a4f2c6b8e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENDER_COLUMNS = ("origin_site_id", "created_by", "created_at", "deleted_at", "deleted_by")


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tenders', sa.Column('entry_method', sa.Text(), nullable=False,
                                       server_default='scraped'))
    op.add_column('tenders', sa.Column('origin_site_id', sa.Integer(), nullable=True))
    op.add_column('tenders', sa.Column('created_by', sa.Text(), nullable=True))
    op.add_column('tenders', sa.Column('created_at', sa.Text(), nullable=True))
    op.add_column('tenders', sa.Column('deleted_at', sa.Text(), nullable=True))
    op.add_column('tenders', sa.Column('deleted_by', sa.Text(), nullable=True))

    op.create_table(
        'tenant_origin_sites',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('name_key', sa.Text(), nullable=False),
        sa.Column('domain', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_by', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=True),
        sa.UniqueConstraint('tenant_id', 'name_key', name='uq_tenant_origin_sites_name_key'),
    )
    op.create_table(
        'manual_tender_counters',
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('last_value', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('tenant_id', 'year'),
    )


def downgrade() -> None:
    """Downgrade schema. Destroys any manual-entry data — like any drop."""
    op.drop_table('manual_tender_counters')
    op.drop_table('tenant_origin_sites')
    for col in reversed(_TENDER_COLUMNS):
        op.drop_column('tenders', col)
    op.drop_column('tenders', 'entry_method')
