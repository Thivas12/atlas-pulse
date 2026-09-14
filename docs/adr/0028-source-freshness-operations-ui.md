# ADR 0028: Render source freshness without inferring from visible events

- Status: accepted
- Date: 2026-09-14

## Context

ADR 0027 introduced an explicit `/v1/source-freshness` contract that evaluates the latest worker
poll and the most recent upstream timestamp independently. The dashboard still described the age
of events in its bounded `/v1/signals` response as “source freshness.” That value can be useful as
visible-event recency, but it cannot prove that a worker is polling. An unchanged upstream response
can be successfully polled and deduplicated without producing a newer visible event, while a recent
event can remain visible after its worker has stopped.

The dashboard also used a successful signal query to display `SYSTEM LIVE`. That collapsed
application reachability, durable-store readiness, source polling, and upstream data age into one
label. The public API already keeps those dimensions separate, so the browser must not rebuild a
weaker inferred status.

## Decision

AtlasPulse will render one compact Operations Beacon from the authoritative
`/v1/source-freshness` response.

1. The web client validates the complete response with a strict Zod contract. It checks literal
   schema and rule versions, canonical source order, field presence groups, failure-code semantics,
   threshold ordering, aggregate pass consistency, timestamp-derived ages, the fixed caveat, and
   the hard-disabled execution flag. Unknown fields or inconsistent values fail closed.
2. Every enabled source shows two independent states: poll heartbeat and upstream data age. The UI
   never replaces one dimension with the other and does not infer either from visible events.
3. The top-bar source label is `SOURCES CURRENT` only when every returned source passes both
   dimensions. Initial loading is `CHECKING SOURCES`; a non-passing valid response is
   `SOURCE ATTENTION`; transport, HTTP, or contract failure is `FRESHNESS UNKNOWN`.
4. The existing event-age metric remains available but is renamed `Visible event age` and states
   that it comes from a bounded view.
5. The endpoint is refreshed every ten seconds. This is a point-in-time operator display, not a
   continuous availability measurement, alerting service, or replacement for the content-addressed
   30-day operational evidence campaign.
6. The browser receives only the bounded credential-free freshness fields already exposed by the
   API. It receives no raw response body, source URL, exception text, secret, or container state.
7. This milestone changes observation only. It does not alter `/readyz`, grant approval, invoke a
   model or agent, perform a tool call, or enable any side effect.

## Consequences

- Operators can distinguish a worker failure from old upstream data without opening a raw API
  response.
- A schema drift or malformed response becomes an explicit unknown state instead of a false green
  status.
- The dashboard may show healthy polling beside an old visible event. That is intentional because
  deduplication and event visibility are different measurements.
- The panel is not an independent monitor: it is still based on worker-written Valkey state and the
  API host clock.

## Rejected alternatives

- Continue estimating source freshness from the newest visible event. This confuses publication
  with polling and creates false stale or false healthy conclusions.
- Fold source freshness into `/readyz`. An upstream publisher or worker can be degraded while the
  API, Valkey, and PostgreSQL remain operational.
- Display one combined source-health badge without its two dimensions. That hides whether the
  failing evidence is the poll heartbeat or the upstream timestamp.
- Preserve the last valid result as green after the endpoint fails. Without a fresh validated
  response, the UI must state that freshness is unknown.
