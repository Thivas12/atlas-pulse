"""Index the geography expression used by cross-source correlation.

Revision ID: 20260921_0025
Revises: 20260914_0024
Create Date: 2026-09-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260921_0025"
down_revision: str | None = "20260914_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Let ST_DWithin prune correlation candidates through a GiST index."""
    op.execute(
        """
        CREATE INDEX ix_event_revisions_evidence_geography_gist
        ON event_revisions
        USING gist ((COALESCE(footprint, point)::geography))
        """
    )


def downgrade() -> None:
    """Remove the correlation-specific geography expression index."""
    op.execute("DROP INDEX ix_event_revisions_evidence_geography_gist")
