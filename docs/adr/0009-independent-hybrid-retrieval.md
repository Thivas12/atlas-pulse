# ADR 0009: Isolate and expose hybrid retrieval

- Status: Accepted
- Date: 2026-09-12

## Context

AtlasPulse needs semantic retrieval before an LLM or agent can safely reason over live evidence.
The source stream is revisioned and auditable, while the canonical PostGIS projector must remain
fast and available even if a local embedding model is cold, slow, or broken. Keyword-only search
misses paraphrases; vector-only search can miss exact identifiers and gives scores that are not
directly comparable with lexical scores. Retrieval also has to preserve time, geography, source,
expiry, and citation boundaries.

All inference must remain free to run. The chosen
[BAAI BGE small English model](https://huggingface.co/BAAI/bge-small-en-v1.5) is MIT-licensed,
384-dimensional, and runs locally through [FastEmbed](https://github.com/qdrant/fastembed) and
ONNX Runtime. [pgvector](https://github.com/pgvector/pgvector) documents PostgreSQL full-text
search plus vector search as a hybrid pattern and explicitly calls out Reciprocal Rank Fusion.

## Decision

Create a second restart-safe stream projection named `hybrid-retrieval-v1`.

1. The retrieval indexer reads strictly after its own PostgreSQL checkpoint.
2. It deterministically renders each normalized event into a bounded passage, embeds a batch on
   CPU, then atomically upserts the current document and checkpoint.
3. PostgreSQL stores generated English `tsvector` data with a GIN index and 384-dimensional
   vectors with a cosine HNSW index. PostGIS point and polygon columns retain the same spatial
   evidence boundary as current signals.
4. A query applies source, expiry, aware timestamp, bounding-box, and radius filters to both
   channels. Dense search also requires the indexed and query embedding-model identities to
   match. Both ranked lists are read in one repeatable-read transaction.
5. Reciprocal Rank Fusion with `k=60` combines ranks. A deterministic reranker then uses only
   inspectable exact-phrase and query-token coverage features. The API returns every component.
6. Citation validation accepts only public HTTP(S) URLs without embedded credentials or
   credential-like query fields and attaches the source/event identity. It does not fetch the URL
   or claim that the underlying statement is true.
7. `/v1/search` returns ranked source events. It deliberately does not generate an answer.

The application image preloads Qdrant's ONNX export at immutable Hugging Face revision
`52398278842ec682c6f32300af41344b1c0b0bb2`, then forces local-files-only inference. A small custom
PostgreSQL image builds pgvector `0.8.6` from commit
`8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c` on the same PostgreSQL 17/PostGIS base instead of
trusting an unrelated combined image.

## Consequences

- Embedding latency and failure are isolated from ingestion and canonical current-state
  projection.
- A failed batch leaves the retrieval checkpoint unchanged and is safely replayed.
- Corrections replace the current retrieval document only when their stream position is newer;
  immutable revision history remains in `event_revisions` and Valkey. Replaying the same revision
  may also replace its derived document when the renderer or embedding-model identity changes.
- A partial model migration cannot compare vectors from different embedding spaces; unmatched
  rows remain available to lexical retrieval while the independent projection catches up.
- Rank fusion avoids pretending PostgreSQL FTS rank and cosine similarity share a calibrated
  scale.
- Search is reproducible for a fixed current index, query, filters, model, and rule version.
- The API and indexer each hold one small local model in memory. The production image is larger,
  and its first Docker build downloads model artifacts.
- English stemming and an English-only embedding model are explicit current limitations.
- HNSW is approximate. PostgreSQL filtering uses pgvector iterative scans, bounded candidates,
  and stable tie-breakers; the response never implies exhaustive recall.

## Rejected alternatives

- A hosted embedding API adds cost, secrets, network failure, and evidence leaving the machine.
- Embedding inside the canonical projector couples model availability to live-state correctness.
- Lexical-only search misses semantic paraphrases.
- Vector-only search weakens exact identifier and phrase retrieval.
- Adding a cross-encoder now would increase image size and latency before a measured need exists.
  The reranker interface and rule version leave room for an evaluated local cross-encoder later.
- Generating an answer in the retrieval endpoint would mix evidence selection with model claims
  before grounding and citation evaluations exist.
