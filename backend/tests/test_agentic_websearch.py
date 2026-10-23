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
    # The suite runs on the mock provider, where the search is off; these
    # tests are about the search itself, so it is switched on by the
    # environment rather than by patching the switch. Strict sources is on by
    # default, and these tests exercise the web path deliberately.
    monkeypatch.setenv("SBER_API_KEY", "a-real-key")
    monkeypatch.setenv("SBER_API_SCOPE", "PERS")
    monkeypatch.setenv(websearch.STRICT_SOURCES_ENV, "false")


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


async def test_the_search_is_off_on_the_mock_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """The switch itself: the key or the scope says the stack runs offline."""
    monkeypatch.setenv("SBER_API_KEY", "mock")
    monkeypatch.delenv("SBER_API_SCOPE", raising=False)
    assert websearch.offline() is True

    monkeypatch.setenv("SBER_API_KEY", "a-real-key")
    monkeypatch.setenv("SBER_API_SCOPE", "mock")
    assert websearch.offline() is True

    monkeypatch.setenv("SBER_API_SCOPE", "PERS")
    assert websearch.offline() is False


async def test_the_search_asks_with_a_user_agent_and_reads_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The request itself: the encyclopedia refuses a request without an agent."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"query": {"search": []}})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    assert await websearch.search_web("ФГОС НОО") == []
    assert len(seen) == 1
    assert seen[0].headers["User-Agent"] == websearch.USER_AGENT
    assert seen[0].url.params["srsearch"] == "ФГОС НОО"


async def test_a_body_that_is_not_json_is_a_search_that_found_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken answer must not become an error of the tool."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    assert await websearch.search_web("ФГОС НОО") == []


async def test_a_payload_of_another_shape_is_a_search_that_found_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An answer the service did not promise must not become an error of the tool."""
    monkeypatch.setattr(websearch, "_fetch_json", answer({"query": "not a list"}))

    assert await websearch.search_web("ФГОС НОО") == []


async def test_the_mock_provider_turns_the_search_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The end-to-end suite runs on the mock provider and must not need the internet."""
    monkeypatch.setenv("SBER_API_KEY", "mock")

    async def fail(*_args, **_kwargs):
        raise AssertionError("the network was reached")

    monkeypatch.setattr(websearch, "_fetch_json", fail)

    assert await websearch.search_web("ФГОС НОО") == []


def _transport(handler) -> httpx.MockTransport:
    """A transport that answers every request the way the handler says."""
    return httpx.MockTransport(handler)


async def test_the_encyclopedia_is_asked_with_an_agent_and_a_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The encyclopedia refuses a request without a user agent, and the query has to be there."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"query": {"search": []}})

    original = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = _transport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(websearch, "offline", lambda: False)

    assert await websearch.search_web("ФГОС НОО") == []
    assert seen[0].headers["User-Agent"] == websearch.USER_AGENT
    assert seen[0].url.params["srsearch"] == "ФГОС НОО"


async def test_a_search_service_of_our_own_is_asked_over_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deployment's own service answers the same shape."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"results": [{"title": "Приказ № 286", "url": "https://docs.test/286", "snippet": "Текст"}]},
        )

    original = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = _transport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(websearch, "offline", lambda: False)
    monkeypatch.setenv(websearch.WEB_SEARCH_ENV, "https://search.test/api")

    found = await websearch.search_web("ФГОС НОО")

    assert [document.url for document in found] == ["https://docs.test/286"]
    assert seen[0].method == "POST"


async def test_a_service_that_answers_nothing_useful_is_a_search_that_found_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A payload of another shape is not an answer."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": "not a list"})

    original = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = _transport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(websearch, "offline", lambda: False)
    monkeypatch.setenv(websearch.WEB_SEARCH_ENV, "https://search.test/api")

    assert await websearch.search_web("ФГОС НОО") == []


async def test_a_hit_the_encyclopedia_cannot_name_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hit without a title is not a document."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"query": {"search": ["not a hit", {"title": ""}]}})

    original = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = _transport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(websearch, "offline", lambda: False)

    assert await websearch.search_web("ФГОС НОО") == []


async def test_a_service_that_fails_is_a_search_that_found_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A service that is down does not break the stage."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    original = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = _transport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(websearch, "offline", lambda: False)

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
