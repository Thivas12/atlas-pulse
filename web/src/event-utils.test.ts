import { describe, expect, it } from "vitest";
import {
  formatTimestamp,
  magnitudeOf,
  newestUpdate,
  placeOf,
  relativeAge,
  strongestMagnitude,
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
});
