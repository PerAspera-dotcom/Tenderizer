"""vault documents tags column

Revision ID: 5dce3f225121
Revises: 3309b0631bb4
Create Date: 2026-07-22 15:33:18.072479

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5dce3f225121'
down_revision: Union[str, Sequence[str], None] = '3309b0631bb4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('vault_documents', sa.Column('tags', sa.Text(), nullable=False, server_default='[]'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('vault_documents', 'tags')
