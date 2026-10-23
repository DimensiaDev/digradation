"""Tests for the strict-provider and strict-sources switches.

A demo that has to stand behind every citation cannot afford a foreign
endpoint or an unconfirmed article. The two switches are what closes both,
and the tests pin their effect: with the flag off, everything behaves as
before; with the flag on, a host that is not on the list is refused.
"""

from __future__ import annotations

import pytest

from app.agentic import websearch
from app.providers.openai import ALLOWED_LLM_HOSTS_DEFAULT, _reject_foreign_host


@pytest.fixture(autouse=True)
def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STRICT_PROVIDER", raising=False)
    monkeypatch.delenv("ALLOWED_LLM_HOSTS", raising=False)
    monkeypatch.delenv("STRICT_SOURCES", raising=False)


def test_a_foreign_host_is_refused_in_strict_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRICT_PROVIDER", "true")
    with pytest.raises(ValueError, match="not in ALLOWED_LLM_HOSTS"):
        _reject_foreign_host("https://api.openai.com/v1")


def test_the_approved_hosts_pass_in_strict_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRICT_PROVIDER", "true")
    for host in ALLOWED_LLM_HOSTS_DEFAULT:
        _reject_foreign_host(f"https://{host}/v1")


def test_a_custom_allowlist_replaces_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRICT_PROVIDER", "true")
    monkeypatch.setenv("ALLOWED_LLM_HOSTS", "llm.example.com")
    _reject_foreign_host("https://llm.example.com/v1")
    with pytest.raises(ValueError, match="not in ALLOWED_LLM_HOSTS"):
        _reject_foreign_host("https://foundation-models.api.cloud.ru/v1")


def test_not_strict_accepts_anything() -> None:
    """Without the flag the endpoint is the operator's business."""
    _reject_foreign_host("https://api.openai.com/v1")


async def test_strict_sources_turns_the_web_search_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRICT_SOURCES", "true")

    async def fail(*_a, **_k):
        raise AssertionError("the network was reached")

    monkeypatch.setattr(websearch, "_fetch_json", fail)
    assert await websearch.search_web("ФГОС НОО") == []


async def test_the_web_is_off_when_the_flag_is_unset() -> None:
    """A deployment that has not chosen its sources cites the corpus only."""
    assert websearch.strict_sources() is True


async def test_an_explicit_false_turns_the_web_back_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The corpus fallback keeps working; only the web is refused."""
    monkeypatch.setenv("SBER_API_KEY", "a-real-key")
    monkeypatch.setenv("SBER_API_SCOPE", "PERS")
    monkeypatch.setenv("STRICT_SOURCES", "false")

    async def answer(*_a, **_k):
        return {"query": {"search": [{"title": "ФГОС", "snippet": "текст"}]}}

    monkeypatch.setattr(websearch, "_fetch_json", answer)
    found = await websearch.search_web("ФГОС НОО")
    assert [document.title for document in found] == ["ФГОС"]
