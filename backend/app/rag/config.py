"""Configuration constants shared by the RAG wiring."""

import os
from pathlib import Path

#: The hub embedding model the project indexes with. The database column, the
#: real provider, and the mock provider all use this one value. A different
#: dimension would require a migration, not just an environment change.
EMBEDDING_DIMENSION = 2048

DEFAULT_TOP_K = 5
DEFAULT_OVERSAMPLE = 4
DEFAULT_MIN_SCORE = 0.15
DEFAULT_MAX_PER_SOURCE = 3

#: The curated catalog ships with the backend image. Resolving it from this file
#: rather than from the process CWD keeps the tool working from the repository
#: root and from ``/app`` in the container alike.
CATALOG_PATH = Path(__file__).resolve().parents[2] / "res" / "rag-sources.json"


def is_rag_enabled() -> bool:
    """Whether programme stages may search the index."""
    return os.getenv("RAG_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
