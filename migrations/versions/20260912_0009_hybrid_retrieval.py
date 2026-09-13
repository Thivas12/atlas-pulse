"""Add independent PostgreSQL FTS and pgvector retrieval projection.

Revision ID: 20260912_0009
Revises: 20260912_0004
Create Date: 2026-09-12 23:30:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260912_0009"
down_revision: str | None = "20260912_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the current retrieval index and its independent access paths."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE retrieval_documents (
            source varchar(64) NOT NULL,
            event_id varchar(200) NOT NULL,
            stream_id varchar(64) NOT NULL UNIQUE,
            stream_ms bigint NOT NULL,
            stream_seq bigint NOT NULL,
            occurred_at timestamptz NOT NULL,
            ingested_at timestamptz NOT NULL,
            expires_at timestamptz,
            event_json jsonb NOT NULL,
            document_text text NOT NULL,
            document_hash char(64) NOT NULL,
            embedding_model varchar(200) NOT NULL,
            embedding vector(384) NOT NULL,
            point geometry(Point, 4326),
            footprint geometry(Geometry, 4326),
            text_search tsvector GENERATED ALWAYS AS (
                to_tsvector('english', document_text)
            ) STORED,
            PRIMARY KEY (source, event_id),
            CONSTRAINT ck_retrieval_document_hash_hex
                CHECK (document_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_retrieval_footprint_type
                CHECK (
                    footprint IS NULL
                    OR GeometryType(footprint) IN ('POLYGON', 'MULTIPOLYGON')
                )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_retrieval_documents_text_search "
        "ON retrieval_documents USING gin (text_search)"
    )
    op.execute(
        "CREATE INDEX ix_retrieval_documents_embedding_hnsw "
        "ON retrieval_documents USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )
    op.execute(
        "CREATE INDEX ix_retrieval_documents_point "
        "ON retrieval_documents USING gist (point) WHERE point IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_retrieval_documents_footprint "
        "ON retrieval_documents USING gist (footprint) WHERE footprint IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_retrieval_documents_occurred ON retrieval_documents (occurred_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_retrieval_documents_source_occurred "
        "ON retrieval_documents (source, occurred_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_retrieval_documents_expires "
        "ON retrieval_documents (expires_at) WHERE expires_at IS NOT NULL"
    )


def downgrade() -> None:
    """Remove retrieval data while leaving shared extensions installed."""
    op.execute("DROP TABLE retrieval_documents")
