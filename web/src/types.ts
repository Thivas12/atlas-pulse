import { z } from "zod";

const geoPointSchema = z.object({
  latitude: z.number().min(-90).max(90),
  longitude: z.number().min(-180).max(180),
  altitude_km: z.number().nullable(),
});

const positionSchema = z.tuple([z.number().min(-180).max(180), z.number().min(-90).max(90)]);
const linearRingSchema = z.array(positionSchema).min(4);
const polygonCoordinatesSchema = z.array(linearRingSchema).min(1);

export const alertGeometrySchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("Polygon"), coordinates: polygonCoordinatesSchema }),
  z.object({
    type: z.literal("MultiPolygon"),
    coordinates: z.array(polygonCoordinatesSchema).min(1),
  }),
]);

export const eventSchema = z.object({
  event_id: z.string().min(1),
  event_type: z.string().min(1),
  source: z.string().min(1),
  occurred_at: z.string(),
  ingested_at: z.string(),
  schema_version: z.string(),
  location: geoPointSchema.nullable(),
  payload: z.record(z.string(), z.unknown()),
});

export const eventEnvelopeSchema = z.object({
  stream_id: z.string().regex(/^\d+-\d+$/),
  event: eventSchema,
});

export const eventsResponseSchema = z.object({
  count: z.number().int().nonnegative(),
  items: z.array(eventEnvelopeSchema),
});

export const replayResponseSchema = eventsResponseSchema.extend({
  next_cursor: z
    .string()
    .regex(/^\d+-\d+$/)
    .nullable(),
  has_more: z.boolean(),
  order: z.literal("oldest_first"),
});

export const signalsResponseSchema = eventsResponseSchema.extend({
  next_cursor: z
    .string()
    .regex(/^\d+-\d+$/)
    .nullable(),
  has_more: z.boolean(),
  order: z.literal("newest_revision_first"),
});

const incidentCenterSchema = z.object({
  latitude: z.number().min(-90).max(90),
  longitude: z.number().min(-180).max(180),
});

const evidenceNodeSchema = z.object({
  node_id: z.string().min(1),
  stream_id: z.string().regex(/^\d+-\d+$/),
  event: eventSchema,
});

const evidenceEdgeSchema = z.object({
  edge_id: z.string().min(1),
  from_node_id: z.string().min(1),
  to_node_id: z.string().min(1),
  relation: z.literal("spatiotemporal_cooccurrence"),
  spatial_relation: z.enum(["intersects", "within_radius"]),
  distance_km: z.number().nonnegative(),
  time_delta_minutes: z.number().nonnegative(),
  from_geometry_basis: z.enum(["point", "polygon"]),
  to_geometry_basis: z.enum(["point", "polygon"]),
  rule_version: z.string().min(1),
});

export const incidentCandidateSchema = z.object({
  incident_id: z.string().min(1),
  title: z.string().min(1),
  started_at: z.string(),
  latest_signal_at: z.string(),
  center: incidentCenterSchema.nullable(),
  sources: z.array(z.string().min(1)).min(2),
  node_count: z.number().int().min(2),
  edge_count: z.number().int().min(1),
  max_distance_km: z.number().nonnegative(),
  time_span_minutes: z.number().nonnegative(),
  nodes: z.array(evidenceNodeSchema).min(2),
  edges: z.array(evidenceEdgeSchema).min(1),
  rule_version: z.string().min(1),
  caveat: z.string().min(1),
});

export const incidentsResponseSchema = z.object({
  count: z.number().int().nonnegative(),
  total_incidents: z.number().int().nonnegative(),
  items: z.array(incidentCandidateSchema),
  incidents_truncated: z.boolean(),
  candidate_edges_truncated: z.boolean(),
  rule_version: z.string().min(1),
  caveat: z.string().min(1),
  parameters: z.object({
    radius_km: z.number().positive(),
    time_window_minutes: z.number().int().positive(),
    lookback_hours: z.number().int().positive(),
    candidate_edge_limit: z.number().int().positive(),
    incident_limit: z.number().int().positive(),
    active_only: z.boolean(),
    bbox: z.tuple([z.number(), z.number(), z.number(), z.number()]).nullable(),
  }),
});

const citationSchema = z.object({
  status: z.enum(["traceable", "missing", "rejected"]),
  url: z.string().url().nullable(),
  source_field: z.string().nullable(),
  reasons: z.array(z.string().min(1)),
});

const rankingSchema = z.object({
  lexical_rank: z.number().int().positive().nullable(),
  lexical_score: z.number().nullable(),
  dense_rank: z.number().int().positive().nullable(),
  dense_similarity: z.number().nullable(),
  rrf_score: z.number().nonnegative(),
  exact_phrase_match: z.boolean(),
  token_coverage: z.number().min(0).max(1),
  rerank_score: z.number().nonnegative(),
});

export const searchHitSchema = z.object({
  stream_id: z.string().regex(/^\d+-\d+$/),
  event: eventSchema,
  document_text: z.string().min(1),
  distance_km: z.number().nonnegative().nullable(),
  ranking: rankingSchema,
  citation: citationSchema,
});

export const searchResponseSchema = z.object({
  count: z.number().int().nonnegative(),
  candidates_considered: z.number().int().nonnegative(),
  items: z.array(searchHitSchema),
  embedding_model: z.string().min(1),
  ranking_rule: z.string().min(1),
  caveat: z.string().min(1),
  parameters: z.object({
    query: z.string().min(2),
    limit: z.number().int().positive(),
    candidate_limit: z.number().int().positive(),
    source: z.enum(["usgs", "nws", "firms", "gdelt"]).nullable(),
    occurred_after: z.string().nullable(),
    occurred_before: z.string().nullable(),
    active_only: z.boolean(),
    bbox: z.tuple([z.number(), z.number(), z.number(), z.number()]).nullable(),
    near: z.tuple([z.number(), z.number()]).nullable(),
    radius_km: z.number().positive().nullable(),
  }),
});

export interface ViewportBounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

export type AtlasEvent = z.infer<typeof eventSchema>;
export type AlertGeometry = z.infer<typeof alertGeometrySchema>;
export type EventEnvelope = z.infer<typeof eventEnvelopeSchema>;
export type EventsResponse = z.infer<typeof eventsResponseSchema>;
export type ReplayResponse = z.infer<typeof replayResponseSchema>;
export type SignalsResponse = z.infer<typeof signalsResponseSchema>;
export type IncidentCandidate = z.infer<typeof incidentCandidateSchema>;
export type IncidentsResponse = z.infer<typeof incidentsResponseSchema>;
export type SearchHit = z.infer<typeof searchHitSchema>;
export type SearchResponse = z.infer<typeof searchResponseSchema>;
