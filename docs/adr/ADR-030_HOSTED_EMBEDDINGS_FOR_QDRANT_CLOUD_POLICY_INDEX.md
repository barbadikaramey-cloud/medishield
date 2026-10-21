# Architecture Decision Record
## ADR-030: Hosted Embeddings for the Qdrant Cloud Policy Index

**Status:** Accepted  
**Date:** 2026-10-21  
**Context:** Policy RAG and Administration Specification § 1, Database and Storage Contracts, ADR-016, ADR-026  
**Deciders:** MediShield Engineering

---

## 1. Context and Problem Statement

The authoritative policy index (`EmbeddedQdrantPolicyIndex`) embeds chunks with a deterministic 128-dimensional token-hash vector and writes them to a local embedded Qdrant store. The project now uses a Qdrant Cloud cluster whose `Medishield` collection expects 512-dimensional cosine vectors under the named vector `Medishield`. The hash embedding cannot populate that collection meaningfully, and the project has no embedding model dependency.

## 2. Decision

1. Policy chunks and queries are embedded through the Gemini embedding API already available through the `google-genai` dependency, using model `gemini-embedding-001` with `output_dimensionality=512`. No new package is added.
2. The model and dimensionality are configuration (`EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`), not constants in code.
3. The collection name and optional named-vector name are configuration (`QDRANT_COLLECTION_NAME`, `QDRANT_VECTOR_NAME`). Cloud deployments set them to `Medishield` and `Medishield`. The local embedded store keeps the existing hash embedding and unnamed vector, so offline evaluation remains deterministic and needs no API key.
4. Embedding failures raise an error at index or search time. The index never silently falls back to the hash embedding, because mixing embedding spaces in one collection would corrupt retrieval.
5. Embeddings are used only for retrieval ranking. They do not determine coverage, exclusion, or any business decision (Rule 15).

## 3. Consequences

### Positive
- Cloud retrieval uses a semantic embedding at the dimensionality the cluster collection expects.
- No new dependency; the model is swappable through configuration.

### Trade-offs and Follow-up
- Policy publishing and search now depend on the Gemini API and its quota. Offline and test runs use the local hash path.
- Vectors already in the local store are not comparable with cloud vectors. Re-index is required when switching embedding providers.
- Named-vector and unnamed-vector collections cannot share a single index instance; the index selects one at construction.
