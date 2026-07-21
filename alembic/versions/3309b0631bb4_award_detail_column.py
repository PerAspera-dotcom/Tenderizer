"""past-tenders data-coverage follow-up: award_detail column on tenders

Revision ID: 3309b0631bb4
Revises: a3f9c1d2e5b7
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3309b0631bb4'
down_revision: Union[str, Sequence[str], None] = 'a3f9c1d2e5b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tenders', sa.Column('award_detail', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tenders', 'award_detail')
