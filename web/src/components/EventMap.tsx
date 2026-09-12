import {
  AttributionControl,
  type GeoJSONSource,
  type MapLayerMouseEvent,
  Map as MapLibreMap,
  NavigationControl,
} from "maplibre-gl";
import { useEffect, useRef } from "react";
import { eventsToGeoJson, weatherPolygonsToGeoJson } from "../geo";
import type { EventEnvelope } from "../types";

const SOURCE_ID = "point-events";
const WEATHER_SOURCE_ID = "weather-polygons";
const CLUSTER_LAYER = "seismic-clusters";
const CLUSTER_COUNT_LAYER = "seismic-cluster-count";
const GLOW_LAYER = "seismic-glow";
const EVENT_LAYER = "seismic-points";
const WEATHER_FILL_LAYER = "weather-alert-fills";
const WEATHER_OUTLINE_LAYER = "weather-alert-outlines";

interface EventMapProps {
  events: EventEnvelope[];
  selectedStreamId: string | null;
  onSelect: (streamId: string) => void;
}

export function EventMap({ events, selectedStreamId, onSelect }: EventMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const latestEventsRef = useRef(events);
  const onSelectRef = useRef(onSelect);
  latestEventsRef.current = events;
  onSelectRef.current = onSelect;

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
      new AttributionControl({ compact: true, customAttribution: "USGS · NOAA/NWS · OpenFreeMap" }),
      "bottom-right",
    );

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
          "circle-color": "#35e0a1",
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["coalesce", ["get", "magnitude"], 0],
            0,
            10,
            3,
            16,
            6,
            28,
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
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["coalesce", ["get", "magnitude"], 0],
            0,
            4,
            4,
            7,
            8,
            12,
          ],
          "circle-stroke-color": "#f3fff9",
          "circle-stroke-width": 1,
          "circle-opacity": 0.95,
        },
      });
    });

    map.on("click", EVENT_LAYER, (event: MapLayerMouseEvent) => {
      const streamId = event.features?.[0]?.properties?.streamId;
      if (typeof streamId === "string") onSelectRef.current(streamId);
    });
    map.on("click", WEATHER_FILL_LAYER, (event: MapLayerMouseEvent) => {
      const streamId = event.features?.[0]?.properties?.streamId;
      if (typeof streamId === "string") onSelectRef.current(streamId);
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
    for (const layer of [EVENT_LAYER, CLUSTER_LAYER, WEATHER_FILL_LAYER]) {
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
    if (!selectedStreamId) return;
    const selected = events.find((item) => item.stream_id === selectedStreamId);
    if (!selected?.event.location) return;
    mapRef.current?.easeTo({
      center: [selected.event.location.longitude, selected.event.location.latitude],
      zoom: Math.max(mapRef.current.getZoom(), 5),
      duration: 850,
    });
  }, [events, selectedStreamId]);

  return <section className="event-map" ref={containerRef} aria-label="Live disruption map" />;
}
