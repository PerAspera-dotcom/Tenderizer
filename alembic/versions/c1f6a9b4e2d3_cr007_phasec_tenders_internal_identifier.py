"""cr-007 phase c: add internal_identifier column to tenders

Revision ID: c1f6a9b4e2d3
Revises: 9b1e4a7c3d6f
Create Date: 2026-08-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1f6a9b4e2d3'
down_revision: Union[str, Sequence[str], None] = '9b1e4a7c3d6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tenders', sa.Column('internal_identifier', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tenders', 'internal_identifier')
