import { describe, expect, it } from "vitest";
import { eventsToGeoJson, incidentEdgesToGeoJson, weatherPolygonsToGeoJson } from "./geo";
import { makeEnvelope, makeIncident } from "./test/fixtures";

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

  it("maps GDELT priority separately from seismic and fire measurements", () => {
    const conflict = makeEnvelope({
      source: "gdelt",
      conflictPriority: "Critical",
      location: { latitude: 31.7683, longitude: 35.2137, altitude_km: null },
    });

    expect(eventsToGeoJson([conflict]).features[0]).toMatchObject({
      geometry: { type: "Point", coordinates: [35.2137, 31.7683] },
      properties: {
        source: "gdelt",
        magnitude: null,
        fireRadiativePowerMw: null,
        fireConfidenceRank: 0,
        conflictPriorityRank: 4,
      },
    });
  });

  it("omits area-only and malformed alert geometry from the polygon layer", () => {
    const areaOnly = makeEnvelope({ source: "nws", geometry: null, location: null });
    const malformed = makeEnvelope({ source: "nws", geometry: { type: "Polygon" } });
    expect(weatherPolygonsToGeoJson([areaOnly, malformed]).features).toEqual([]);
  });

  it("maps evidence-graph edges between source focus points", () => {
    const collection = incidentEdgesToGeoJson([makeIncident()]);

    expect(collection.features[0]).toMatchObject({
      id: "edge-test123",
      geometry: {
        type: "LineString",
        coordinates: [
          [77, 12],
          [77.05, 12.05],
        ],
      },
      properties: {
        incidentId: "incident-test123",
        distanceKm: 7.75,
        timeDeltaMinutes: 5,
        spatialRelation: "within_radius",
      },
    });
  });

  it("omits graph edges whose source focus point is unavailable", () => {
    const incident = makeIncident();
    incident.nodes[0].event.location = null;
    expect(incidentEdgesToGeoJson([incident]).features).toEqual([]);
  });

  it("splits evidence lines at the antimeridian instead of drawing across the world", () => {
    const incident = makeIncident();
    incident.nodes[0].event.location = {
      latitude: 10,
      longitude: 179,
      altitude_km: null,
    };
    incident.nodes[1].event.location = {
      latitude: 12,
      longitude: -179,
      altitude_km: null,
    };

    expect(incidentEdgesToGeoJson([incident]).features[0]?.geometry).toEqual({
      type: "MultiLineString",
      coordinates: [
        [
          [179, 10],
          [180, 11],
        ],
        [
          [-180, 11],
          [-179, 12],
        ],
      ],
    });
  });
});
