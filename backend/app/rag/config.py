"""Configuration constants shared by the RAG wiring."""

import os
from pathlib import Path

#: The hub embedding model the project indexes with. The database column, the
#: real provider, and the mock provider all use this one value. A different
#: dimension would require a migration, not just an environment change.
EMBEDDING_DIMENSION = 2048

DEFAULT_TOP_K = 5
DEFAULT_OVERSAMPLE = 4

#: The floor a fragment has to clear to count as an answer.
#:
#: The number is measured, not guessed. On this corpus, with this model, an
#: unrelated query scores around 0.18 and a related one around 0.44, and the two
#: distributions do not overlap. A floor of 0.30 sits between them, so a
#: fragment that clears it is about the subject and one that does not is not.
#: A lower floor admits the junk the measurement excludes.
DEFAULT_MIN_SCORE = 0.30
DEFAULT_MAX_PER_SOURCE = 3

#: The curated catalog ships with the backend image. Resolving it from this file
#: rather than from the process CWD keeps the tool working from the repository
#: root and from ``/app`` in the container alike.
CATALOG_PATH = Path(__file__).resolve().parents[2] / "res" / "rag-sources.json"


def is_rag_enabled() -> bool:
    """Whether programme stages may search the index."""
    return os.getenv("RAG_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
