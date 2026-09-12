import type { EventEnvelope } from "../types";

interface EnvelopeOptions {
  streamId?: string;
  eventId?: string;
  magnitude?: number | null;
  place?: string | null;
  occurredAt?: string;
  updatedAt?: string | null;
  location?: { latitude: number; longitude: number; altitude_km: number | null } | null;
  source?: "usgs" | "nws";
  alertType?: string;
  severity?: "Extreme" | "Severe" | "Moderate" | "Minor" | "Unknown";
  geometry?: unknown;
  expiresAt?: string | null;
}

export function makeEnvelope({
  streamId = "1000-0",
  eventId = "us-test-1",
  magnitude = 4.2,
  place = "Test Ridge",
  occurredAt = "2026-09-12T10:00:00Z",
  updatedAt = "2026-09-12T10:02:00Z",
  location = { latitude: 12.25, longitude: 77.5, altitude_km: -10 },
  source = "usgs",
  alertType = "Severe Thunderstorm Warning",
  severity = "Severe",
  geometry = null,
  expiresAt = "2099-01-01T00:00:00Z",
}: EnvelopeOptions = {}): EventEnvelope {
  return {
    stream_id: streamId,
    event: {
      event_id: eventId,
      event_type: source === "nws" ? "weather.alert" : "seismic.earthquake",
      source,
      occurred_at: occurredAt,
      ingested_at: "2026-09-12T10:03:00Z",
      schema_version: "1.0.0",
      location,
      payload: {
        magnitude,
        place,
        depth_km: location ? Math.abs(location.altitude_km ?? 0) : null,
        updated_at: updatedAt,
        status: "reviewed",
        source_url:
          source === "nws"
            ? `https://api.weather.gov/alerts/${eventId}`
            : `https://earthquake.usgs.gov/earthquakes/eventpage/${eventId}`,
        ...(source === "nws"
          ? {
              alert_type: alertType,
              severity,
              severity_rank: { Unknown: 0, Minor: 1, Moderate: 2, Severe: 3, Extreme: 4 }[severity],
              certainty: "Likely",
              urgency: "Immediate",
              message_type: "Alert",
              geometry,
              expires_at: expiresAt,
              sender_name: "NWS Test Office",
              description: "Synthetic alert description.",
              instruction: "Take shelter now.",
            }
          : {}),
      },
    },
  };
}
