"""duplicate resolution: add resolved_by/resolved_at to tender_duplicates

Revision ID: b3d8f1a4c7e9
Revises: a7c2e4f9b1d6
Create Date: 2026-08-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3d8f1a4c7e9'
down_revision: Union[str, Sequence[str], None] = 'a7c2e4f9b1d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tender_duplicates', sa.Column('resolved_by', sa.Text(), nullable=True))
    op.add_column('tender_duplicates', sa.Column('resolved_at', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tender_duplicates', 'resolved_at')
    op.drop_column('tender_duplicates', 'resolved_by')
