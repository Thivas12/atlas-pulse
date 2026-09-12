import { describe, expect, it } from "vitest";
import {
  alertTypeOf,
  fireConfidenceOf,
  fireConfidenceRankOf,
  fireRadiativePowerOf,
  formatTimestamp,
  isActiveAt,
  isFireDetection,
  isWeatherAlert,
  magnitudeOf,
  newestUpdate,
  placeOf,
  relativeAge,
  severityOf,
  severityRankOf,
  strongestMagnitude,
  titleOf,
} from "./event-utils";
import { makeEnvelope } from "./test/fixtures";

describe("event utilities", () => {
  it("reads validated payload values and rejects non-numeric magnitude", () => {
    const envelope = makeEnvelope({ magnitude: 5.25, place: "  Sunda Trench  " });
    expect(magnitudeOf(envelope.event)).toBe(5.25);
    expect(placeOf(envelope.event)).toBe("  Sunda Trench  ");

    envelope.event.payload.magnitude = "5.25";
    envelope.event.payload.place = "";
    expect(magnitudeOf(envelope.event)).toBeNull();
    expect(placeOf(envelope.event)).toBe("Unknown region");
  });

  it("finds the strongest magnitude and newest source update", () => {
    const older = makeEnvelope({ magnitude: 2.1, updatedAt: "2026-09-12T10:00:00Z" });
    const newer = makeEnvelope({
      streamId: "1001-0",
      magnitude: 6.4,
      updatedAt: "2026-09-12T10:30:00Z",
    });
    expect(strongestMagnitude([older, newer])).toBe(6.4);
    expect(newestUpdate([older, newer])?.toISOString()).toBe("2026-09-12T10:30:00.000Z");
  });

  it("handles missing and malformed measurements", () => {
    const item = makeEnvelope({ magnitude: null, updatedAt: null });
    expect(strongestMagnitude([item])).toBeNull();
    expect(newestUpdate([item])).toBeNull();
    expect(relativeAge(null)).toBe("—");
    expect(formatTimestamp("not-a-date")).toBe("Unknown time");
  });

  it("formats second, minute, and hour freshness without negative ages", () => {
    const now = new Date("2026-09-12T12:00:00Z");
    expect(relativeAge(new Date("2026-09-12T11:59:45Z"), now)).toBe("15s");
    expect(relativeAge(new Date("2026-09-12T11:45:00Z"), now)).toBe("15m");
    expect(relativeAge(new Date("2026-09-12T09:00:00Z"), now)).toBe("3h");
    expect(relativeAge(new Date("2026-09-12T12:01:00Z"), now)).toBe("0s");
  });

  it("formats timestamps explicitly in UTC", () => {
    expect(formatTimestamp("2026-09-12T10:02:03Z")).toContain("10:02:03");
  });

  it("reads weather classification and active-window fields safely", () => {
    const weather = makeEnvelope({
      source: "nws",
      alertType: "Tornado Warning",
      severity: "Extreme",
      expiresAt: "2026-09-12T13:00:00Z",
    });
    expect(isWeatherAlert(weather.event)).toBe(true);
    expect(alertTypeOf(weather.event)).toBe("Tornado Warning");
    expect(titleOf(weather.event)).toBe("Tornado Warning");
    expect(severityOf(weather.event)).toBe("Extreme");
    expect(severityRankOf(weather.event)).toBe(4);
    expect(isActiveAt(weather.event, new Date("2026-09-12T12:00:00Z"))).toBe(true);
    expect(isActiveAt(weather.event, new Date("2026-09-12T14:00:00Z"))).toBe(false);
  });

  it("falls back safely for incomplete source payloads", () => {
    const quake = makeEnvelope();
    expect(isWeatherAlert(quake.event)).toBe(false);
    expect(alertTypeOf(quake.event)).toBeNull();
    expect(titleOf(quake.event)).toBe("Test Ridge");
    expect(severityOf(quake.event)).toBeNull();
    expect(severityRankOf(quake.event)).toBe(0);
    expect(isActiveAt(quake.event)).toBe(true);

    const weather = makeEnvelope({ source: "nws" });
    weather.event.payload.alert_type = "";
    weather.event.payload.severity = "Impossible";
    weather.event.payload.expires_at = "not-a-date";
    expect(alertTypeOf(weather.event)).toBeNull();
    expect(severityOf(weather.event)).toBeNull();
    expect(isActiveAt(weather.event)).toBe(true);
    delete weather.event.payload.expires_at;
    expect(isActiveAt(weather.event)).toBe(true);
  });

  it("reads FIRMS measurements and applies the operational active window", () => {
    const fire = makeEnvelope({
      source: "firms",
      fireRadiativePowerMw: 18.4,
      fireConfidence: "High",
      expiresAt: "2026-09-12T13:00:00Z",
    });
    expect(isFireDetection(fire.event)).toBe(true);
    expect(fireRadiativePowerOf(fire.event)).toBe(18.4);
    expect(fireConfidenceOf(fire.event)).toBe("High");
    expect(fireConfidenceRankOf(fire.event)).toBe(3);
    expect(titleOf(fire.event)).toBe("High-confidence VIIRS thermal anomaly");
    expect(isActiveAt(fire.event, new Date("2026-09-12T12:00:00Z"))).toBe(true);
    expect(isActiveAt(fire.event, new Date("2026-09-12T14:00:00Z"))).toBe(false);

    fire.event.payload.fire_radiative_power_mw = "18.4";
    fire.event.payload.confidence = "Certain";
    fire.event.payload.title = "";
    expect(fireRadiativePowerOf(fire.event)).toBeNull();
    expect(fireConfidenceOf(fire.event)).toBeNull();
    expect(fireConfidenceRankOf(fire.event)).toBe(0);
    expect(titleOf(fire.event)).toBe("Test Ridge");
  });
});
