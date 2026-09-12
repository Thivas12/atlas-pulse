import { describe, expect, it } from "vitest";
import { eventsToGeoJson, weatherPolygonsToGeoJson } from "./geo";
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

  it("keeps weather source polygons separate from clustered point events", () => {
    const polygon = {
      type: "Polygon" as const,
      coordinates: [
        [
          [-98, 34],
          [-96, 34],
          [-96, 36],
          [-98, 34],
        ],
      ],
    };
    const weather = makeEnvelope({
      streamId: "2000-0",
      source: "nws",
      eventId: "weather-1",
      location: { latitude: 35, longitude: -97, altitude_km: null },
      geometry: polygon,
    });

    expect(eventsToGeoJson([weather]).features).toEqual([]);
    expect(weatherPolygonsToGeoJson([weather]).features[0]).toMatchObject({
      id: "2000-0",
      geometry: polygon,
      properties: {
        source: "nws",
        severity: "Severe",
        severityRank: 3,
        title: "Severe Thunderstorm Warning",
      },
    });
  });

  it("maps FIRMS point measurements without inventing earthquake magnitude", () => {
    const fire = makeEnvelope({
      source: "firms",
      fireRadiativePowerMw: 18.4,
      fireConfidence: "High",
      location: { latitude: 34.1235, longitude: -118.5432, altitude_km: null },
    });

    expect(eventsToGeoJson([fire]).features[0]).toMatchObject({
      geometry: { type: "Point", coordinates: [-118.5432, 34.1235] },
      properties: {
        source: "firms",
        magnitude: null,
        fireRadiativePowerMw: 18.4,
        fireConfidenceRank: 3,
      },
    });
  });

  it("omits area-only and malformed alert geometry from the polygon layer", () => {
    const areaOnly = makeEnvelope({ source: "nws", geometry: null, location: null });
    const malformed = makeEnvelope({ source: "nws", geometry: { type: "Polygon" } });
    expect(weatherPolygonsToGeoJson([areaOnly, malformed]).features).toEqual([]);
  });
});
