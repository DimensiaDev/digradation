"""Backend-side RAG wiring: catalog, ingestion, and retrieval settings.

The reusable embedding, indexing, and pgvector code lives in ``edu_rag``. This
package holds only what is specific to digradation: where the curated source
catalog lives, how parser output becomes chunks, and how the agent tool reaches
the index.
"""
