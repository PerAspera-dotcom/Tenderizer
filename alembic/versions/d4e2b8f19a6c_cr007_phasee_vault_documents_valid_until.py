"""cr-007 phase e: add valid_until column to vault_documents

Revision ID: d4e2b8f19a6c
Revises: c1f6a9b4e2d3
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e2b8f19a6c'
down_revision: Union[str, Sequence[str], None] = 'c1f6a9b4e2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('vault_documents', sa.Column('valid_until', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('vault_documents', 'valid_until')
