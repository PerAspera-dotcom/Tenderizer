"""cr-007 phase f: add source_verified column to composer_requirements

Revision ID: e5f3c7a2b8d1
Revises: d4e2b8f19a6c
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f3c7a2b8d1'
down_revision: Union[str, Sequence[str], None] = 'd4e2b8f19a6c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('composer_requirements', sa.Column('source_verified', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('composer_requirements', 'source_verified')
