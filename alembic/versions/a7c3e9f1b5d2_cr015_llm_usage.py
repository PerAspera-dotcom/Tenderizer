"""CR-015: llm_usage_months + llm_usage, the Claude spend guard's counters

Additive: two new tables, nothing else touched. The gateway (src/llm.py)
refuses every Claude call while these tables are missing, so run this
before the build that ships the gateway.

Revision ID: a7c3e9f1b5d2
Revises: f3b9d2e6a1c4
Create Date: 2026-11-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e9f1b5d2'
down_revision: Union[str, Sequence[str], None] = 'f3b9d2e6a1c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'llm_usage_months',
        sa.Column('month', sa.Text(), primary_key=True),
        sa.Column('calls', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_table(
        'llm_usage',
        sa.Column('month', sa.Text(), nullable=False),
        sa.Column('feature', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('calls', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('output_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('month', 'feature', 'model'),
    )


def downgrade() -> None:
    """Downgrade schema. Loses the month's count — the gateway then refuses
    every call until the tables are back."""
    op.drop_table('llm_usage')
    op.drop_table('llm_usage_months')
