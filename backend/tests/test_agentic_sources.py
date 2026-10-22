"""Tests for the source catalogue in ``app.agentic.sources``.

The catalogue holds the documents of the corpus: the federal educational
standards, the professional standards and the programmes of the organisation.
It answers two questions the agent asks — does this document exist, and what
does it say — and the tests pin both, including the honest answer for a query
the corpus does not cover.
"""

from __future__ import annotations

import pytest

from app.agentic.models import SourceKind
from app.agentic.sources import CatalogEntry, clear_cache, entries, get_source, search


def test_the_catalog_holds_the_documents_of_the_corpus() -> None:
    """Every corpus directory contributes its documents."""
    found = {entry.id for entry in entries()}

    assert "fgos-soo-2026" in found
    assert "profstd-06.017" in found
    assert "prog-ba-internship-248h" in found


def test_a_catalog_entry_carries_what_the_programme_cites() -> None:
    """The document is cited with its order, its origin and how far it may be trusted."""
    record = get_source("profstd-06.017")

    assert record is not None
    assert record.kind is SourceKind.PROFESSIONAL_STANDARD
    assert "Профессиональный стандарт" in record.title
    assert record.identifier is not None
    assert "423н" in record.identifier
    assert record.trust is not None
    assert record.origin is not None
    assert record.confirmed is True


def test_a_document_the_corpus_does_not_hold_is_not_in_the_catalog() -> None:
    assert get_source("src-01") is None
    assert get_source("273-ФЗ") is None


async def test_a_search_finds_the_document_that_answers_the_query() -> None:
    """The professional standard named in the query comes first."""
    fragments = await search("профессиональный стандарт программист")

    assert fragments
    assert fragments[0].source.id == "profstd-06.001"
    assert fragments[0].source.confirmed is True


async def test_a_search_can_be_narrowed_to_a_kind() -> None:
    fragments = await search("образовательная программа", kinds=[SourceKind.REFERENCE])

    assert fragments
    assert all(fragment.source.kind is SourceKind.REFERENCE for fragment in fragments)


async def test_a_query_the_corpus_does_not_cover_finds_nothing() -> None:
    """The honest answer is an empty list, so the agent leaves the citation out."""
    assert await search("квантовая хромодинамика") == []


async def test_a_blank_query_never_reaches_the_catalog() -> None:
    assert await search("   ") == []


async def test_the_fragment_holds_the_lines_that_answer_the_query() -> None:
    fragments = await search("ФГОС среднего общего образования")

    assert fragments
    assert fragments[0].source.id == "fgos-soo-2026"
    assert "среднего общего" in fragments[0].source.title.lower()
    assert fragments[0].text


def test_the_catalog_reads_the_description_of_a_document() -> None:
    """The description is parsed into the fields the programme cites."""
    from app.agentic.sources import _parse

    parsed = _parse(
        "ID: doc-1\n"
        "Название: Название документа\n"
        "Тип: Профессиональный стандарт\n"
        "Версия/Дата: Приказ от 01.01.2020 № 1\n"
        "Происхождение: Реестр\n"
        "Статус доверия: Утверждённый\n"
        "\n"
        "Краткая выжимка:\n"
        "\n"
        "Цель: Проверить что поле разобрано\n",
        SourceKind.PROFESSIONAL_STANDARD,
    )

    assert len(parsed) == 1
    entry: CatalogEntry = parsed[0]
    assert entry.id == "doc-1"
    assert entry.title == "Название документа"
    assert entry.identifier == "Приказ от 01.01.2020 № 1"
    assert entry.origin == "Реестр"
    assert entry.trust == "Утверждённый"
    assert entry.summary == "Цель: Проверить что поле разобрано"


def test_a_description_without_an_id_is_no_entry() -> None:
    """A description that names no document contributes nothing."""
    from app.agentic.sources import _parse

    assert _parse("Просто текст без документа\n", SourceKind.LITERATURE) == []


@pytest.fixture(autouse=True)
def _clear_the_catalog():
    """The catalogue is read once; a test that changes the corpus clears it."""
    clear_cache()
    yield
    clear_cache()
