"""Formatting of the findings a tool hands back to the model.

A refusal is only useful if the model can act on it in one round: it has to say
which value was wrong, which values are allowed, and how many findings it is
looking at. These helpers are shared by the validators in
:mod:`app.agentic.state` and the tool dispatcher, so a message reads the same
wherever it is produced.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

#: How many allowed ids a finding names before it stops listing them.
ID_LIMIT = 12

#: How many findings reach the model in one refusal. A block refused for
#: hundreds of reasons is unreadable, and the model repairs it one call at a
#: time; the count past the limit tells it that the list is not complete.
FINDING_LIMIT = 15

#: How much of a received value a finding repeats.
VALUE_LIMIT = 160


def ids(values: Iterable[str], limit: int = ID_LIMIT) -> str:
    """The allowed identifiers, in order and without repeats."""
    unique = list(dict.fromkeys(value for value in values if value))
    if not unique:
        return "нет"
    shown = ", ".join(unique[:limit])
    if len(unique) > limit:
        return f"{shown} и ещё {len(unique) - limit}"
    return shown


def short(text: str | None, limit: int = VALUE_LIMIT) -> str:
    """A piece of model-written text, cut to a length a finding can carry."""
    if not text:
        return "—"
    flat = " ".join(text.split())
    if len(flat) > limit:
        return flat[: limit - 1] + "…"
    return flat


def quote(value: Any, limit: int = VALUE_LIMIT) -> str:
    """A received value as JSON, so a string and a number read differently."""
    try:
        text = json.dumps(value, ensure_ascii=False)
    except TypeError, ValueError:
        text = repr(value)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def cap(findings: list[str], limit: int = FINDING_LIMIT) -> list[str]:
    """Cut a refusal to the findings the model can hold in one turn."""
    if len(findings) <= limit:
        return findings
    rest = len(findings) - limit
    return [*findings[:limit], f"и ещё {rest} замечаний — исправь все пункты сразу"]
