"""CR-013 §2: object_key column on every table that stores an uploaded file

Additive and nullable: existing rows keep their storage_path and read from
it until `cli storage-migrate` assigns each one a key. composer_matrix's
filled_path is reused (it now holds a key for new rows) rather than adding a
second column for it.

Revision ID: d9a4f2c6b8e1
Revises: c8e2a5d7f3b1
Create Date: 2026-11-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9a4f2c6b8e1'
down_revision: Union[str, Sequence[str], None] = 'c8e2a5d7f3b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("documents", "vault_documents", "composer_documents", "composer_matrix",
          "composer_style_examples")


def upgrade() -> None:
    """Upgrade schema."""
    for table in TABLES:
        op.add_column(table, sa.Column('object_key', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column('object_key')
