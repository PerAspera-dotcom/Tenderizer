"""add assigned_to column to tender_reviews

Revision ID: f6a1c9d3e7b2
Revises: e5f3c7a2b8d1
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a1c9d3e7b2'
down_revision: Union[str, Sequence[str], None] = 'e5f3c7a2b8d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tender_reviews', sa.Column('assigned_to', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tender_reviews', 'assigned_to')
