"""The source catalog and the RAG search the agent tools call.

The curated catalog supplies stable ``src-NN`` citation ids; the pgvector index
supplies the fragments behind them. This module is the seam between the two and
is the only place the rest of ``app.agentic`` touches either.

RAG is feature-flagged: with ``RAG_ENABLED`` unset the search reports nothing and
the pipeline behaves exactly as it did before the index existed. Enabling it
without a reachable embedding provider raises :class:`RagUnavailable`, which the
tool turns into a model-visible note instead of failing the stage.
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection
from functools import lru_cache
from pathlib import Path, PurePath

import structlog.stdlib
from edu_rag import PgVectorStore, RagRetriever
from pydantic import BaseModel, Field

from app.agentic.models import Source, SourceKind
from app.database import get_session_maker
from app.models.rag import RagChunk
from app.providers.containers import get_embedding_provider
from app.rag.catalog import canonical_source_id, catalog_by_id, get_catalog_entry
from app.rag.config import (
    DEFAULT_MAX_PER_SOURCE,
    DEFAULT_MIN_SCORE,
    DEFAULT_OVERSAMPLE,
    DEFAULT_TOP_K,
    is_rag_enabled,
)

log = structlog.stdlib.get_logger(__name__)


class RagUnavailable(RuntimeError):
    """The index or its embedding provider cannot answer this query."""


#: Where the corpus lives. The image keeps it at /edu_materials; a checkout
#: has it next to the backend package.
CORPUS_ENV = "EDU_MATERIALS_PATH"

#: The kind of source each corpus directory holds.
CORPUS_KINDS: dict[str, SourceKind] = {
    "fgos": SourceKind.NORMATIVE_ACT,
    "prof_stand": SourceKind.PROFESSIONAL_STANDARD,
    "edu_programs": SourceKind.REFERENCE,
}

#: The description file of each corpus directory.
CORPUS_DESCRIPTIONS: dict[str, str] = {
    "fgos": "fgos.txt",
    "prof_stand": "prof_standarts.txt",
    "edu_programs": "edu_programs.txt",
}

#: The fields a description carries, and the heading above the extract.
_FIELDS = ("ID", "Название", "Тип", "Версия/Дата", "Происхождение", "Статус доверия")
_EXTRACT_HEADINGS = ("Краткая выжимка", "Сокращенная выжимка")

#: The words that say nothing about what a document is about.
_STOP_WORDS = frozenset(
    {
        "а",
        "без",
        "в",
        "во",
        "для",
        "до",
        "из",
        "и",
        "их",
        "к",
        "на",
        "не",
        "о",
        "об",
        "от",
        "по",
        "при",
        "с",
        "со",
        "что",
        "это",
    }
)

_FIELD = re.compile(r"^(?P<name>[^:]{1,40}):\s*(?P<value>.*)$")
_WORD = re.compile(r"[0-9a-zA-Zа-яА-ЯёЁ]+")

#: How many lines of a document a fragment holds.
MAX_EXCERPT_LINES = 6

#: How much of a document a fragment holds when no line matches the query.
MAX_EXCERPT_CHARS = 1200


class SourceFragment(BaseModel):
    """One retrieved fragment with the document it belongs to."""

    fragment_id: str
    source: Source
    section: str
    text: str
    page: int | None = None
    trust: float = Field(default=0.5, ge=0.0, le=1.0)
    score: float = 0.0


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None or not raw.strip() else int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None or not raw.strip() else float(raw)


@lru_cache
def get_retriever() -> RagRetriever | None:
    """Build the retriever once, or return ``None`` while RAG is disabled.

    Construction is lazy because importing a worker must not require an
    embedding provider, and because the GigaChat-native scope has no embedding
    endpoint at all. A provider error is surfaced as ``RagUnavailable`` so the
    stage can continue without citations rather than dying.
    """
    if not is_rag_enabled():
        return None
    try:
        provider = get_embedding_provider()
        store = PgVectorStore(get_session_maker(), RagChunk.__table__)
        return RagRetriever(
            provider,
            store,
            oversample=_env_int("RAG_OVERSAMPLE", DEFAULT_OVERSAMPLE),
            min_score=_env_float("RAG_MIN_SCORE", DEFAULT_MIN_SCORE),
            max_per_source=_env_int("RAG_MAX_PER_SOURCE", DEFAULT_MAX_PER_SOURCE),
        )
    except Exception as error:  # any construction failure means "no search"
        raise RagUnavailable(str(error)) from error


def clear_retriever_cache() -> None:
    """Drop the cached retriever; tests toggle ``RAG_ENABLED`` between cases."""
    clear = getattr(get_retriever, "cache_clear", None)
    if clear is not None:
        clear()


#: Where the corpus lives. The image keeps it at /edu_materials; a checkout
#: has it next to the backend package.
CORPUS_ENV = "EDU_MATERIALS_PATH"

#: The kind of source each corpus directory holds.
CORPUS_KINDS: dict[str, SourceKind] = {
    "fgos": SourceKind.NORMATIVE_ACT,
    "prof_stand": SourceKind.PROFESSIONAL_STANDARD,
    "edu_programs": SourceKind.REFERENCE,
}

#: The description file of each corpus directory.
CORPUS_DESCRIPTIONS: dict[str, str] = {
    "fgos": "fgos.txt",
    "prof_stand": "prof_standarts.txt",
    "edu_programs": "edu_programs.txt",
}

#: The fields a description carries, and the heading above the extract.
_FIELDS = ("ID", "Название", "Тип", "Версия/Дата", "Происхождение", "Статус доверия")
_EXTRACT_HEADINGS = ("Краткая выжимка", "Сокращенная выжимка")

#: The words that say nothing about what a document is about.
_STOP_WORDS = frozenset(
    {
        "а",
        "без",
        "в",
        "во",
        "для",
        "до",
        "из",
        "и",
        "их",
        "к",
        "на",
        "не",
        "о",
        "об",
        "от",
        "по",
        "при",
        "с",
        "со",
        "что",
        "это",
    }
)

_FIELD = re.compile(r"^(?P<name>[^:]{1,40}):\s*(?P<value>.*)$")
_WORD = re.compile(r"[0-9a-zA-Zа-яА-ЯёЁ]+")

#: How many lines of a document a fragment holds.
MAX_EXCERPT_LINES = 6

#: How much of a document a fragment holds when no line matches the query.
MAX_EXCERPT_CHARS = 1200


class CorpusEntry(BaseModel):
    """One document of the corpus, as its description describes it.

    ``id`` is the document's file name, which is how the curated catalogue
    refers to it too; the citation id is the curated one.
    """

    id: str
    kind: SourceKind
    title: str
    identifier: str | None = None
    origin: str = ""
    trust: str = ""
    summary: str = ""

    def to_source(self) -> Source:
        """The catalogue record as the programme cites it."""
        return Source(
            id=self.id,
            kind=self.kind,
            title=self.title,
            identifier=self.identifier,
            origin=self.origin or None,
            trust=self.trust or None,
            confirmed=True,
        )

    def text(self) -> str:
        """Everything a search may match the document by."""
        return " ".join(part for part in (self.title, self.identifier, self.origin, self.trust, self.summary) if part)


def corpus_path() -> Path:
    """Where the corpus lives."""
    configured = os.getenv(CORPUS_ENV)
    if configured:
        return Path(configured)
    # backend/app/agentic/sources.py -> the checkout's root.
    return Path(__file__).resolve().parents[3] / "edu_materials"


@lru_cache(maxsize=1)
def corpus_entries() -> tuple[CorpusEntry, ...]:
    """Every document of the corpus, read once.

    The descriptions need neither an index nor an embedding provider, so they
    answer the search when the index is not there — a fresh checkout, the test
    suite and the end-to-end stack.

    The corpus describes the same documents as the curated catalogue, under
    their file names. One document has one id, so a corpus entry is filed under
    the curated one: a programme cites ``src-04`` and not ``profstd-06.001``
    for the same standard.
    """
    curated = _curated_by_stem()
    found: list[CorpusEntry] = []
    for directory, kind in CORPUS_KINDS.items():
        path = corpus_path() / directory / CORPUS_DESCRIPTIONS[directory]
        if not path.is_file():
            continue
        for entry in _parse(path.read_text(encoding="utf-8"), kind):
            source_id = curated.get(entry.id)
            found.append(entry.model_copy(update={"id": source_id or entry.id}))
    return tuple(found)


@lru_cache(maxsize=1)
def _corpus_by_id() -> dict[str, CorpusEntry]:
    return {entry.id: entry for entry in corpus_entries()}


@lru_cache(maxsize=1)
def _curated_by_stem() -> dict[str, str]:
    """The curated id of every document, keyed by the file it was parsed from."""
    try:
        entries = catalog_by_id()
    except Exception as error:  # a broken catalog must not break the corpus
        log.error("source_catalog_failed", error=str(error))
        return {}
    return {PurePath(entry.path).stem: entry.id for entry in entries.values()}


def clear_cache() -> None:
    """Forget the corpus, so a changed one is read again.

    The corpus does not change while the app runs, but a test that points the
    catalogue at another directory has to be able to make it read again.
    """
    corpus_entries.cache_clear()
    _corpus_by_id.cache_clear()


def get_source(source_id: str) -> Source | None:
    """Return the canonical source record with this id, if the catalog has one.

    The curated catalogue is the authority: it is what a citation is confirmed
    against. The corpus descriptions answer for the documents it does not list,
    so a deployment without the curated catalogue still knows its own corpus.
    """
    try:
        entry = get_catalog_entry(source_id)
    except Exception as error:  # a broken catalog is a missing source, not a crash
        log.error("source_catalog_failed", error=str(error))
        entry = None
    if entry is not None:
        record = entry.to_source()
        corpus = _corpus_by_id().get(entry.id)
        if corpus is not None:
            record = record.model_copy(
                update={
                    "origin": corpus.origin or record.origin,
                    "trust": corpus.trust or record.trust,
                    "identifier": corpus.identifier or record.identifier,
                }
            )
        return record
    corpus = _corpus_by_id().get(source_id)
    return corpus.to_source() if corpus is not None else None


async def search(
    query: str,
    *,
    kinds: Collection[SourceKind] | None = None,
    limit: int = DEFAULT_TOP_K,
) -> list[SourceFragment]:
    """Return up to ``limit`` fragments most relevant to the query.

    The semantic index is the better answer and is asked first, but it needs an
    index and an embedding provider. Without them the corpus descriptions
    answer instead, so a deployment that has not built the index still finds
    the documents it already has.

    ``RagUnavailable`` is deliberately allowed through: the tool handler turns
    it into a note, while a generic tool failure would abort the stage. An empty
    list means the search worked and found nothing.
    """
    if not query.strip() or limit <= 0:
        return []
    retriever = get_retriever()
    if retriever is None:
        return await search_corpus(query, kinds=kinds, limit=limit)

    try:
        hits = await retriever.retrieve(
            query,
            kinds={kind.value for kind in kinds} if kinds is not None else None,
            limit=min(limit, 10),
        )
    except RagUnavailable:
        raise
    except Exception as error:  # provider, database, and pgvector errors all mean "unavailable"
        log.warning("rag_search_failed", error=str(error))
        raise RagUnavailable(str(error)) from error

    try:
        entries = catalog_by_id()
    except Exception as error:  # a broken catalog must not abort the stage
        log.error("source_catalog_failed", error=str(error))
        raise RagUnavailable(str(error)) from error
    fragments: list[SourceFragment] = []
    for hit in hits:
        if not hit.source_id:
            log.warning("rag_hit_without_source", doc_id=hit.doc_id, ordinal=hit.ordinal)
            continue
        entry = entries.get(canonical_source_id(hit.source_id))
        if entry is None:
            log.warning("rag_hit_with_unknown_source", source_id=hit.source_id, doc_id=hit.doc_id)
            continue
        fragments.append(
            SourceFragment(
                fragment_id=f"{hit.doc_id}:{hit.kind}:{hit.ordinal}",
                source=entry.to_source(),
                section=hit.section_title or entry.title,
                text=hit.content,
                page=hit.page_number,
                trust=entry.trust,
                score=hit.score,
            )
        )
    return fragments


async def search_corpus(
    query: str,
    *,
    kinds: Collection[SourceKind] | None = None,
    limit: int = 5,
) -> list[SourceFragment]:
    """The documents that mention the query, found without an index.

    A document that mentions nothing of the query is not returned: the honest
    answer for a query the corpus does not cover is an empty list, so the agent
    leaves the citation out instead of inventing one.
    """
    words = _words(query)
    if not words:
        return []
    scored: list[tuple[int, CorpusEntry]] = []
    for entry in corpus_entries():
        if kinds is not None and entry.kind not in kinds:
            continue
        score = _score(entry, words)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    return [
        SourceFragment(
            fragment_id=entry.id,
            source=entry.to_source(),
            section=entry.title,
            text=_excerpt(entry, words),
        )
        for _score_value, entry in scored[:limit]
    ]


def _words(text: str) -> list[str]:
    """The words of a text, lowercased."""
    return [word.lower() for word in _WORD.findall(text)]


def _matches(word: str, words: list[str]) -> bool:
    """Whether a word of the query is in the document, allowing a shared stem."""
    if word in words:
        return True
    if len(word) < 4:
        return False
    return any(
        len(candidate) >= 4 and (candidate.startswith(word) or word.startswith(candidate)) for candidate in words
    )


def _score(entry: CorpusEntry, words: list[str]) -> int:
    """How much of the query the document holds.

    A word in the title is the document's own name and counts more than a word
    in its body, so a query naming a standard finds that standard first.
    """
    title = _words(entry.title)
    rest = _words(entry.text())
    score = 0
    for word in words:
        if word in _STOP_WORDS:
            continue
        if _matches(word, title):
            score += 3
        elif _matches(word, rest):
            score += 1
    return score


def _excerpt(entry: CorpusEntry, words: list[str]) -> str:
    """The lines of the document that mention the query."""
    lines = [line.strip() for line in entry.summary.splitlines() if line.strip()]
    hits = [line for line in lines if any(_matches(word, _words(line)) for word in words)]
    return "\n".join(hits[:MAX_EXCERPT_LINES]) or entry.summary[:MAX_EXCERPT_CHARS]


def _parse(text: str, kind: SourceKind) -> list[CorpusEntry]:
    """The entries of one corpus description.

    An entry starts at its ``ID:`` line and runs to the next one, so the
    heading a description puts above the id does not have to be understood.
    """
    found: list[CorpusEntry] = []
    fields: dict[str, str] = {}
    body: list[str] = []
    for line in text.splitlines():
        match = _FIELD.match(line.strip())
        if match is None or match.group("name") not in _FIELDS:
            if fields:
                body.append(line)
            continue
        name, value = match.group("name"), match.group("value").strip()
        if name == "ID":
            if fields:
                found.append(_entry(fields, body, kind))
            fields, body = {"ID": value}, []
            continue
        fields[name] = value
    if fields:
        found.append(_entry(fields, body, kind))
    return found


def _entry(fields: dict[str, str], body: list[str], kind: SourceKind) -> CorpusEntry:
    """One entry of a description."""
    summary = "\n".join(
        line.strip() for line in body if line.strip() and not line.strip().startswith(_EXTRACT_HEADINGS)
    )
    return CorpusEntry(
        id=fields.get("ID", ""),
        kind=kind,
        title=fields.get("Название", ""),
        identifier=fields.get("Версия/Дата") or None,
        origin=fields.get("Происхождение", ""),
        trust=fields.get("Статус доверия", ""),
        summary=summary,
    )
