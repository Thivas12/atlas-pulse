import type { AtlasEvent, EventEnvelope } from "./types";

export function magnitudeOf(event: AtlasEvent): number | null {
  const magnitude = event.payload.magnitude;
  return typeof magnitude === "number" && Number.isFinite(magnitude) ? magnitude : null;
}

export function placeOf(event: AtlasEvent): string {
  const place = event.payload.place;
  return typeof place === "string" && place.trim() ? place : "Unknown region";
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
