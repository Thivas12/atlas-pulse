import type {
  Feature,
  FeatureCollection,
  LineString,
  MultiLineString,
  MultiPolygon,
  Point,
  Polygon,
  Position,
} from "geojson";
import {
  conflictPriorityRankOf,
  fireConfidenceRankOf,
  fireRadiativePowerOf,
  magnitudeOf,
  placeOf,
  severityOf,
  severityRankOf,
  titleOf,
} from "./event-utils";
import { alertGeometrySchema, type EventEnvelope, type IncidentCandidate } from "./types";

export interface EventProperties {
  streamId: string;
  eventId: string;
  source: string;
  magnitude: number | null;
  fireRadiativePowerMw: number | null;
  fireConfidenceRank: number;
  conflictPriorityRank: number;
  severity: string;
  severityRank: number;
  title: string;
  place: string;
  occurredAt: string;
  status: string;
  depthKm: number | null;
}

export interface IncidentEdgeProperties {
  incidentId: string;
  edgeId: string;
  title: string;
  distanceKm: number;
  timeDeltaMinutes: number;
  spatialRelation: string;
}

function evidenceLine(from: Position, to: Position): LineString | MultiLineString {
  const longitudeDelta = to[0] - from[0];
  if (Math.abs(longitudeDelta) <= 180) {
    return { type: "LineString", coordinates: [from, to] };
  }

  const crossesEast = longitudeDelta < -180;
  const boundary = crossesEast ? 180 : -180;
  const wrappedBoundary = -boundary;
  const adjustedToLongitude = to[0] + (crossesEast ? 360 : -360);
  const adjustedDelta = adjustedToLongitude - from[0];
  const fraction = adjustedDelta === 0 ? 0.5 : (boundary - from[0]) / adjustedDelta;
  const crossingLatitude = from[1] + (to[1] - from[1]) * fraction;
  return {
    type: "MultiLineString",
    coordinates: [
      [from, [boundary, crossingLatitude]],
      [[wrappedBoundary, crossingLatitude], to],
    ],
  };
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
        conflictPriorityRank: conflictPriorityRankOf(event),
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
        conflictPriorityRank: 0,
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

export function incidentEdgesToGeoJson(
  incidents: IncidentCandidate[],
): FeatureCollection<LineString | MultiLineString, IncidentEdgeProperties> {
  const features: Array<Feature<LineString | MultiLineString, IncidentEdgeProperties>> = [];
  for (const incident of incidents) {
    const nodes = new Map(incident.nodes.map((node) => [node.node_id, node]));
    for (const edge of incident.edges) {
      const from = nodes.get(edge.from_node_id)?.event.location;
      const to = nodes.get(edge.to_node_id)?.event.location;
      if (!from || !to) continue;
      features.push({
        type: "Feature",
        id: edge.edge_id,
        geometry: evidenceLine([from.longitude, from.latitude], [to.longitude, to.latitude]),
        properties: {
          incidentId: incident.incident_id,
          edgeId: edge.edge_id,
          title: incident.title,
          distanceKm: edge.distance_km,
          timeDeltaMinutes: edge.time_delta_minutes,
          spatialRelation: edge.spatial_relation,
        },
      });
    }
  }
  return { type: "FeatureCollection", features };
}
