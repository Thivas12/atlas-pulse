import type { AtlasEvent, EventEnvelope } from "./types";

const SEVERITY_RANKS = {
  Unknown: 0,
  Minor: 1,
  Moderate: 2,
  Severe: 3,
  Extreme: 4,
} as const;

const FIRE_CONFIDENCE_RANKS = {
  Low: 1,
  Nominal: 2,
  High: 3,
} as const;

export function magnitudeOf(event: AtlasEvent): number | null {
  const magnitude = event.payload.magnitude;
  return typeof magnitude === "number" && Number.isFinite(magnitude) ? magnitude : null;
}

export function placeOf(event: AtlasEvent): string {
  const place = event.payload.place;
  return typeof place === "string" && place.trim() ? place : "Unknown region";
}

export function isWeatherAlert(event: AtlasEvent): boolean {
  return event.source === "nws" && event.event_type === "weather.alert";
}

export function isFireDetection(event: AtlasEvent): boolean {
  return event.source === "firms" && event.event_type === "fire.thermal_anomaly";
}

export function fireRadiativePowerOf(event: AtlasEvent): number | null {
  const value = event.payload.fire_radiative_power_mw;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function fireConfidenceOf(event: AtlasEvent): keyof typeof FIRE_CONFIDENCE_RANKS | null {
  const confidence = event.payload.confidence;
  return typeof confidence === "string" && confidence in FIRE_CONFIDENCE_RANKS
    ? (confidence as keyof typeof FIRE_CONFIDENCE_RANKS)
    : null;
}

export function fireConfidenceRankOf(event: AtlasEvent): number {
  const confidence = fireConfidenceOf(event);
  return confidence ? FIRE_CONFIDENCE_RANKS[confidence] : 0;
}

export function alertTypeOf(event: AtlasEvent): string | null {
  const alertType = event.payload.alert_type;
  return typeof alertType === "string" && alertType.trim() ? alertType : null;
}

export function severityOf(event: AtlasEvent): keyof typeof SEVERITY_RANKS | null {
  const severity = event.payload.severity;
  return typeof severity === "string" && severity in SEVERITY_RANKS
    ? (severity as keyof typeof SEVERITY_RANKS)
    : null;
}

export function severityRankOf(event: AtlasEvent): number {
  const severity = severityOf(event);
  return severity ? SEVERITY_RANKS[severity] : 0;
}

export function titleOf(event: AtlasEvent): string {
  if (isFireDetection(event)) {
    const title = event.payload.title;
    if (typeof title === "string" && title.trim()) return title;
  }
  return alertTypeOf(event) ?? placeOf(event);
}

export function isActiveAt(event: AtlasEvent, now = new Date()): boolean {
  const expiresAt = event.payload.expires_at;
  if (typeof expiresAt !== "string") return true;
  const expires = new Date(expiresAt);
  return Number.isNaN(expires.getTime()) || expires > now;
}

export function strongestMagnitude(items: EventEnvelope[]): number | null {
  const magnitudes = items
    .map(({ event }) => magnitudeOf(event))
    .filter((value): value is number => value !== null);
  return magnitudes.length ? Math.max(...magnitudes) : null;
}

export function newestUpdate(items: EventEnvelope[]): Date | null {
  const times = items
    .map(({ event }) => event.payload.updated_at)
    .filter((value): value is string => typeof value === "string")
    .map((value) => new Date(value))
    .filter((value) => !Number.isNaN(value.getTime()));
  if (!times.length) return null;
  return new Date(Math.max(...times.map((value) => value.getTime())));
}

export function relativeAge(date: Date | null, now = new Date()): string {
  if (!date) return "—";
  const seconds = Math.max(0, Math.round((now.getTime() - date.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h`;
}

export function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown time";
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "UTC",
  }).format(date);
}
