"""Create immutable revisions and current-signal projection.

Revision ID: 20260912_0004
Revises:
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision: str = "20260912_0004"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Install PostGIS and create the durable projection schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "event_revisions",
        sa.Column("stream_id", sa.String(length=64), primary_key=True),
        sa.Column("stream_ms", sa.BigInteger(), nullable=False),
        sa.Column("stream_seq", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("point", Geometry("POINT", srid=4326, spatial_index=False), nullable=True),
        sa.Column(
            "footprint",
            Geometry("GEOMETRY", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column("severity_rank", sa.SmallInteger(), nullable=True),
        sa.Column("magnitude", sa.Float(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("place", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "severity_rank IS NULL OR severity_rank BETWEEN 0 AND 4",
            name="ck_event_revisions_severity_rank",
        ),
        sa.UniqueConstraint(
            "stream_id",
            "source",
            "event_id",
            name="uq_event_revisions_stream_identity",
        ),
    )
    op.create_index(
        "ix_event_revisions_identity",
        "event_revisions",
        ["source", "event_id", "stream_ms", "stream_seq"],
    )
    op.create_index("ix_event_revisions_occurred_at", "event_revisions", ["occurred_at"])
    op.create_index("ix_event_revisions_severity_rank", "event_revisions", ["severity_rank"])
    op.create_index("ix_event_revisions_expires_at", "event_revisions", ["expires_at"])
    op.create_index(
        "ix_event_revisions_point_gist",
        "event_revisions",
        ["point"],
        postgresql_using="gist",
    )
    op.create_index(
        "ix_event_revisions_footprint_gist",
        "event_revisions",
        ["footprint"],
        postgresql_using="gist",
    )

    op.create_table(
        "current_signals",
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("stream_id", sa.String(length=64), nullable=False),
        sa.Column("stream_ms", sa.BigInteger(), nullable=False),
        sa.Column("stream_seq", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["stream_id", "source", "event_id"],
            [
                "event_revisions.stream_id",
                "event_revisions.source",
                "event_revisions.event_id",
            ],
            name="fk_current_signals_revision_identity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("source", "event_id", name="pk_current_signals"),
    )
    op.create_index(
        "ix_current_signals_stream_position",
        "current_signals",
        ["stream_ms", "stream_seq"],
    )
    op.create_index(
        "ix_current_signals_source_stream_position",
        "current_signals",
        ["source", "stream_ms", "stream_seq"],
    )

    op.create_table(
        "projection_checkpoints",
        sa.Column("projection_name", sa.String(length=128), primary_key=True),
        sa.Column("last_stream_id", sa.String(length=64), nullable=False),
        sa.Column("last_stream_ms", sa.BigInteger(), nullable=False),
        sa.Column("last_stream_seq", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    """Remove projection tables while leaving the shared PostGIS extension installed."""
    op.drop_table("projection_checkpoints")
    op.drop_table("current_signals")
    op.drop_table("event_revisions")
