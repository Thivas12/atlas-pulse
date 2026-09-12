import type { EventEnvelope } from "../types";

interface EnvelopeOptions {
  streamId?: string;
  eventId?: string;
  magnitude?: number | null;
  place?: string | null;
  occurredAt?: string;
  updatedAt?: string | null;
  location?: { latitude: number; longitude: number; altitude_km: number | null } | null;
  source?: "usgs" | "nws" | "firms" | "gdelt";
  alertType?: string;
  severity?: "Extreme" | "Severe" | "Moderate" | "Minor" | "Unknown";
  geometry?: unknown;
  expiresAt?: string | null;
  fireRadiativePowerMw?: number;
  fireConfidence?: "Low" | "Nominal" | "High";
  conflictPriority?: "Elevated" | "High" | "Critical";
  conflictRootCode?: "14" | "15" | "16" | "17" | "18" | "19" | "20";
  goldsteinScale?: number;
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
  fireRadiativePowerMw = 18.4,
  fireConfidence = "High",
  conflictPriority = "High",
  conflictRootCode = "19",
  goldsteinScale = -7,
}: EnvelopeOptions = {}): EventEnvelope {
  const conflictCategory = {
    "14": "Protest",
    "15": "Force posture",
    "16": "Reduced relations",
    "17": "Coercion",
    "18": "Assault",
    "19": "Fight",
    "20": "Mass violence",
  }[conflictRootCode];
  return {
    stream_id: streamId,
    event: {
      event_id: eventId,
      event_type:
        source === "nws"
          ? "weather.alert"
          : source === "firms"
            ? "fire.thermal_anomaly"
            : source === "gdelt"
              ? "geopolitical.gdelt_event"
              : "seismic.earthquake",
      source,
      occurred_at: occurredAt,
      ingested_at: "2026-09-12T10:03:00Z",
      schema_version: "1.0.0",
      location,
      payload: {
        magnitude: source === "firms" || source === "gdelt" ? null : magnitude,
        place,
        depth_km:
          source === "firms" || source === "gdelt"
            ? null
            : location
              ? Math.abs(location.altitude_km ?? 0)
              : null,
        updated_at: updatedAt,
        status: "reviewed",
        source_url:
          source === "nws"
            ? `https://api.weather.gov/alerts/${eventId}`
            : source === "firms"
              ? "https://firms.modaps.eosdis.nasa.gov/map/"
              : source === "gdelt"
                ? "https://news.example.org/reports/123"
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
        ...(source === "firms"
          ? {
              title: `${fireConfidence}-confidence VIIRS thermal anomaly`,
              confidence: fireConfidence,
              confidence_rank: { Low: 1, Nominal: 2, High: 3 }[fireConfidence],
              fire_radiative_power_mw: fireRadiativePowerMw,
              brightness_ti4_k: 367.9,
              satellite: "N20",
              instrument: "VIIRS",
              product: "VIIRS_NOAA20_NRT",
              day_night: "day",
              expires_at: expiresAt,
              expiry_basis: "AtlasPulse 24-hour operational window",
            }
          : {}),
        ...(source === "gdelt"
          ? {
              title: `${conflictCategory}: GOVERNMENT → REBELS`,
              category: conflictCategory,
              priority: conflictPriority,
              severity_rank: { Elevated: 2, High: 3, Critical: 4 }[conflictPriority],
              cameo_event_code: "190",
              cameo_base_code: "190",
              cameo_root_code: conflictRootCode,
              quad_class: 4,
              quad_class_label: "Material conflict",
              goldstein_scale: goldsteinScale,
              is_root_event: true,
              actor1: "GOVERNMENT",
              actor1_code: "GOV",
              actor1_country_code: "US",
              actor2: "REBELS",
              actor2_code: "REB",
              actor2_country_code: "US",
              mentions: 12,
              sources: 4,
              articles: 7,
              average_tone: -4.25,
              reported_event_date: "2026-09-12",
              detected_at: occurredAt,
              expires_at: expiresAt,
              expiry_basis: "AtlasPulse 24-hour operational window",
              geo_precision: "world city or landmark centroid",
              geo_type: 4,
              verification_status: "machine-coded media observation; not independently verified",
            }
          : {}),
      },
    },
  };
}
