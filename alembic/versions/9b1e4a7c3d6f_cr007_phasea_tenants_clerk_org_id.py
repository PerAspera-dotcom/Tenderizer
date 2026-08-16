"""cr-007 phase a: add clerk_org_id column to tenants

Revision ID: 9b1e4a7c3d6f
Revises: 7c4a9e1f2d8b
Create Date: 2026-08-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b1e4a7c3d6f'
down_revision: Union[str, Sequence[str], None] = '7c4a9e1f2d8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tenants', sa.Column('clerk_org_id', sa.String(), nullable=True))
    op.create_unique_constraint('uq_tenants_clerk_org_id', 'tenants', ['clerk_org_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_tenants_clerk_org_id', 'tenants', type_='unique')
    op.drop_column('tenants', 'clerk_org_id')
