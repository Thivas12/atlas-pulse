# ADR 0008: Build deterministic non-causal evidence graphs at query time

- Status: Accepted
- Date: 2026-09-12

## Context

AtlasPulse now projects four heterogeneous public sources into one durable PostGIS current-state
model. Operators need a way to notice nearby cross-source signals without hiding the evidence
behind an opaque score or allowing a language model to invent relationships. Earthquake points,
weather polygons, satellite thermal pixels, and machine-coded media observations also carry very
different uncertainty. Proximity is useful for triage, but it is not proof that two observations
describe the same incident or that one caused the other.

Persisting inferred incidents would introduce a second state machine, invalidation rules, and a
migration before the correlation semantics have been evaluated. An unbounded spatial self-join
could also overwhelm a local, free deployment when a source produces a dense burst.

## Decision

Expose `GET /v1/incidents` as a bounded query over projected current state. Version the first rule
as `spatiotemporal-v1` and create an edge only when all of these conditions hold:

1. The endpoints come from different sources.
2. Both endpoints have source-backed point or polygon geometry.
3. Their occurrence times differ by no more than the requested window.
4. PostGIS geography distance is no greater than the requested radius.
5. Both signals satisfy lookback, activity, and optional viewport bounds.

Prefer a source polygon over its display focus point when both exist. Measure distance with
PostGIS geography so the API reports kilometres rather than treating WGS84 degrees as planar
units. Return the endpoint geometry basis with every edge. The browser draws an edge between
focus points as a visual guide; it does not replace the backend measurement.

Construct connected components with deterministic union-find logic after the database returns
candidate pairs. Node identities use `source:event_id`; edge identities hash the ordered endpoint
identities and rule version; component identities hash sorted membership and rule version. Sort
components by newest participating occurrence time and stable ID. Calculate display centers with
an antimeridian-safe spherical mean.

Apply hard API bounds: radius `(0, 500]` km, pair window `1..1440` minutes, lookback `1..168`
hours, candidate edges `1..5000`, and returned components `1..100`. Fetch one extra edge to expose
candidate truncation. Return both edge and component truncation flags plus the exact effective
parameters.

Every response and detail panel states that an edge proves bounded spatial and temporal
co-occurrence only. It does not establish causation, corroboration, or a shared real-world
incident. Preserve full normalized events and evidence URLs on graph nodes so a human or future
agent can inspect the original claims.

## Consequences

- Identical current evidence produces identical graph topology and IDs independent of row order.
- Correlation needs no paid model, vector database, or new persisted inference table.
- Pair measurements and every source claim remain inspectable at the API and UI boundaries.
- Source revisions update node content while stable provider identity keeps topology traceable.
- A chain can connect endpoints that are farther apart than the pair radius; every included edge
  still satisfies the rule, and the UI exposes each edge rather than implying all-to-all proximity.
- Query-time graphs represent current projected evidence, not immutable historical graph
  snapshots. Deterministic replay of graph state requires a separate future projection.
- Hitting the candidate-edge cap can split or omit components. The response exposes this condition
  and the dashboard asks the operator to narrow the viewport.
- Focus-point rendering can look longer than a polygon-to-point backend distance. Geometry-basis
  metadata makes that distinction explicit.
- Semantic similarity, entity resolution, contradiction flags, learned scores, and causal claims
  are outside this rule. Future systems must add separately versioned evidence layers rather than
  mutate measured `spatiotemporal-v1` edges.

## Rejected alternatives

- **Ask an LLM whether two events match.** This is non-deterministic, difficult to calibrate,
  potentially costly, and can fabricate evidence before a measured baseline exists.
- **Persist candidate incidents immediately.** This adds invalidation and lifecycle semantics
  before the rule has evaluation data or a stable product contract.
- **Correlate events from the browser.** This would require downloading excessive state and would
  duplicate geospatial logic without PostGIS geography accuracy.
- **Call every component a verified incident.** Connected proximity is an investigation lead, not
  corroboration.
