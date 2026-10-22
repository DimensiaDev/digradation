"""Tests for the online search in ``app.agentic.websearch``.

The search reaches the network, so the tests answer for it: the encyclopedia
search and a deployment's own search service are both asked through one
function, and the tests put their answers there. What matters is the shape
the documents come back in, and what happens when the search fails.
"""

from __future__ import annotations

import httpx
import pytest

from app.agentic import websearch
from app.agentic.models import SourceKind


@pytest.fixture(autouse=True)
def _no_configured_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a service of its own, the encyclopedia answers."""
    monkeypatch.delenv(websearch.WEB_SEARCH_ENV, raising=False)
    # The suite runs on the mock provider, and the search is off there.
    monkeypatch.setattr(websearch, "offline", lambda: False)


def answer(payload: dict) -> None:
    """Answer every request with this payload."""

    async def fetch(*_args, **_kwargs):
        return payload

    return fetch


async def test_the_encyclopedia_search_answers_with_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hit becomes a document with a title, a link and a plain snippet."""
    monkeypatch.setattr(
        websearch,
        "_fetch_json",
        answer(
            {
                "query": {
                    "search": [
                        {
                            "title": "Федеральный государственный образовательный стандарт",
                            "snippet": 'Приказ <span class="searchmatch">Минпросвещения</span> № 286.',
                        }
                    ]
                }
            }
        ),
    )

    found = await websearch.search_web("ФГОС НОО")

    assert len(found) == 1
    assert found[0].title == "Федеральный государственный образовательный стандарт"
    assert found[0].url == "https://ru.wikipedia.org/wiki/Федеральный_государственный_образовательный_стандарт"
    assert "<span" not in found[0].snippet
    assert "Минпросвещения" in found[0].snippet


async def test_a_service_of_our_own_answers_when_one_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment points the search at a service and gets its documents."""
    monkeypatch.setenv(websearch.WEB_SEARCH_ENV, "https://search.test/api")
    monkeypatch.setattr(
        websearch,
        "_fetch_json",
        answer(
            {
                "results": [
                    {"title": "Приказ № 286", "url": "https://docs.test/286", "snippet": "Текст приказа"},
                    {"title": "Без ссылки", "snippet": "не годится"},
                ]
            }
        ),
    )

    found = await websearch.search_web("ФГОС НОО")

    assert [document.url for document in found] == ["https://docs.test/286"]


async def test_a_search_that_fails_finds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken search is a search that found nothing, not a crash."""

    async def fail(*_args, **_kwargs):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(websearch, "_fetch_json", fail)

    assert await websearch.search_web("ФГОС НОО") == []


async def test_a_blank_query_never_reaches_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(websearch, "_fetch_json", answer({"query": {"search": []}}))

    assert await websearch.search_web("   ") == []


async def test_the_mock_provider_turns_the_search_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The end-to-end suite runs on the mock provider and must not need the internet."""
    monkeypatch.setattr(websearch, "offline", lambda: True)

    async def fail(*_args, **_kwargs):
        raise AssertionError("the network was reached")

    monkeypatch.setattr(websearch, "_fetch_json", fail)

    assert await websearch.search_web("ФГОС НОО") == []


async def test_a_web_document_is_cited_as_an_internet_resource() -> None:
    """The kind is the one the document is cited as, and it is not confirmed."""
    document = websearch.WebDocument(title="ФГОС НОО", url="https://ru.wikipedia.org/wiki/ФГОС", snippet="Текст")

    source = document.as_source()

    assert source.kind is SourceKind.INTERNET_RESOURCE
    assert source.url == "https://ru.wikipedia.org/wiki/ФГОС"
    assert source.confirmed is False


async def test_the_id_of_a_web_document_is_the_same_every_time() -> None:
    """The id has to be stable: the programme cites it by id."""
    first = websearch.WebDocument(title="ФГОС НОО", url="https://ru.wikipedia.org/wiki/ФГОС", snippet="")
    second = websearch.WebDocument(title="ФГОС НОО", url="https://ru.wikipedia.org/wiki/ФГОС", snippet="Другое")

    assert first.as_source().id == second.as_source().id
    assert (
        first.as_source().id
        != websearch.WebDocument(title="ФГОС ООО", url="https://ru.wikipedia.org/wiki/Другое", snippet="")
        .as_source()
        .id
    )
