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
