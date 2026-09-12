# ADR 0006: Treat FIRMS pixels as expiring thermal-anomaly evidence

- Status: Accepted
- Date: 2026-09-12

## Context

AtlasPulse needs fire signals with global geographic coverage, actual observations, and a free
production path. NASA FIRMS distributes VIIRS active-fire and thermal-anomaly detections in near
real time. Its Area API returns CSV and requires a free `MAP_KEY` in the URL path.

A FIRMS row has no provider-issued event ID or incident perimeter. The same pixel can appear in
successive polls, measurements can be corrected, and a thermal anomaly is not proof of a
wildfire. Putting the path credential in ordinary URLs also risks leaking it through source
metadata, exceptions, traces, or unrelated containers.

## Decision

Add an opt-in `firms` source adapter for the NOAA-20, NOAA-21, and Suomi-NPP VIIRS NRT products.
Default to one day of global NOAA-20 observations polled every 15 minutes. Preserve each complete
CSV response before parsing, require the documented VIIRS columns, validate WGS84 coordinates,
UTC acquisition time, confidence, instrument, radiative power, brightness, scan/track, and
day/night values, then normalize every row as `fire.thermal_anomaly`.

Derive a stable event ID from product, satellite, instrument, acquisition minute, and coordinates.
Exclude confidence, brightness, and radiative power from identity: if NASA revises those values,
the event bus retains a new immutable revision under the same source identity. Retain all source
measurements in the payload and project the source point through the existing PostGIS boundary.

Apply a configurable 24-hour `expires_at` value from the observation time. This controls the
operational live view and is labeled as an AtlasPulse display window; it is not represented as a
NASA incident-closure time. The immutable revision remains available to replay.

Keep FIRMS disabled until `ATLAS_FIRMS_ENABLED=true` and a non-empty `ATLAS_FIRMS_MAP_KEY` are
provided. Pass the credential only to the ingestor container. Return a public FIRMS map URL in
document metadata and event evidence, convert HTTP failures into URL-free exceptions, exclude
credential-bearing requests from automatic HTTP client spans, and retain a redaction hook as
defense in depth for both legacy and current OpenTelemetry URL attributes.

## Consequences

- AtlasPulse gains global, observed satellite heat signals without a paid service.
- The map and API can spatially query FIRMS through the same durable contract as existing sources.
- Corrections remain auditable while repeated rows are atomically deduplicated.
- Operators must request and safely configure a free NASA key before enabling this source.
- Global daily CSV responses and raw snapshots can be large; content addressing prevents
  byte-identical copies, and operators may narrow `ATLAS_FIRMS_AREA`.
- UI language must say "thermal anomaly" rather than claiming a confirmed fire or perimeter.
