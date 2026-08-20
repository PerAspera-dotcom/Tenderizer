"""org-shared review state: add reason_category/assigned_to to tenders,
backfill from tender_reviews (no longer written going forward)

Revision ID: a7c2e4f9b1d6
Revises: f6a1c9d3e7b2
Create Date: 2026-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c2e4f9b1d6'
down_revision: Union[str, Sequence[str], None] = 'f6a1c9d3e7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tenders', sa.Column('reason_category', sa.Text(), nullable=True))
    op.add_column('tenders', sa.Column('assigned_to', sa.Text(), nullable=True))

    # Backfill: each tender's most-recently-updated tender_reviews row (across
    # every org member who ever acted on it) becomes the tender's one shared
    # state — only where the shared row hasn't already been decided
    # ("shortlisted" stays as-is; "new" is the only overwritable starting
    # point). dismissed_by only gets stamped for an actual dismiss stage, same
    # rule store.set_status enforces going forward. Deliberately does NOT drop
    # tender_reviews itself — see schema.py's retirement comment.
    op.execute(sa.text("""
        WITH latest AS (
            SELECT DISTINCT ON (tenant_id, pub_number)
                   tenant_id, pub_number, status, reason, reason_category,
                   dismissed_at, assigned_to, account_name
            FROM tender_reviews
            ORDER BY tenant_id, pub_number, updated_at DESC
        )
        UPDATE tenders t
        SET status = latest.status,
            dismissal_reason = latest.reason,
            reason_category = latest.reason_category,
            dismissed_at = latest.dismissed_at,
            dismissed_by = CASE WHEN latest.status IN ('dismissed', 'dismissed_final')
                                 THEN latest.account_name ELSE t.dismissed_by END,
            assigned_to = latest.assigned_to
        FROM latest
        WHERE t.tenant_id = latest.tenant_id
          AND t.pub_number = latest.pub_number
          AND t.status = 'new'
    """))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tenders', 'assigned_to')
    op.drop_column('tenders', 'reason_category')
