# ADR 0029: Expose bounded source-poll recovery history

- Status: accepted
- Date: 2026-09-14

## Context

ADR 0027 records every accepted source-poll start and terminal outcome in a bounded Valkey Stream,
while `/v1/source-freshness` exposes only the latest state for each enabled source. That current
state correctly separates poll heartbeat from upstream-data age, but it cannot show the sequence
around an incident. Once a later attempt succeeds, the prior failure and intervening start are no
longer visible in the point-in-time response.

Operators need to inspect that retained sequence without gaining access to source URLs, raw
payloads, exception text, credentials, or container state. The history must remain diagnostic: it
cannot become a readiness dependency, an independent availability observation, or a substitute for
the content-addressed operational evidence campaign and its explicit recovery drills.

## Decision

AtlasPulse will expose the existing bounded poll-transition stream through `GET /v1/source-polls`
and render its first page as a compact Recovery Evidence timeline.

1. The API returns at most 100 transitions in strict newest-first Valkey Stream order. `before` is
   an exclusive cursor, and `next_cursor` identifies the oldest item returned so adjacent pages do
   not repeat their boundary entry.
2. Every record contains only the stream ID, source, transition kind, and the existing strict
   `SourcePollAttempt`: attempt identity, bounded stage and failure code, timestamps, actual
   transport-attempt count, source timestamp basis, and event counters. The adapter rejects missing
   or additional stream fields and revalidates the nested attempt before returning it.
3. A transition and its attempt outcome must agree: `started` maps to `in_progress`, `succeeded` to
   `succeeded`, and `failed` to `failed`. The response also rejects duplicate or reordered stream
   positions, a mismatched count or cursor, and any enabled execution flag.
4. The browser requests 12 records, validates the complete response independently with a strict Zod
   contract, and refreshes every ten seconds. Contract, transport, or HTTP failure produces an
   explicit unavailable state; it does not invalidate or replace the separate freshness panel.
5. The UI labels terminal success as `Succeeded`. It does not infer that a source recovered merely
   because a success is newer than a failure; reviewers can inspect the shared attempt identities
   and sequence directly.
6. Stream retention remains the configured approximate maximum. Older transitions may expire, so
   an absent record does not prove that an attempt never occurred or that history is complete.
7. This observation surface does not alter `/readyz`, operational-campaign scoring, release gates,
   approval state, model or agent invocation, or the hard-disabled execution boundary.

## Consequences

- Operators can distinguish failure, next start, and later success transitions without accessing
  raw source material or logs.
- Pagination is deterministic for the retained snapshot boundary, but concurrent newer writes and
  approximate retention mean the endpoint is not a durable audit log.
- Current freshness and retained history can fail independently in the UI. That separation avoids
  converting a diagnostic read into a health verdict.
- The first page is intentionally small; older retained entries remain available to API clients by
  following the exclusive cursor.

## Rejected alternatives

- Add prior failures to `/v1/source-freshness`. That would mix point-in-time evaluation with an
  unbounded temporal concern and complicate the strict freshness contract.
- Infer a `recovered` event by pairing a failure with a later success. The retained sequence alone
  does not establish incident scope, continuous recovery, or upstream completeness.
- Return raw Valkey fields or worker exceptions. That would expose implementation details and risk
  leaking URLs, response content, or credentials.
- Make transition history part of `/readyz` or the 30-day campaign. The ledger and API share the
  deployment being observed and are not independent evidence of availability or recovery.
