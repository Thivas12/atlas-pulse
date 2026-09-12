import { describe, expect, it } from "vitest";
import { eventsToGeoJson } from "./geo";
import { makeEnvelope } from "./test/fixtures";

describe("eventsToGeoJson", () => {
  it("maps located events into longitude-first GeoJSON", () => {
    const collection = eventsToGeoJson([
      makeEnvelope({
        streamId: "1234-1",
        magnitude: 3.7,
        place: "Carlsberg Ridge",
        location: { latitude: -4.1, longitude: 67.2, altitude_km: -8.3 },
      }),
    ]);

    expect(collection.type).toBe("FeatureCollection");
    expect(collection.features[0]).toMatchObject({
      id: "1234-1",
      geometry: { type: "Point", coordinates: [67.2, -4.1] },
      properties: {
        streamId: "1234-1",
        magnitude: 3.7,
        place: "Carlsberg Ridge",
        depthKm: 8.3,
      },
    });
  });

  it("omits events that cannot be mapped", () => {
    expect(eventsToGeoJson([makeEnvelope({ location: null })]).features).toEqual([]);
  });
});
