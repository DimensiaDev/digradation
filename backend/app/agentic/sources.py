"""Search over the source materials the agent may cite.

The methodist scenario needs retrieved fragments with stable identifiers to cite.
RAG (issue #11) provides the real search interface behind the same entry point;
until it lands, this module searches the human-written summaries under
``materials/`` with a deliberately simple lexical match. The summaries are
duplicated in the backend package because the Docker build context is
``backend/`` alone — the root ``edu_materials/`` dataset is not copied into the
image — and the two copies must be kept in step until the RAG index replaces
this module's body.
"""

import re
from collections.abc import Collection
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from pydantic import BaseModel

from app.agentic.models import Source, SourceKind

MATERIALS_DIR = Path(__file__).parent / "materials"

#: Which material file lists which kind of source. The order fixes the ``src-NN``
#: identifiers, so reordering this mapping renumbers every source.
MATERIAL_FILES: dict[str, SourceKind] = {
    "fgos.txt": SourceKind.NORMATIVE_ACT,
    "prof_standarts.txt": SourceKind.PROFESSIONAL_STANDARD,
    "edu_programs.txt": SourceKind.REFERENCE,
}

#: Header fields of a material record. Everything else in a record is body text,
#: split into fragments by section headings.
_HEADER_KEYS = ("Название", "Тип", "Версия/Дата", "Происхождение", "Статус доверия")

#: Shortest query token that takes part in matching, and the stem length shared
#: by two tokens for a match. Crude on purpose: this is a placeholder for the
#: RAG index, and a real morphology model would be dead weight here.
_MIN_TOKEN_LENGTH = 3
_STEM_LENGTH = 6


class SourceFragment(BaseModel):
    """One retrieved fragment with the document it belongs to."""

    fragment_id: str
    source: Source
    section: str
    text: str


@dataclass
class _MaterialRecord:
    material_id: str
    fields: dict[str, str]
    fragments: list[tuple[str, str]]


def _parse_material(text: str) -> list[_MaterialRecord]:
    """Split one summary file into records of header fields and body fragments."""
    records: list[_MaterialRecord] = []
    current: _MaterialRecord | None = None
    section = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("ID:"):
            current = _MaterialRecord(material_id=line.removeprefix("ID:").strip(), fields={}, fragments=[])
            records.append(current)
            section = ""
            continue
        if current is None or not line:
            continue
        key, separator, value = line.partition(":")
        if separator and key in _HEADER_KEYS:
            current.fields[key] = value.strip()
            current.fragments.append((key, line))
            continue
        if line.endswith(":") and len(line) < 120:
            section = line.removesuffix(":")
            continue
        current.fragments.append((section, line))
    return records


@cache
def _catalog() -> tuple[tuple[Source, ...], tuple[SourceFragment, ...]]:
    """The sources and fragments, loaded once on first use."""
    sources: list[Source] = []
    fragments: list[SourceFragment] = []
    for filename, kind in MATERIAL_FILES.items():
        text = (MATERIALS_DIR / filename).read_text(encoding="utf-8")
        for record in _parse_material(text):
            source = Source(
                id=f"src-{len(sources) + 1:02d}",
                kind=kind,
                title=record.fields.get("Название", record.material_id),
                identifier=record.material_id,
            )
            sources.append(source)
            fragments.extend(
                SourceFragment(fragment_id=f"{source.id}-{index}", source=source, section=section, text=fragment)
                for index, (section, fragment) in enumerate(record.fragments, start=1)
            )
    return tuple(sources), tuple(fragments)


def get_source(source_id: str) -> Source | None:
    """Return the canonical source record with this id, if the catalog has one."""
    sources, _ = _catalog()
    for source in sources:
        if source.id == source_id:
            return source
    return None


def _tokens(text: str) -> list[str]:
    """Lowercase word tokens; punctuation and short words carry no signal."""
    return [token for token in re.findall(r"[а-яёa-z0-9]+", text.lower()) if len(token) >= _MIN_TOKEN_LENGTH]


def _same_stem(first: str, second: str) -> bool:
    """Whether two tokens share a stem, approximated by a common prefix."""
    return first == second or (
        len(first) >= _STEM_LENGTH and len(second) >= _STEM_LENGTH and first[:_STEM_LENGTH] == second[:_STEM_LENGTH]
    )


def _score(query_tokens: list[str], fragment: SourceFragment) -> int:
    """How many distinct query tokens the fragment holds, with a title bonus."""
    text_tokens = _tokens(fragment.text)
    hits = sum(1 for query in query_tokens if any(_same_stem(query, token) for token in text_tokens))
    if hits == 0:
        return 0
    title_tokens = _tokens(fragment.source.title)
    title_bonus = sum(1 for query in query_tokens if any(_same_stem(query, token) for token in title_tokens))
    return hits * 2 + title_bonus


async def search(
    query: str,
    *,
    kinds: Collection[SourceKind] | None = None,
    limit: int = 5,
) -> list[SourceFragment]:
    """Return up to ``limit`` fragments most relevant to the query.

    An empty query or no match at all returns an empty list, which the caller
    reports as "nothing found" rather than as an arbitrary fragment.
    """
    query_tokens = list(dict.fromkeys(_tokens(query)))
    if not query_tokens:
        return []
    _, fragments = _catalog()
    scored = [
        (_score(query_tokens, fragment), fragment)
        for fragment in fragments
        if kinds is None or fragment.source.kind in kinds
    ]
    scored = [(score, fragment) for score, fragment in scored if score > 0]
    scored.sort(key=lambda item: (-item[0], item[1].source.id, item[1].fragment_id))
    return [fragment for _, fragment in scored[:limit]]
