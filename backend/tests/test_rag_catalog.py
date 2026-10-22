"""Tests for the curated source catalog."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agentic.models import SourceKind
from app.rag.catalog import (
    CatalogEntry,
    canonical_source_id,
    clear_catalog_cache,
    find_catalog_entry,
    get_catalog_entry,
    load_catalog,
)


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    clear_catalog_cache()
    yield
    clear_catalog_cache()


def test_catalog_is_complete_and_unique() -> None:
    entries = load_catalog()
    assert len(entries) == 18
    assert len({entry.id for entry in entries}) == len(entries)
    assert len({entry.path for entry in entries}) == len(entries)
    assert entries[0].id == "src-01"
    assert entries[0].kind is SourceKind.NORMATIVE_ACT


def test_catalog_entry_builds_the_schema_source() -> None:
    entry = get_catalog_entry("src-1")
    assert entry is not None
    source = entry.to_source()
    assert source.id == "src-01"
    assert source.identifier == "Приказ Минпросвещения России от 31.05.2021 № 286"


def test_source_ids_are_canonicalised() -> None:
    assert canonical_source_id("src-1") == "src-01"
    assert canonical_source_id(" src-07 ") == "src-07"
    assert canonical_source_id("not-a-source") == "not-a-source"


def test_path_matching_accepts_a_parser_subtree() -> None:
    entries = load_catalog()
    entry = find_catalog_entry("origs/fgos-noo-2021.pdf", entries)
    assert entry is not None
    assert entry.id == "src-01"


def test_path_matching_refuses_an_ambiguous_suffix() -> None:
    entries = (
        CatalogEntry(
            id="src-01",
            path="a/origs/same.pdf",
            kind=SourceKind.NORMATIVE_ACT,
            title="A",
        ),
        CatalogEntry(
            id="src-02",
            path="b/origs/same.pdf",
            kind=SourceKind.NORMATIVE_ACT,
            title="B",
        ),
    )
    with pytest.raises(ValueError, match="more than one catalog entry"):
        find_catalog_entry("origs/same.pdf", entries)


def test_a_catalog_path_cannot_escape_the_corpus() -> None:
    with pytest.raises(ValidationError, match="must not contain"):
        CatalogEntry(
            id="src-99",
            path="../secret.pdf",
            kind=SourceKind.REFERENCE,
            title="Secret",
        )


def test_catalog_ids_must_be_zero_padded() -> None:
    with pytest.raises(ValidationError, match="zero-padded"):
        CatalogEntry(
            id="src-1",
            path="a.pdf",
            kind=SourceKind.REFERENCE,
            title="A",
        )


def test_every_corpus_document_has_a_catalog_entry() -> None:
    """A corpus block the catalog does not list cannot be cited as confirmed."""
    from app.agentic.sources import clear_cache as clear_source_cache
    from app.agentic.sources import corpus_entries

    clear_source_cache()
    try:
        catalog_ids = {entry.id for entry in load_catalog()}
        for entry in corpus_entries():
            assert entry.id in catalog_ids, f"corpus document {entry.id!r} has no catalog entry"
    finally:
        clear_source_cache()
