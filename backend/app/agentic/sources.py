"""The catalogue of documents a programme may cite.

The corpus in ``edu_materials/`` holds the documents an educational programme
is built upon: the federal educational standards, the professional standards
and the programmes of the organisation itself. Every corpus directory carries a
description of its documents — id, name, type, the order that approved it,
where it came from and how far it may be trusted — and this module turns those
descriptions into the catalogue the agent searches.

The catalogue answers two questions. *Does this document exist* is what makes a
citation confirmable: only a document the catalogue holds may be marked as
confirmed, and anything else the agent names is kept and marked as
unconfirmed. *What does the document say* is what the fragment holds, so the
model builds on the document instead of guessing what it says.

The search is lexical. The semantic index over the corpus arrives with the
vector store; until then a keyword match over the descriptions answers the
questions the agent asks, and it answers them offline.
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from app.agentic.models import Source, SourceKind

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


class CatalogEntry(BaseModel):
    """One document of the corpus."""

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
def entries() -> tuple[CatalogEntry, ...]:
    """Every document of the corpus, read once."""
    found: list[CatalogEntry] = []
    for directory, kind in CORPUS_KINDS.items():
        path = corpus_path() / directory / CORPUS_DESCRIPTIONS[directory]
        if not path.is_file():
            continue
        found.extend(_parse(path.read_text(encoding="utf-8"), kind))
    return tuple(found)


@lru_cache(maxsize=1)
def _by_id() -> dict[str, CatalogEntry]:
    return {entry.id: entry for entry in entries()}


def clear_cache() -> None:
    """Forget the corpus, so a changed one is read again.

    The corpus does not change while the app runs, but a test that points the
    catalogue at another directory has to be able to make it read again.
    """
    entries.cache_clear()
    _by_id.cache_clear()


def get_source(source_id: str) -> Source | None:
    """Return the canonical source record with this id, if the catalog has one."""
    entry = _by_id().get(source_id)
    return entry.to_source() if entry is not None else None


async def search(
    query: str,
    *,
    kinds: Collection[SourceKind] | None = None,
    limit: int = 5,
) -> list[SourceFragment]:
    """Return up to ``limit`` documents that mention the query, best first.

    A document that mentions nothing of the query is not returned: the honest
    answer for a query the corpus does not cover is an empty list, so the agent
    leaves the citation out instead of inventing one.
    """
    words = _words(query)
    if not words:
        return []
    scored: list[tuple[int, CatalogEntry]] = []
    for entry in entries():
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


def _score(entry: CatalogEntry, words: list[str]) -> int:
    """How much of the query the document holds, the title counting double."""
    title = _words(entry.title)
    rest = _words(entry.text())
    score = 0
    for word in words:
        if word in _STOP_WORDS:
            continue
        if _matches(word, title):
            score += 2
        elif _matches(word, rest):
            score += 1
    return score


def _excerpt(entry: CatalogEntry, words: list[str]) -> str:
    """The lines of the document that mention the query."""
    lines = [line.strip() for line in entry.summary.splitlines() if line.strip()]
    hits = [line for line in lines if any(_matches(word, _words(line)) for word in words)]
    return "\n".join(hits[:MAX_EXCERPT_LINES]) or entry.summary[:MAX_EXCERPT_CHARS]


def _parse(text: str, kind: SourceKind) -> list[CatalogEntry]:
    """The entries of one corpus description.

    An entry starts at its ``ID:`` line and runs to the next one, so the
    heading a description puts above the id does not have to be understood.
    """
    found: list[CatalogEntry] = []
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
                found.append(_entry(fields, _without_heading(body), kind))
            fields, body = {"ID": value}, []
            continue
        fields[name] = value
    if fields:
        found.append(_entry(fields, body, kind))
    return found


def _without_heading(body: list[str]) -> list[str]:
    """The extract of an entry, without the heading of the next one.

    A description puts the heading of the next document on the line before its
    id, so that line is not part of this entry's extract. A description that
    starts an entry with its id has no such heading, and the blank line before
    it stays where it is.
    """
    if body and body[-1].strip():
        return body[:-1]
    return body


def _entry(fields: dict[str, str], body: list[str], kind: SourceKind) -> CatalogEntry:
    """One entry of a description."""
    summary = "\n".join(
        line.strip() for line in body if line.strip() and not line.strip().startswith(_EXTRACT_HEADINGS)
    )
    return CatalogEntry(
        id=fields.get("ID", ""),
        kind=kind,
        title=fields.get("Название", ""),
        identifier=fields.get("Версия/Дата") or None,
        origin=fields.get("Происхождение", ""),
        trust=fields.get("Статус доверия", ""),
        summary=summary,
    )
