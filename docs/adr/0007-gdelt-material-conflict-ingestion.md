# ADR 0007: Ingest GDELT material-conflict observations with explicit uncertainty

- Status: Accepted
- Date: 2026-09-12

## Context

AtlasPulse needs a free, global geopolitical signal that complements physical hazards. GDELT 2.0
publishes a keyless Event export every 15 minutes and provides a latest-update manifest containing
the archive size, MD5 checksum, and URL. Each export is a ZIP containing one tab-delimited,
61-column Event file despite its `.CSV` name.

The feed is machine-coded from news coverage. A row is a media observation, not an independently
verified physical incident. Its action location can be a centroid, `SQLDATE` has only daily event
precision, `DATEADDED` is the 15-minute database-ingestion timestamp, and the Goldstein scale is a
theoretical category-level impact score rather than measured severity. AtlasPulse must not erase
those distinctions or turn coverage volume into an invented risk score.

The source also introduces a binary archive boundary. Trusting a manifest URL or ZIP metadata
without validation could enable server-side request forgery, decompression bombs, path traversal,
or unbounded memory and CPU use.

## Decision

Poll the official `https://data.gdeltproject.org/gdeltv2/lastupdate.txt` endpoint every 15 minutes.
Accept exactly one advertised Event export, require the official host and filename pattern, reject
credentials, query strings, fragments, and nonstandard ports, and upgrade the advertised URL to
HTTPS. Stream the response under a compressed-byte limit and require both the advertised byte
length and MD5 checksum to match before parsing.

Open the archive in memory only after integrity validation. Require one unencrypted, non-directory
member whose timestamp and filename match the manifest. Bound compressed bytes, declared and
actual expanded bytes, total rows, and selected records. Decode strict UTF-8, require exactly 61
tab-separated columns per non-empty row, and validate IDs, UTC timestamps, CAMEO codes, numeric
ranges, WGS84 coordinates, and geography fields. Snapshot the original verified ZIP through the
existing content-addressed evidence boundary before normalization.

Select only records that meet all default operational criteria:

- CAMEO `QuadClass=4` material conflict;
- `IsRootEvent=1`, avoiding subordinate duplicate mentions;
- action geography type 3 or better, so a point is no broader than a city, landmark, or
  administrative-area centroid; and
- at least one GDELT mention.

Normalize each row to `geopolitical.gdelt_event` with `GlobalEventID` as the stable identity.
Preserve the CAMEO event/base/root codes, QuadClass, Goldstein scale, actors, mention/source/article
counts, average tone, reported event date, geography precision and codes, first-report URL, and
official dataset URL. If the report URL is unsafe, expose the official dataset page instead.

Use `DATEADDED` as `occurred_at` because it is the only sub-day operational timestamp, but label it
**Detected** everywhere in the UI and retain `SQLDATE` as `reported_event_date`. Apply a configurable
24-hour live-display window from detection time; this is an AtlasPulse freshness rule, not a claim
that the real-world event ended.

Expose a transparent display priority derived only from CAMEO root categories: protest through
coercion (14–17) are Elevated/rank 2, assault and fight (18–19) are High/rank 3, and mass violence
(20) is Critical/rank 4. Preserve Goldstein separately and do not combine it with coverage or tone
into an opaque score.

Every dashboard detail must identify the record as a machine-coded media observation that may
contain reporting, NLP, or geocoding errors and is not an independently verified incident.

## Consequences

- AtlasPulse gains a free, keyless global geopolitical signal on the existing stream, PostGIS,
  spatial API, replay, and evidence boundaries.
- Integrity and resource checks make the compressed-data boundary deterministic and bounded.
- Provider IDs keep repeated observations idempotent while revised payloads remain immutable
  stream revisions.
- Detection time, report date, operational expiry, category priority, and Goldstein meaning remain
  distinguishable to operators and downstream retrieval or agent systems.
- Root-event and geography filters intentionally trade recall for a less noisy operational map.
- The latest-update manifest exposes only the newest export. If AtlasPulse is down across multiple
  publication intervals, it can miss exports; replay proves received observations, not complete
  GDELT history. Historical backfill requires a separate, future workflow.
- A media observation can duplicate another source, describe rhetoric rather than ground truth, or
  carry an imprecise centroid. Cross-source entity resolution and corroboration remain later work.

## References

- [GDELT 2.0 announcement](https://blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime/)
- [GDELT 2.0 Event codebook](https://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf)
- [GDELT data access](https://www.gdeltproject.org/data.html)
- [GDELT latest-update manifest](https://data.gdeltproject.org/gdeltv2/lastupdate.txt)
