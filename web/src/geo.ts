import type { Feature, FeatureCollection, Point } from "geojson";
import { magnitudeOf, placeOf } from "./event-utils";
import type { EventEnvelope } from "./types";

export interface EventProperties {
  streamId: string;
  eventId: string;
  magnitude: number | null;
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
    if (!event.location) continue;
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
        magnitude: magnitudeOf(event),
        place: placeOf(event),
        occurredAt: event.occurred_at,
        status: typeof status === "string" ? status : "unknown",
        depthKm: typeof depth === "number" ? depth : null,
      },
    });
  }
  return { type: "FeatureCollection", features };
}
