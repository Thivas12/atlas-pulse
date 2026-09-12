# ADR 0002: Snapshot source bytes before validation

- Status: Accepted
- Date: 2026-09-12

## Context

Public feeds change, occasionally emit malformed records, and can later revise events. Saving
only normalized records would erase the evidence needed to reproduce parser failures or prove
what the source returned at ingestion time.

## Decision

After a successful HTTP response, write the exact body to content-addressed storage before
parsing it. Use SHA-256 as the filename, partition by source and UTC fetch date, create files
exclusively, and verify existing bytes on a same-path write. Parsing and stream publication
happen only after this evidence boundary succeeds.

## Consequences

- Invalid payloads remain available for incident analysis and schema-drift fixtures.
- Identical polls do not create duplicate files.
- Normalized events can be traced back to immutable input content.
- Retention, object-storage replication, and snapshot metadata indexes must be added before a
  multi-source production deployment.
