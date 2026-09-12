"""Atomic PostgreSQL full-text and pgvector retrieval projection."""

from collections.abc import Mapping
from decimal import Decimal
from typing import cast

from agent_rag_core import Event
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from atlas_pulse.projections.postgres import parse_stream_id, projection_values
from atlas_pulse.retrieval.base import (
    CandidateBatch,
    ChannelCandidate,
    Embedding,
    IndexedDocument,
    SearchQuery,
)
from atlas_pulse.streams import StreamMessage

_UPSERT_DOCUMENT = """
INSERT INTO retrieval_documents (
    source,
    event_id,
    stream_id,
    stream_ms,
    stream_seq,
    occurred_at,
    ingested_at,
    expires_at,
    event_json,
    document_text,
    document_hash,
    embedding_model,
    embedding,
    point,
    footprint
) VALUES (
    :source,
    :event_id,
    :stream_id,
    :stream_ms,
    :stream_seq,
    :occurred_at,
    :ingested_at,
    :expires_at,
    CAST(:event_json AS jsonb),
    :document_text,
    :document_hash,
    :embedding_model,
    CAST(:embedding AS vector),
    CASE
        WHEN CAST(:longitude AS double precision) IS NULL THEN NULL
        ELSE ST_SetSRID(
            ST_MakePoint(
                CAST(:longitude AS double precision),
                CAST(:latitude AS double precision)
            ),
            4326
        )
    END,
    CASE
        WHEN CAST(:footprint_json AS text) IS NULL THEN NULL
        ELSE ST_SetSRID(ST_GeomFromGeoJSON(CAST(:footprint_json AS text)), 4326)
    END
)
ON CONFLICT (source, event_id) DO UPDATE SET
    stream_id = EXCLUDED.stream_id,
    stream_ms = EXCLUDED.stream_ms,
    stream_seq = EXCLUDED.stream_seq,
    occurred_at = EXCLUDED.occurred_at,
    ingested_at = EXCLUDED.ingested_at,
    expires_at = EXCLUDED.expires_at,
    event_json = EXCLUDED.event_json,
    document_text = EXCLUDED.document_text,
    document_hash = EXCLUDED.document_hash,
    embedding_model = EXCLUDED.embedding_model,
    embedding = EXCLUDED.embedding,
    point = EXCLUDED.point,
    footprint = EXCLUDED.footprint
WHERE (retrieval_documents.stream_ms, retrieval_documents.stream_seq)
    < (EXCLUDED.stream_ms, EXCLUDED.stream_seq)
   OR (
        (retrieval_documents.stream_ms, retrieval_documents.stream_seq)
            = (EXCLUDED.stream_ms, EXCLUDED.stream_seq)
        AND (retrieval_documents.document_hash, retrieval_documents.embedding_model)
            IS DISTINCT FROM (EXCLUDED.document_hash, EXCLUDED.embedding_model)
   )
"""

_UPSERT_CHECKPOINT = """
INSERT INTO projection_checkpoints (
    projection_name,
    last_stream_id,
    last_stream_ms,
    last_stream_seq,
    updated_at
)
VALUES (:projection_name, :stream_id, :stream_ms, :stream_seq, CURRENT_TIMESTAMP)
ON CONFLICT (projection_name) DO UPDATE SET
    last_stream_id = EXCLUDED.last_stream_id,
    last_stream_ms = EXCLUDED.last_stream_ms,
    last_stream_seq = EXCLUDED.last_stream_seq,
    updated_at = EXCLUDED.updated_at
WHERE (projection_checkpoints.last_stream_ms, projection_checkpoints.last_stream_seq)
    < (EXCLUDED.last_stream_ms, EXCLUDED.last_stream_seq)
"""


def _vector_literal(embedding: Embedding) -> str:
    return "[" + ",".join(format(value, ".9g") for value in embedding) + "]"


def retrieval_document_values(document: IndexedDocument) -> dict[str, object]:
    """Extract atomic index values while preserving the authoritative event."""
    values = projection_values(document.message)
    values.update(
        document_text=document.text,
        document_hash=document.document_hash,
        embedding_model=document.embedding_model,
        embedding=_vector_literal(document.embedding),
    )
    return values


def _event_from_database(value: object) -> Event:
    if isinstance(value, bytes | str):
        return Event.model_validate_json(value)
    if isinstance(value, Mapping):
        return Event.model_validate(dict(value))
    raise TypeError("event_json must be a JSON object, string, or bytes")


def _number(value: object, *, field: str) -> float:
    if isinstance(value, int | float | Decimal) and not isinstance(value, bool):
        return float(value)
    raise TypeError(f"{field} must be numeric")


def _optional_number(value: object, *, field: str) -> float | None:
    return None if value is None else _number(value, field=field)


class PostgresRetrievalStore:
    """Independent current-document index with atomic replay checkpointing."""

    def __init__(
        self,
        *,
        database_url: str,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._owns_engine = engine is None
        self._engine = engine or create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
        )

    async def checkpoint(self, projection_name: str) -> str | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT last_stream_id FROM projection_checkpoints "
                    "WHERE projection_name = :projection_name"
                ),
                {"projection_name": projection_name},
            )
            return cast(str | None, result.scalar_one_or_none())

    async def index(
        self,
        *,
        projection_name: str,
        documents: tuple[IndexedDocument, ...],
    ) -> None:
        if not documents:
            return
        positions = [parse_stream_id(document.message.stream_id) for document in documents]
        if positions != sorted(set(positions)):
            raise ValueError("retrieval batches must contain unique oldest-first stream IDs")
        values = [retrieval_document_values(document) for document in documents]
        async with self._engine.begin() as connection:
            await connection.execute(text(_UPSERT_DOCUMENT), values)
            await connection.execute(
                text(_UPSERT_CHECKPOINT),
                {
                    "projection_name": projection_name,
                    "stream_id": documents[-1].message.stream_id,
                    "stream_ms": positions[-1][0],
                    "stream_seq": positions[-1][1],
                },
            )

    @staticmethod
    def _filters(query: SearchQuery) -> tuple[list[str], dict[str, object]]:
        conditions = ["TRUE"]
        parameters: dict[str, object] = {"candidate_limit": query.candidate_limit}
        if query.source is not None:
            conditions.append("source = :source")
            parameters["source"] = query.source
        if query.occurred_after is not None:
            conditions.append("occurred_at >= :occurred_after")
            parameters["occurred_after"] = query.occurred_after
        if query.occurred_before is not None:
            conditions.append("occurred_at <= :occurred_before")
            parameters["occurred_before"] = query.occurred_before
        if query.active_only:
            conditions.append("(expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)")
        geometry = "COALESCE(footprint, point)"
        if query.bounds is not None:
            conditions.append(
                f"{geometry} IS NOT NULL AND ST_Intersects("
                f"{geometry}, ST_MakeEnvelope(:west, :south, :east, :north, 4326))"
            )
            parameters.update(
                west=query.bounds.west,
                south=query.bounds.south,
                east=query.bounds.east,
                north=query.bounds.north,
            )
        if query.near is not None:
            conditions.append(
                f"{geometry} IS NOT NULL AND ST_DWithin("
                f"{geometry}::geography, "
                "ST_SetSRID(ST_MakePoint(:near_longitude, :near_latitude), 4326)::geography, "
                ":radius_metres)"
            )
            parameters.update(
                near_longitude=query.near.longitude,
                near_latitude=query.near.latitude,
                radius_metres=query.near.radius_km * 1_000,
            )
        return conditions, parameters

    @staticmethod
    def _distance_expression(query: SearchQuery) -> str:
        if query.near is None:
            return "NULL::double precision AS distance_km"
        return (
            "ST_Distance(COALESCE(footprint, point)::geography, "
            "ST_SetSRID(ST_MakePoint(:near_longitude, :near_latitude), 4326)::geography) "
            "/ 1000.0 AS distance_km"
        )

    @staticmethod
    def _candidate(row: RowMapping, *, rank: int) -> ChannelCandidate:
        return ChannelCandidate(
            message=StreamMessage(
                stream_id=cast(str, row["stream_id"]),
                event=_event_from_database(row["event_json"]),
            ),
            document_text=cast(str, row["document_text"]),
            rank=rank,
            score=_number(row["score"], field="score"),
            distance_km=_optional_number(row["distance_km"], field="distance_km"),
        )

    async def candidates(
        self,
        query: SearchQuery,
        embedding: Embedding,
        *,
        embedding_model: str,
    ) -> CandidateBatch:
        conditions, parameters = self._filters(query)
        parameters.update(
            query_text=query.text,
            query_embedding=_vector_literal(embedding),
            embedding_model=embedding_model,
        )
        predicate = " AND ".join(conditions)
        distance = self._distance_expression(query)
        lexical = text(
            f"""
            SELECT
                stream_id,
                event_json,
                document_text,
                ts_rank_cd(text_search, websearch_to_tsquery('english', :query_text)) AS score,
                {distance}
            FROM retrieval_documents
            WHERE {predicate}
              AND text_search @@ websearch_to_tsquery('english', :query_text)
            ORDER BY score DESC, stream_ms DESC, stream_seq DESC, source, event_id
            LIMIT :candidate_limit
            """
        )
        dense = text(
            f"""
            SELECT
                stream_id,
                event_json,
                document_text,
                1 - (embedding <=> CAST(:query_embedding AS vector)) AS score,
                {distance}
            FROM retrieval_documents
            WHERE {predicate}
              AND embedding_model = :embedding_model
            ORDER BY embedding <=> CAST(:query_embedding AS vector),
                     stream_ms DESC, stream_seq DESC, source, event_id
            LIMIT :candidate_limit
            """
        )
        async with self._engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            await connection.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            lexical_rows = (await connection.execute(lexical, parameters)).mappings().all()
            dense_rows = (await connection.execute(dense, parameters)).mappings().all()
        return CandidateBatch(
            lexical=tuple(
                self._candidate(row, rank=rank) for rank, row in enumerate(lexical_rows, start=1)
            ),
            dense=tuple(
                self._candidate(row, rank=rank) for rank, row in enumerate(dense_rows, start=1)
            ),
        )

    async def is_ready(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1 FROM retrieval_documents LIMIT 1"))
            return True
        except (OSError, SQLAlchemyError):
            return False

    async def close(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()
