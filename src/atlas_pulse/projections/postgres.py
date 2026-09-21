"""Transactional PostgreSQL/PostGIS current-signal projection."""

import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import cast

from agent_rag_core import Event
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from atlas_pulse.correlation import CorrelationBatch, CorrelationPair, GeometryBasis
from atlas_pulse.projections.base import CorrelationQuery, SignalPage, SignalQuery
from atlas_pulse.streams import StreamMessage

_INSERT_REVISIONS = """
INSERT INTO event_revisions (
    stream_id,
    stream_ms,
    stream_seq,
    source,
    event_id,
    event_type,
    occurred_at,
    ingested_at,
    event_json,
    point,
    footprint,
    severity_rank,
    magnitude,
    expires_at,
    place,
    title
) VALUES (
    :stream_id,
    :stream_ms,
    :stream_seq,
    :source,
    :event_id,
    :event_type,
    :occurred_at,
    :ingested_at,
    CAST(:event_json AS jsonb),
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
    END,
    :severity_rank,
    :magnitude,
    :expires_at,
    :place,
    :title
)
ON CONFLICT (stream_id) DO NOTHING
"""

_UPSERT_CURRENT = """
INSERT INTO current_signals (source, event_id, stream_id, stream_ms, stream_seq)
VALUES (:source, :event_id, :stream_id, :stream_ms, :stream_seq)
ON CONFLICT (source, event_id) DO UPDATE SET
    stream_id = EXCLUDED.stream_id,
    stream_ms = EXCLUDED.stream_ms,
    stream_seq = EXCLUDED.stream_seq
WHERE (current_signals.stream_ms, current_signals.stream_seq)
    < (EXCLUDED.stream_ms, EXCLUDED.stream_seq)
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


def parse_stream_id(stream_id: str) -> tuple[int, int]:
    """Parse a Valkey Stream ID into sortable numeric components."""
    parts = stream_id.split("-", maxsplit=1)
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"invalid stream id: {stream_id}")
    return int(parts[0]), int(parts[1])


def _payload_number(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _payload_integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _payload_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _payload_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def projection_values(message: StreamMessage) -> dict[str, object]:
    """Extract indexed columns while retaining the authoritative event JSON."""
    event = message.event
    stream_ms, stream_seq = parse_stream_id(message.stream_id)
    geometry = event.payload.get("geometry")
    footprint_json = (
        json.dumps(geometry, sort_keys=True, separators=(",", ":"))
        if isinstance(geometry, dict) and geometry.get("type") in {"Polygon", "MultiPolygon"}
        else None
    )
    return {
        "stream_id": message.stream_id,
        "stream_ms": stream_ms,
        "stream_seq": stream_seq,
        "source": event.source,
        "event_id": event.event_id,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at,
        "ingested_at": event.ingested_at,
        "event_json": event.model_dump_json(),
        "longitude": event.location.longitude if event.location else None,
        "latitude": event.location.latitude if event.location else None,
        "footprint_json": footprint_json,
        "severity_rank": _payload_integer(event.payload.get("severity_rank")),
        "magnitude": _payload_number(event.payload.get("magnitude")),
        "expires_at": _payload_datetime(event.payload.get("expires_at")),
        "place": _payload_text(event.payload.get("place")),
        "title": _payload_text(event.payload.get("title")),
    }


def _event_from_database(value: object) -> Event:
    if isinstance(value, bytes | str):
        return Event.model_validate_json(value)
    if isinstance(value, Mapping):
        return Event.model_validate(dict(value))
    raise TypeError("event_json must be a JSON object, string, or bytes")


def _number_from_database(value: object, *, field: str) -> float:
    if isinstance(value, int | float | Decimal) and not isinstance(value, bool):
        return float(value)
    raise TypeError(f"{field} must be numeric")


def _geometry_basis_from_database(value: object) -> GeometryBasis:
    if value == "point" or value == "polygon":
        return value
    raise TypeError("geometry basis must be point or polygon")


class PostgresSignalStore:
    """Durable immutable revisions, current pointers, and atomic checkpoints."""

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

    async def project(
        self,
        *,
        projection_name: str,
        messages: tuple[StreamMessage, ...],
    ) -> None:
        if not messages:
            return
        positions = [parse_stream_id(message.stream_id) for message in messages]
        if positions != sorted(set(positions)):
            raise ValueError("projection batches must contain unique oldest-first stream IDs")

        values = [projection_values(message) for message in messages]
        async with self._engine.begin() as connection:
            await connection.execute(text(_INSERT_REVISIONS), values)
            await connection.execute(text(_UPSERT_CURRENT), values)
            await connection.execute(
                text(_UPSERT_CHECKPOINT),
                {
                    "projection_name": projection_name,
                    "stream_id": messages[-1].stream_id,
                    "stream_ms": positions[-1][0],
                    "stream_seq": positions[-1][1],
                },
            )

    async def query_current(self, query: SignalQuery) -> SignalPage:
        conditions = ["TRUE"]
        parameters: dict[str, object] = {"fetch_limit": query.limit + 1}
        if query.source is not None:
            conditions.append("cs.source = :source")
            parameters["source"] = query.source
        if query.min_severity is not None:
            conditions.append("er.severity_rank >= :min_severity")
            parameters["min_severity"] = query.min_severity
        if query.occurred_after is not None:
            conditions.append("er.occurred_at >= :occurred_after")
            parameters["occurred_after"] = query.occurred_after
        if query.occurred_before is not None:
            conditions.append("er.occurred_at <= :occurred_before")
            parameters["occurred_before"] = query.occurred_before
        if query.active_only:
            conditions.append("(er.expires_at IS NULL OR er.expires_at > CURRENT_TIMESTAMP)")
        if query.after is not None:
            cursor_ms, cursor_seq = parse_stream_id(query.after)
            conditions.append("(cs.stream_ms, cs.stream_seq) < (:cursor_ms, :cursor_seq)")
            parameters.update(cursor_ms=cursor_ms, cursor_seq=cursor_seq)
        if query.bounds is not None:
            bounds = query.bounds
            spatial = (
                "((er.footprint IS NOT NULL AND ST_Intersects(er.footprint, "
                "ST_MakeEnvelope(:west, :south, :east, :north, 4326))) OR "
                "(er.footprint IS NULL AND er.point IS NOT NULL AND ST_Intersects(er.point, "
                "ST_MakeEnvelope(:west, :south, :east, :north, 4326))))"
            )
            if query.include_area_only:
                spatial = f"({spatial} OR (er.footprint IS NULL AND er.point IS NULL))"
            conditions.append(spatial)
            parameters.update(
                west=bounds.west,
                south=bounds.south,
                east=bounds.east,
                north=bounds.north,
            )

        statement = text(
            """
            SELECT er.stream_id, er.event_json
            FROM current_signals AS cs
            JOIN event_revisions AS er ON er.stream_id = cs.stream_id
            WHERE """
            + " AND ".join(conditions)
            + """
            ORDER BY cs.stream_ms DESC, cs.stream_seq DESC
            LIMIT :fetch_limit
            """
        )
        async with self._engine.connect() as connection:
            result = await connection.execute(statement, parameters)
            rows = result.mappings().all()

        has_more = len(rows) > query.limit
        visible = rows[: query.limit]
        messages = tuple(
            StreamMessage(
                stream_id=cast(str, row["stream_id"]),
                event=_event_from_database(row["event_json"]),
            )
            for row in visible
        )
        return SignalPage(
            items=messages,
            next_cursor=messages[-1].stream_id if messages else None,
            has_more=has_more,
        )

    async def query_correlations(self, query: CorrelationQuery) -> CorrelationBatch:
        """Measure bounded current cross-source pairs using PostGIS geography."""
        left_geometry = "COALESCE(left_signal.footprint, left_signal.point)"
        right_geometry = "COALESCE(right_signal.footprint, right_signal.point)"
        conditions = [
            f"{left_geometry} IS NOT NULL",
            f"{right_geometry} IS NOT NULL",
            "left_signal.occurred_at >= "
            "CURRENT_TIMESTAMP - make_interval(hours => :lookback_hours)",
            "right_signal.occurred_at >= "
            "CURRENT_TIMESTAMP - make_interval(hours => :lookback_hours)",
        ]
        parameters: dict[str, object] = {
            "lookback_hours": query.lookback_hours,
            "time_window_seconds": query.time_window_minutes * 60,
            "radius_metres": query.radius_km * 1_000,
            "fetch_limit": query.edge_limit + 1,
        }
        if query.active_only:
            conditions.extend(
                (
                    "(left_signal.expires_at IS NULL "
                    "OR left_signal.expires_at > CURRENT_TIMESTAMP)",
                    "(right_signal.expires_at IS NULL "
                    "OR right_signal.expires_at > CURRENT_TIMESTAMP)",
                )
            )
        if query.bounds is not None:
            envelope = "ST_MakeEnvelope(:west, :south, :east, :north, 4326)"
            conditions.extend(
                (
                    f"ST_Intersects({left_geometry}, {envelope})",
                    f"ST_Intersects({right_geometry}, {envelope})",
                )
            )
            parameters.update(
                west=query.bounds.west,
                south=query.bounds.south,
                east=query.bounds.east,
                north=query.bounds.north,
            )

        statement = text(
            """
            SELECT
                left_signal.stream_id AS left_stream_id,
                left_signal.event_json AS left_event_json,
                CASE WHEN left_signal.footprint IS NOT NULL THEN 'polygon' ELSE 'point' END
                    AS left_geometry_basis,
                right_signal.stream_id AS right_stream_id,
                right_signal.event_json AS right_event_json,
                CASE WHEN right_signal.footprint IS NOT NULL THEN 'polygon' ELSE 'point' END
                    AS right_geometry_basis,
                ST_Distance(
                    COALESCE(left_signal.footprint, left_signal.point)::geography,
                    COALESCE(right_signal.footprint, right_signal.point)::geography
                ) / 1000.0 AS distance_km,
                ABS(EXTRACT(EPOCH FROM (
                    left_signal.occurred_at - right_signal.occurred_at
                ))) / 60.0 AS time_delta_minutes,
                GREATEST(left_signal.occurred_at, right_signal.occurred_at) AS latest_signal_at
            FROM current_signals AS left_current
            JOIN event_revisions AS left_signal
              ON left_signal.stream_id = left_current.stream_id
             AND left_signal.source = left_current.source
             AND left_signal.event_id = left_current.event_id
            JOIN event_revisions AS right_signal
              ON left_signal.source < right_signal.source
             AND right_signal.occurred_at BETWEEN
                    left_signal.occurred_at
                        - make_interval(secs => :time_window_seconds)
                AND left_signal.occurred_at
                        + make_interval(secs => :time_window_seconds)
             AND ST_DWithin(
                    COALESCE(left_signal.footprint, left_signal.point)::geography,
                    COALESCE(right_signal.footprint, right_signal.point)::geography,
                    :radius_metres
                 )
            JOIN current_signals AS right_current
              ON right_current.source = right_signal.source
             AND right_current.event_id = right_signal.event_id
             AND right_current.stream_id = right_signal.stream_id
            WHERE """
            + " AND ".join(conditions)
            + """
            ORDER BY latest_signal_at DESC, distance_km ASC,
                     left_signal.source, left_signal.event_id,
                     right_signal.source, right_signal.event_id
            LIMIT :fetch_limit
            """
        )
        async with self._engine.connect() as connection:
            result = await connection.execute(statement, parameters)
            rows = result.mappings().all()

        truncated = len(rows) > query.edge_limit
        pairs = tuple(
            CorrelationPair(
                left=StreamMessage(
                    stream_id=cast(str, row["left_stream_id"]),
                    event=_event_from_database(row["left_event_json"]),
                ),
                right=StreamMessage(
                    stream_id=cast(str, row["right_stream_id"]),
                    event=_event_from_database(row["right_event_json"]),
                ),
                distance_km=_number_from_database(row["distance_km"], field="distance_km"),
                time_delta_minutes=_number_from_database(
                    row["time_delta_minutes"], field="time_delta_minutes"
                ),
                left_geometry_basis=_geometry_basis_from_database(row["left_geometry_basis"]),
                right_geometry_basis=_geometry_basis_from_database(row["right_geometry_basis"]),
            )
            for row in rows[: query.edge_limit]
        )
        return CorrelationBatch(pairs=pairs, truncated=truncated)

    async def is_ready(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1 FROM projection_checkpoints LIMIT 1"))
            return True
        except (OSError, SQLAlchemyError):
            return False

    async def close(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()
