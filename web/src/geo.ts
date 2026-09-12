import type { Feature, FeatureCollection, MultiPolygon, Point, Polygon } from "geojson";
import {
  fireConfidenceRankOf,
  fireRadiativePowerOf,
  magnitudeOf,
  placeOf,
  severityOf,
  severityRankOf,
  titleOf,
} from "./event-utils";
import { alertGeometrySchema, type EventEnvelope } from "./types";

export interface EventProperties {
  streamId: string;
  eventId: string;
  source: string;
  magnitude: number | null;
  fireRadiativePowerMw: number | null;
  fireConfidenceRank: number;
  severity: string;
  severityRank: number;
  title: string;
  place: string;
  occurredAt: string;
  status: string;
  depthKm: number | null;
}

export function eventsToGeoJson(
  events: EventEnvelope[],
): FeatureCollection<Point, EventProperties> {
  const features: Array<Feature<Point, EventProperties>> = [];
  for (const { event, stream_id: streamId } of events) {
    if (!event.location || event.source === "nws") continue;
    const status = event.payload.status;
    const depth = event.payload.depth_km;
    features.push({
      type: "Feature",
      id: streamId,
      geometry: {
        type: "Point",
        coordinates: [event.location.longitude, event.location.latitude],
      },
      properties: {
        streamId,
        eventId: event.event_id,
        source: event.source,
        magnitude: magnitudeOf(event),
        fireRadiativePowerMw: fireRadiativePowerOf(event),
        fireConfidenceRank: fireConfidenceRankOf(event),
        severity: severityOf(event) ?? "Unknown",
        severityRank: severityRankOf(event),
        title: titleOf(event),
        place: placeOf(event),
        occurredAt: event.occurred_at,
        status: typeof status === "string" ? status : "unknown",
        depthKm: typeof depth === "number" ? depth : null,
      },
    });
  }
  return { type: "FeatureCollection", features };
}

export function weatherPolygonsToGeoJson(
  events: EventEnvelope[],
): FeatureCollection<Polygon | MultiPolygon, EventProperties> {
  const features: Array<Feature<Polygon | MultiPolygon, EventProperties>> = [];
  for (const { event, stream_id: streamId } of events) {
    if (event.source !== "nws") continue;
    const geometry = alertGeometrySchema.safeParse(event.payload.geometry);
    if (!geometry.success) continue;
    const status = event.payload.status;
    features.push({
      type: "Feature",
      id: streamId,
      geometry: geometry.data,
      properties: {
        streamId,
        eventId: event.event_id,
        source: event.source,
        magnitude: null,
        fireRadiativePowerMw: null,
        fireConfidenceRank: 0,
        severity: severityOf(event) ?? "Unknown",
        severityRank: severityRankOf(event),
        title: titleOf(event),
        place: placeOf(event),
        occurredAt: event.occurred_at,
        status: typeof status === "string" ? status : "unknown",
        depthKm: null,
      },
    });
  }
  return { type: "FeatureCollection", features };
}
