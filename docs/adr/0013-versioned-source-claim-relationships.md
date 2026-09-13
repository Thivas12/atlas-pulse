# ADR 0013: Annotate measured edges with versioned source-claim relationships

## Status

Accepted

## Context

The `spatiotemporal-v1` evidence graph deliberately proves only that signals from different
sources occurred within bounded distance and time. That baseline is useful for triage, but an
operator still has to discover whether the source records make a comparable claim. Treating
proximity, embedding similarity, or fluent model output as corroboration would collapse three
different questions: whether records are near each other, whether they say the same thing, and
whether that thing is true.

The first semantic layer must therefore remain reproducible, inspectable, and replaceable. It
also needs an honest abstention state. The four current sources have heterogeneous semantics:
FIRMS reports thermal anomalies, GDELT reports machine-coded media observations, NWS publishes
CAP alerts, and USGS publishes measured earthquakes. Their uncertainty cannot be erased by one
generic confidence score.

## Decision

Add a separate `structured-claims-v1` analysis to every returned incident candidate. It consumes
the immutable graph nodes and edges after deterministic correlation and never changes node,
edge, or incident identity.

1. Extract a small, versioned claim ontology from explicit event types and source fields. The
   first predicates are `hazard_domain`, `evacuation_state`, and `road_access_state`.
2. Preserve a stable claim ID, node ID, predicate, normalized value, comparison scope, exact
   source field, bounded evidence excerpt, source-specific qualifier, and extractor version.
3. Compare claims only across the endpoints of an existing measured edge. Hazard-domain claims
   use the edge area as their deliberately broad scope. Operational-state claims require the
   exact same place after conservative case and punctuation normalization.
4. Emit `corroborates` only for exact normalized agreement at the same predicate and scope. Emit
   `contradicts` only for an allow-listed mutually exclusive state pair: active versus lifted
   evacuation, or closed versus open road access.
5. If no decisive pair exists, emit `insufficient_evidence`. Different hazard domains are not a
   contradiction because multiple disruptions can coexist.
6. Drop an operational claim when one node contains both sides of the state rule. Do not choose a
   convenient sentence from internally ambiguous evidence.
7. Return stable relationship IDs, parent edge IDs, claim IDs, rule basis, rationale, counts,
   version, and a caveat through FastAPI and Zod. Display both normalized claims and source-field
   provenance in the command center.
8. Treat all labels as relationship annotations. Corroboration is not verification of truth or
   proof that two records describe one incident. Contradiction is a review flag, not an automated
   adjudication. Count annotations, not probabilities.

The initial rule set is intentionally symbolic and CPU-only. A future local NLI model or LLM may
propose additional claim pairs only behind a new version, a reviewed benchmark, explicit model
provenance, and an abstention threshold. Model output may not rewrite `spatiotemporal-v1` facts.

## Consequences

- Identical graph evidence produces identical claim and relationship IDs without a paid API,
  network call, or model download.
- Operators can trace every non-abstaining comparison to two source fields and its measured edge.
- FIRMS-to-NWS fire agreement is explicitly qualified: a satellite thermal anomaly remains a
  thermal anomaly rather than being silently promoted to a verified wildfire.
- USGS records carrying the source tsunami flag can agree with tsunami-related NWS alerts at the
  broad hazard-domain predicate without claiming observed tsunami impact.
- Current contradiction coverage is deliberately narrow. Many real contradictions require
  entity resolution or natural-language inference and must remain unresolved until evaluated.
- Phrase rules can miss paraphrases. That is a measured baseline for later models, not a reason to
  broaden regexes until they become unreviewable.
- Relationship counts can exceed edge counts when one edge supports more than one comparable
  predicate. An unresolved annotation is emitted only when its edge has no decisive comparison.

## Rejected alternatives

- **Use embedding similarity as corroboration.** Similar language can describe disagreement, and
  a similarity score has no entailment direction.
- **Ask an LLM to decide every edge immediately.** There is no reviewed claim-pair benchmark or
  calibrated abstention threshold yet, so this would manufacture authority from fluency.
- **Call different normalized domains contradictory.** Fire, conflict, weather, and earthquake
  signals can legitimately coexist at the same place and time.
- **Infer operational scope from approximate coordinates alone.** A road or evacuation assertion
  needs a named subject; proximity does not make two operational claims comparable.
- **Store annotations inside measured edges.** That would mix model/rule revisions with invariant
  PostGIS measurements and make audit history ambiguous.
