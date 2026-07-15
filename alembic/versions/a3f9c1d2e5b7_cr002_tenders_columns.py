"""cr-002: notice classification + dismiss note columns on tenders

Revision ID: a3f9c1d2e5b7
Revises: 92041be0f186
Create Date: 2026-07-15 21:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f9c1d2e5b7'
down_revision: Union[str, Sequence[str], None] = '92041be0f186'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tenders', sa.Column('dismiss_note', sa.Text(), nullable=True))
    op.add_column('tenders', sa.Column('notice_type', sa.Text(), server_default='tender', nullable=False))
    op.add_column('tenders', sa.Column('awarded_to', sa.Text(), nullable=True))
    op.add_column('tenders', sa.Column('awarded_value', sa.Text(), nullable=True))
    op.add_column('tenders', sa.Column('awarded_currency', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tenders', 'awarded_currency')
    op.drop_column('tenders', 'awarded_value')
    op.drop_column('tenders', 'awarded_to')
    op.drop_column('tenders', 'notice_type')
    op.drop_column('tenders', 'dismiss_note')
