# ADR 0039: Execute only the retrieval channels required by each mode

## Status

Accepted

## Context

The first reviewed `live-disruptions.v2` development capture restored FIRMS coverage and showed
hybrid as the strongest overall ranking mode. It also reported p95 latency near two seconds for
every mode. The slowest query shapes were shared across lexical, dense, RRF, and hybrid.

Inspection found that every request embedded the query and executed both PostgreSQL full-text and
pgvector searches before the selected ranking rule ran. Consequently, a lexical ablation paid for
dense inference and SQL, while a dense ablation paid for lexical SQL. Their results used only the
selected channel, but their latency did not isolate that channel. This obscured diagnosis and did
unnecessary production work.

## Decision

1. Lexical mode executes only the PostgreSQL full-text channel and does not embed the query.
2. Dense mode embeds the query and executes only the pgvector channel.
3. RRF and hybrid continue to execute both channels inside one repeatable-read transaction before
   fusion, preserving their candidate consistency boundary.
4. Source, time, expiry, geometry, typed source-native constraints, candidate limits, stable
   ordering, embedding-model matching, ranking rules, and response contracts remain unchanged.
5. `candidates_considered` counts the union of the channels that actually ran. It therefore counts
   only selected-channel candidates for lexical and dense modes, and the dual-channel union for
   RRF and hybrid.
6. Retrieval spans record which channels ran. Client-observed latency remains end-to-end and is
   not presented as a database-operator benchmark.

## Consequences

- Lexical and dense captures measure their actual request paths without unused-channel work.
- Lexical requests no longer depend on query-embedding availability after the service has started.
- Hybrid and RRF outputs retain the shared-snapshot guarantee required for deterministic fusion.
- A fresh capture is required to attribute latency to the isolated paths. Existing capture
  latency remains valid for its deployed revision but is not directly comparable as operator cost.
- The change does not claim that hybrid p95 improves; both-channel queries may still require SQL
  or index tuning after the isolated measurement identifies the expensive channel.

## Rejected alternatives

- **Keep dual-channel execution for nominally fair ablations.** Fairness requires identical
  eligible documents and filters, not executing work whose candidates the selected mode discards.
- **Run both channels concurrently on separate connections.** That would lose the single-snapshot
  fusion boundary and add database pressure before the responsible channel is measured.
- **Add expression indexes immediately.** The current capture cannot distinguish lexical from
  dense database cost because both ran in every request. Index changes should follow isolated
  evidence rather than guesswork.
