"""The online search: documents from the web, next to the documents of the corpus.

The corpus in ``edu_materials/`` holds the documents the organisation already
trusts. The web holds the rest: the current versions of the acts, the orders
that were not in the corpus when it was put together. The criteria ask the
agent to look for the actual sources, and the marking rules ask it to say
which of them nobody confirmed — so the search has to reach outside the
corpus, and what it finds has to come back in the same shape as what the
corpus holds.

Two sources are behind this module, and both answer the same question — *what
documents does the web hold about this* — in the same shape:

- **The default is the Wikipedia search**, which needs no credentials and
  covers the federal educational standards and the professional standards by
  their names and their numbers. It is a search over an encyclopedia, not over
  the whole web, and the documents it finds are marked as unconfirmed unless
  the corpus holds them too: the encyclopedia is not the publisher of an act.
- **A deployment with a search service of its own sets ``WEB_SEARCH_URL``**,
  and the service answers the same request and the same response, so the
  backend does not change with it.

The response of a search service is a JSON object with a ``results`` list, and
every result has a ``title``, a ``url`` and a ``snippet``.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

import httpx
import structlog.stdlib
from pydantic import BaseModel

from app.agentic.models import Source, SourceKind

log = structlog.stdlib.get_logger(__name__)

#: Where a deployment's own search service lives. Without it the encyclopedia
#: search answers.
WEB_SEARCH_ENV = "WEB_SEARCH_URL"

#: The encyclopedia search, which needs no credentials.
WIKIPEDIA_SEARCH_URL = "https://ru.wikipedia.org/w/api.php"

#: The language the encyclopedia search answers in.
WIKIPEDIA_LANGUAGE = "ru"

#: The encyclopedia refuses requests without one, and so does every other
#: service that is asked politely.
USER_AGENT = "Mozilla/5.0 (compatible; DigradationBot/1.0; +https://github.com/DimensiaDev/digradation)"

#: How long a search may take before it is given up on.
SEARCH_TIMEOUT_S = 15.0

#: The most results a search returns.
MAX_RESULTS = 10

_TAGS = re.compile(r"<[^>]+>")


class WebDocument(BaseModel):
    """One document a web search found."""

    title: str
    url: str
    snippet: str

    def as_source(self, kind: SourceKind = SourceKind.INTERNET_RESOURCE) -> Source:
        """The document as a source the programme may cite.

        The kind is the one the document is *cited as*: an encyclopedia article
        is an internet resource, whatever it is about. The source is not
        confirmed: the corpus is what confirms a document, and it does not hold
        this one.
        """
        digest = hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:8]
        return Source(
            id=f"web-{digest}",
            kind=kind,
            title=self.title,
            url=self.url,
            confirmed=False,
        )


async def search_web(query: str, *, limit: int = 5) -> list[WebDocument]:
    """Return the documents the web holds about the query.

    A search that fails is a search that found nothing: the caller tells the
    model so, and the model leaves the citation out rather than inventing one.
    """
    text = query.strip()
    if not text:
        return []
    if offline() or strict_sources():
        # The stack runs on the mock provider, and the end-to-end suite among
        # it: a search that reached the network would make it depend on the
        # internet. A strict deployment refuses the web outright, because
        # nothing outside the corpus has been verified.
        return []
    endpoint = os.getenv(WEB_SEARCH_ENV)
    try:
        if endpoint:
            found = await _search_service(endpoint, text, limit)
        else:
            found = await _search_wikipedia(text, limit)
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as error:
        # A search that fails — a broken service, an answer that is not JSON,
        # a payload of another shape — is a search that found nothing.
        log.warning("web_search_failed", error=str(error))
        return []
    return found[:limit]


def offline() -> bool:
    """Whether the deployment has no network to search.

    The mock provider means the whole stack runs without one.
    """
    key = os.getenv("SBER_API_KEY", "").strip().lower()
    scope = os.getenv("SBER_API_SCOPE", "").strip().lower()
    return key == "mock" or scope == "mock"


def strict_sources() -> bool:
    """Whether the deployment accepts only the corpus as sources.

    A strict deployment refuses the web search outright: the corpus is what the
    methodist trusts, and a document from outside it is unconfirmed at best.
    The switch is what closes the Wikipedia fallback in a demo that has to
    stand behind every citation.
    """
    return os.getenv("STRICT_SOURCES", "").strip().lower() in {"1", "true", "yes", "on"}


async def _search_service(endpoint: str, query: str, limit: int) -> list[WebDocument]:
    """Ask the deployment's own search service."""
    payload = await _fetch_json(endpoint, payload={"query": query, "limit": limit})
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return []
    return [
        WebDocument(
            title=str(item.get("title") or "").strip(),
            url=str(item.get("url") or "").strip(),
            snippet=_plain(str(item.get("snippet") or "")),
        )
        for item in results
        if isinstance(item, dict) and item.get("title") and item.get("url")
    ]


async def _search_wikipedia(query: str, limit: int) -> list[WebDocument]:
    """Ask the encyclopedia, which answers without credentials."""
    payload = await _fetch_json(
        WIKIPEDIA_SEARCH_URL,
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": min(limit, MAX_RESULTS),
            "format": "json",
            "utf8": 1,
        },
        headers={"Accept-Language": WIKIPEDIA_LANGUAGE, "User-Agent": USER_AGENT},
    )
    query = payload.get("query") if isinstance(payload, dict) else None
    hits = query.get("search", []) if isinstance(query, dict) else []
    if not isinstance(hits, list):
        return []
    found: list[WebDocument] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        title = str(hit.get("title") or "").strip()
        if not title:
            continue
        found.append(
            WebDocument(
                title=title,
                url=f"https://{WIKIPEDIA_LANGUAGE}.wikipedia.org/wiki/{title.replace(' ', '_')}",
                snippet=_plain(str(hit.get("snippet") or "")),
            )
        )
    return found


async def _fetch_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """The JSON of a URL.

    One place where the module talks to the network, so the tests can answer
    for it without touching the network.
    """
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_S) as client:
        if payload is None:
            response = await client.get(url, params=params, headers=headers)
        else:
            response = await client.post(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()


def _plain(text: str) -> str:
    """The text of a snippet, without the encyclopedia's markup."""
    return _TAGS.sub("", text).strip()
