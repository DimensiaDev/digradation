"""Tests for the source-catalog seam in ``app.agentic.sources``.

The module is a placeholder until the RAG index (#11) lands: the catalog is
empty, so a lookup finds nothing and a search reports nothing found. These tests
pin that honest emptiness, because a stage that could cite an invented document
would dead-end on the next validation.
"""

from __future__ import annotations

from app.agentic.sources import get_source, search


def test_the_catalog_has_no_sources_until_the_index_lands() -> None:
    assert get_source("src-01") is None


async def test_search_finds_nothing_in_an_empty_catalog() -> None:
    assert await search("профстандарт аналитика") == []


async def test_a_blank_query_never_reaches_the_catalog() -> None:
    assert await search("   ") == []
