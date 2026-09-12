# ADR 0003: Use exclusive stream cursors for deterministic replay

- Status: Accepted
- Date: 2026-09-12

## Context

AtlasPulse needs historical playback for the operator dashboard and future agent evaluation.
Offset pagination is unstable while a live stream grows, and loading the entire retained stream
would make memory and response time scale with history. The first event-bus implementation
already assigns every immutable event revision a monotonically ordered Valkey Stream ID.

## Decision

Expose `GET /v1/events/replay` as an oldest-first cursor API. The optional `after` value is a
Valkey Stream ID with the form `<milliseconds>-<sequence>`. Read with `XRANGE` from the exclusive
lower bound `(<after>`, requesting `limit + 1` entries. Return at most `limit` entries, use the
last visible ID as `next_cursor`, and set `has_more` from the extra entry.

Keep the browser's received pages in order and express playback as an index over that immutable
client-side sequence. Prefetch the next page near the current boundary. Validate response data
with Zod before it reaches map or timeline state.

## Consequences

- Page boundaries cannot duplicate the cursor entry.
- The same retained cursor produces the same subsequent ordering without an offset scan.
- New live entries can extend the tail during a replay; this is intentional, not snapshot
  isolation.
- Approximate stream trimming can remove old cursors and events. Raw source snapshots remain the
  audit record, but rebuilding an expired replay requires a future snapshot reprocessor.
- Cursor syntax is transport-visible, so a future event-bus migration must preserve opaque cursor
  behavior at the API boundary even if its internal position format changes.
