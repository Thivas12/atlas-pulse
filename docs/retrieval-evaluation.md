# Hybrid retrieval evaluation boundary

Day 9 evaluates retrieval before adding generation. The quality gate is deliberately split into
deterministic unit cases and real PostgreSQL integration cases.

| Property | Frozen proof |
| --- | --- |
| Document identity | Same normalized event renders the same bounded text and SHA-256 hash |
| Exact retrieval | PostgreSQL `websearch_to_tsquery` ranks exact event language through GIN FTS |
| Dense retrieval | 384-dimensional cosine search executes through pgvector HNSW |
| Hybrid fusion | RRF combines independently ranked lists without mixing raw score scales |
| Reranking | Exact phrase and token coverage contributions are returned and tested |
| Time filtering | A future lower bound returns no historical candidates |
| Bounding box | PostGIS limits both retrieval channels to intersecting source geometry |
| Radius filtering | Geography distance limits candidates and returns measured kilometres |
| Revision safety | Replaying the same batch is idempotent and the newest revision remains current |
| Citation safety | Missing, credential-bearing, local, private-IP, and unsafe-scheme URLs fail closed |
| Browser boundary | Zod rejects malformed search payloads before they reach the UI |

The real-service test seeds a synthetic, source-shaped corpus into PostgreSQL and exercises FTS,
pgvector, PostGIS, expiry, temporal filtering, rank fusion, and idempotent indexing. Synthetic
records make CI reproducible; they are not claims about real events. Live public feeds are used in
the deployed demo, where event IDs and relevance labels naturally drift.

This milestone does not report a misleading aggregate relevance score over the live feed because
there is no human-labeled live judgment set yet. The next retrieval iteration should add versioned
human relevance judgments and report Recall@k, MRR, nDCG, citation traceability, latency, and
filter-selectivity slices before changing the embedding model or reranker.
