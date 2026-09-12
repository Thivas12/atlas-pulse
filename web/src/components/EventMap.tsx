import {
  AttributionControl,
  type GeoJSONSource,
  type MapLayerMouseEvent,
  Map as MapLibreMap,
  NavigationControl,
} from "maplibre-gl";
import { useEffect, useRef } from "react";
import { eventsToGeoJson, incidentEdgesToGeoJson, weatherPolygonsToGeoJson } from "../geo";
import type { EventEnvelope, IncidentCandidate, ViewportBounds } from "../types";

const SOURCE_ID = "point-events";
const WEATHER_SOURCE_ID = "weather-polygons";
const INCIDENT_SOURCE_ID = "incident-edges";
const CLUSTER_LAYER = "seismic-clusters";
const CLUSTER_COUNT_LAYER = "seismic-cluster-count";
const GLOW_LAYER = "seismic-glow";
const EVENT_LAYER = "seismic-points";
const WEATHER_FILL_LAYER = "weather-alert-fills";
const WEATHER_OUTLINE_LAYER = "weather-alert-outlines";
const INCIDENT_GLOW_LAYER = "incident-edge-glow";
const INCIDENT_LAYER = "incident-edges";

interface EventMapProps {
  events: EventEnvelope[];
  incidents: IncidentCandidate[];
  selectedStreamId: string | null;
  selectedIncidentId: string | null;
  onSelect: (streamId: string) => void;
  onIncidentSelect: (incidentId: string) => void;
  onViewportChange: (bounds: ViewportBounds) => void;
}

export function EventMap({
  events,
  incidents,
  selectedStreamId,
  selectedIncidentId,
  onSelect,
  onIncidentSelect,
  onViewportChange,
}: EventMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const latestEventsRef = useRef(events);
  const latestIncidentsRef = useRef(incidents);
  const onSelectRef = useRef(onSelect);
  const onIncidentSelectRef = useRef(onIncidentSelect);
  const onViewportChangeRef = useRef(onViewportChange);
  latestEventsRef.current = events;
  latestIncidentsRef.current = incidents;
  onSelectRef.current = onSelect;
  onIncidentSelectRef.current = onIncidentSelect;
  onViewportChangeRef.current = onViewportChange;

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new MapLibreMap({
      container: containerRef.current,
      style: "https://tiles.openfreemap.org/styles/liberty",
      center: [5, 24],
      zoom: 1.6,
      minZoom: 1,
      attributionControl: false,
    });
    mapRef.current = map;
    map.addControl(new NavigationControl({ showCompass: false }), "bottom-left");
    map.addControl(
      new AttributionControl({
        compact: true,
        customAttribution: "USGS · NOAA/NWS · NASA FIRMS · GDELT · OpenFreeMap",
      }),
      "bottom-right",
    );

    const emitViewport = () => {
      const bounds = map.getBounds();
      const viewport = {
        west: Number(Math.max(-180, bounds.getWest()).toFixed(4)),
        south: Number(Math.max(-90, bounds.getSouth()).toFixed(4)),
        east: Number(Math.min(180, bounds.getEast()).toFixed(4)),
        north: Number(Math.min(90, bounds.getNorth()).toFixed(4)),
      };
      if (viewport.west < viewport.east && viewport.south < viewport.north) {
        onViewportChangeRef.current(viewport);
      }
    };

    map.on("load", () => {
      map.addSource(SOURCE_ID, {
        type: "geojson",
        data: eventsToGeoJson(latestEventsRef.current),
        cluster: true,
        clusterMaxZoom: 7,
        clusterRadius: 44,
      });
      map.addSource(WEATHER_SOURCE_ID, {
        type: "geojson",
        data: weatherPolygonsToGeoJson(latestEventsRef.current),
      });
      map.addSource(INCIDENT_SOURCE_ID, {
        type: "geojson",
        data: incidentEdgesToGeoJson(latestIncidentsRef.current),
      });
      map.addLayer({
        id: WEATHER_FILL_LAYER,
        type: "fill",
        source: WEATHER_SOURCE_ID,
        paint: {
          "fill-color": [
            "match",
            ["get", "severity"],
            "Extreme",
            "#ff4264",
            "Severe",
            "#ff7a59",
            "Moderate",
            "#f2c14e",
            "Minor",
            "#61aef2",
            "#7790a1",
          ],
          "fill-opacity": 0.3,
        },
      });
      map.addLayer({
        id: WEATHER_OUTLINE_LAYER,
        type: "line",
        source: WEATHER_SOURCE_ID,
        paint: {
          "line-color": [
            "match",
            ["get", "severity"],
            "Extreme",
            "#ff4264",
            "Severe",
            "#ff7a59",
            "Moderate",
            "#f2c14e",
            "Minor",
            "#61aef2",
            "#7790a1",
          ],
          "line-opacity": 0.9,
          "line-width": ["interpolate", ["linear"], ["get", "severityRank"], 0, 1, 4, 2.5],
        },
      });
      map.addLayer({
        id: INCIDENT_GLOW_LAYER,
        type: "line",
        source: INCIDENT_SOURCE_ID,
        paint: {
          "line-color": "#bd7cff",
          "line-width": 7,
          "line-opacity": 0.14,
          "line-blur": 4,
        },
      });
      map.addLayer({
        id: INCIDENT_LAYER,
        type: "line",
        source: INCIDENT_SOURCE_ID,
        paint: {
          "line-color": "#d7b2ff",
          "line-width": 2,
          "line-opacity": 0.82,
          "line-dasharray": [2, 2],
        },
      });
      map.addLayer({
        id: CLUSTER_LAYER,
        type: "circle",
        source: SOURCE_ID,
        filter: ["has", "point_count"],
        paint: {
          "circle-color": "#102f48",
          "circle-stroke-color": "#35e0a1",
          "circle-stroke-width": 1.5,
          "circle-radius": ["step", ["get", "point_count"], 17, 10, 22, 30, 28],
          "circle-opacity": 0.94,
        },
      });
      map.addLayer({
        id: CLUSTER_COUNT_LAYER,
        type: "symbol",
        source: SOURCE_ID,
        filter: ["has", "point_count"],
        layout: {
          "text-field": ["get", "point_count_abbreviated"],
          "text-font": ["Noto Sans Regular"],
          "text-size": 12,
        },
        paint: { "text-color": "#e9fff7" },
      });
      map.addLayer({
        id: GLOW_LAYER,
        type: "circle",
        source: SOURCE_ID,
        filter: ["!", ["has", "point_count"]],
        paint: {
          "circle-color": [
            "case",
            ["==", ["get", "source"], "firms"],
            "#ff7a3d",
            ["==", ["get", "source"], "gdelt"],
            "#bd7cff",
            "#35e0a1",
          ],
          "circle-radius": [
            "case",
            ["==", ["get", "source"], "gdelt"],
            [
              "interpolate",
              ["linear"],
              ["coalesce", ["get", "conflictPriorityRank"], 0],
              0,
              9,
              2,
              13,
              4,
              22,
            ],
            ["interpolate", ["linear"], ["coalesce", ["get", "magnitude"], 0], 0, 10, 3, 16, 6, 28],
          ],
          "circle-opacity": 0.16,
          "circle-blur": 0.7,
        },
      });
      map.addLayer({
        id: EVENT_LAYER,
        type: "circle",
        source: SOURCE_ID,
        filter: ["!", ["has", "point_count"]],
        paint: {
          "circle-color": [
            "case",
            ["==", ["get", "source"], "firms"],
            [
              "interpolate",
              ["linear"],
              ["coalesce", ["get", "fireConfidenceRank"], 0],
              0,
              "#f2c14e",
              2,
              "#ff8a3d",
              3,
              "#ff4264",
            ],
            ["==", ["get", "source"], "gdelt"],
            [
              "interpolate",
              ["linear"],
              ["coalesce", ["get", "conflictPriorityRank"], 0],
              0,
              "#7790a1",
              2,
              "#b79aff",
              3,
              "#a855f7",
              4,
              "#ff4264",
            ],
            [
              "interpolate",
              ["linear"],
              ["coalesce", ["get", "magnitude"], 0],
              0,
              "#35e0a1",
              3,
              "#f2c14e",
              5,
              "#ff7a59",
              7,
              "#ff4264",
            ],
          ],
          "circle-radius": [
            "case",
            ["==", ["get", "source"], "firms"],
            [
              "interpolate",
              ["linear"],
              ["coalesce", ["get", "fireRadiativePowerMw"], 0],
              0,
              4,
              25,
              6,
              100,
              10,
            ],
            ["==", ["get", "source"], "gdelt"],
            [
              "interpolate",
              ["linear"],
              ["coalesce", ["get", "conflictPriorityRank"], 0],
              0,
              4,
              2,
              5,
              3,
              7,
              4,
              10,
            ],
            ["interpolate", ["linear"], ["coalesce", ["get", "magnitude"], 0], 0, 4, 4, 7, 8, 12],
          ],
          "circle-stroke-color": ["case", ["==", ["get", "source"], "gdelt"], "#f1ddff", "#f3fff9"],
          "circle-stroke-width": 1,
          "circle-opacity": 0.95,
        },
      });
      emitViewport();
    });
    map.on("moveend", emitViewport);

    map.on("click", EVENT_LAYER, (event: MapLayerMouseEvent) => {
      const streamId = event.features?.[0]?.properties?.streamId;
      if (typeof streamId === "string") onSelectRef.current(streamId);
    });
    map.on("click", WEATHER_FILL_LAYER, (event: MapLayerMouseEvent) => {
      const streamId = event.features?.[0]?.properties?.streamId;
      if (typeof streamId === "string") onSelectRef.current(streamId);
    });
    map.on("click", INCIDENT_LAYER, (event: MapLayerMouseEvent) => {
      const incidentId = event.features?.[0]?.properties?.incidentId;
      if (typeof incidentId === "string") onIncidentSelectRef.current(incidentId);
    });
    map.on("click", CLUSTER_LAYER, async (event: MapLayerMouseEvent) => {
      const feature = event.features?.[0];
      const clusterId = feature?.properties?.cluster_id;
      if (typeof clusterId !== "number" || feature?.geometry.type !== "Point") return;
      const source = map.getSource(SOURCE_ID) as GeoJSONSource;
      const zoom = await source.getClusterExpansionZoom(clusterId);
      const [longitude, latitude] = feature.geometry.coordinates;
      map.easeTo({ center: [longitude, latitude], zoom });
    });
    for (const layer of [EVENT_LAYER, CLUSTER_LAYER, WEATHER_FILL_LAYER, INCIDENT_LAYER]) {
      map.on("mouseenter", layer, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", layer, () => {
        map.getCanvas().style.cursor = "";
      });
    }

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.isStyleLoaded()) return;
    const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined;
    source?.setData(eventsToGeoJson(events));
    const weatherSource = map.getSource(WEATHER_SOURCE_ID) as GeoJSONSource | undefined;
    weatherSource?.setData(weatherPolygonsToGeoJson(events));
  }, [events]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.isStyleLoaded()) return;
    const source = map.getSource(INCIDENT_SOURCE_ID) as GeoJSONSource | undefined;
    source?.setData(incidentEdgesToGeoJson(incidents));
  }, [incidents]);

  useEffect(() => {
    if (!selectedStreamId) return;
    const selected = events.find((item) => item.stream_id === selectedStreamId);
    if (!selected?.event.location) return;
    mapRef.current?.easeTo({
      center: [selected.event.location.longitude, selected.event.location.latitude],
      zoom: Math.max(mapRef.current.getZoom(), 5),
      duration: 850,
    });
  }, [events, selectedStreamId]);

  useEffect(() => {
    if (!selectedIncidentId) return;
    const selected = incidents.find((incident) => incident.incident_id === selectedIncidentId);
    if (!selected?.center) return;
    mapRef.current?.easeTo({
      center: [selected.center.longitude, selected.center.latitude],
      zoom: Math.max(mapRef.current.getZoom(), 4),
      duration: 850,
    });
  }, [incidents, selectedIncidentId]);

  return <section className="event-map" ref={containerRef} aria-label="Live disruption map" />;
}
