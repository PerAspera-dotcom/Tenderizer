"""cr-006: rename dismiss_note -> dismissal_reason, add dismissed_by/dismissed_at

Revision ID: 7c4a9e1f2d8b
Revises: 5dce3f225121
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c4a9e1f2d8b'
down_revision: Union[str, Sequence[str], None] = '5dce3f225121'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('tenders', 'dismiss_note', new_column_name='dismissal_reason',
                     existing_type=sa.Text(), existing_nullable=True)
    op.add_column('tenders', sa.Column('dismissed_by', sa.Text(), nullable=True))
    op.add_column('tenders', sa.Column('dismissed_at', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tenders', 'dismissed_at')
    op.drop_column('tenders', 'dismissed_by')
    op.alter_column('tenders', 'dismissal_reason', new_column_name='dismiss_note',
                     existing_type=sa.Text(), existing_nullable=True)
