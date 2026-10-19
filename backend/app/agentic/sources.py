"""The source catalog the agent may cite, pending the RAG index.

The methodist scenario needs retrieved fragments with stable identifiers to cite.
RAG (issue #11) owns both the material corpus and the search over it, so this
module keeps only the seam the tool handlers call: the fragment type, the lookup
that canonicalises a cited id and the search itself. Until the index lands the
catalog is empty — a search reports nothing found and every citation is refused
as unknown, so a draft stored now cites no documents.
"""

from collections.abc import Collection

from pydantic import BaseModel

from app.agentic.models import Source, SourceKind


class SourceFragment(BaseModel):
    """One retrieved fragment with the document it belongs to."""

    fragment_id: str
    source: Source
    section: str
    text: str


def _catalog() -> list[Source]:
    """The sources the RAG index (#11) will fill; nothing is indexed yet."""
    return []


def _fragments() -> list[SourceFragment]:
    """The indexed fragments the RAG search (#11) will fill; nothing is indexed yet."""
    return []


def get_source(source_id: str) -> Source | None:
    """Return the canonical source record with this id, if the catalog has one."""
    return next((source for source in _catalog() if source.id == source_id), None)


async def search(
    query: str,
    *,
    kinds: Collection[SourceKind] | None = None,
    limit: int = 5,
) -> list[SourceFragment]:
    """Return up to ``limit`` fragments most relevant to the query.

    The catalog is empty until the RAG index replaces it, so the honest answer for
    any query is an empty list.
    """
    if not query.strip():
        return []
    return [fragment for fragment in _fragments() if kinds is None or fragment.source.kind in kinds][:limit]
