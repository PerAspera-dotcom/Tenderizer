"""add notice_detail column to tenders

Revision ID: c8e2a5d7f3b1
Revises: b3d8f1a4c7e9
Create Date: 2026-10-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8e2a5d7f3b1'
down_revision: Union[str, Sequence[str], None] = 'b3d8f1a4c7e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tenders', sa.Column('notice_detail', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tenders', 'notice_detail')
