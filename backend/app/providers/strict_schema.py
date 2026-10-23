"""Close a pydantic JSON schema so a strict tool decoder enforces it.

The hub behind ``SBER_API_SCOPE=openai`` accepts ``strict: true`` on a function
and constrains generation to the schema, which is what keeps a stage from
spending a round on malformed arguments. It does not validate the schema the
way OpenAI does: an object may omit ``additionalProperties`` and a property may
stay optional, the request still succeeds, and enforcement is weaker than the
flag promises. :func:`strict_schema` closes those two holes, so every object is
sealed and every property is required.

Only the structural keywords survive the round trip. Bounds (``minLength``,
``maximum``, ``pattern``) and annotations (``default``, ``title``, ``examples``)
are dropped from the wire because a decoder has no agreed support for them;
pydantic still checks every one of them on the way into the block, so a value
that is too long is refused as before. What is lost is only the constraint the
decoder might silently ignore. ``description`` is kept: it is the part of the
schema the model actually reads.
"""

from __future__ import annotations

from typing import Any

#: Keywords copied through unchanged, besides the ones handled structurally.
_PASS_THROUGH = ("type", "description", "enum", "const", "anyOf", "oneOf", "allOf", "items")


def strict_schema(schema: Any) -> Any:
    """Return the strict form of ``schema``.

    A dict that describes an object becomes a closed object whose every property
    is required; arrays recurse into their items; ``anyOf``/``oneOf``/``allOf``
    recurse into every branch. Anything else is copied with only the keywords a
    decoder is known to understand.
    """
    if isinstance(schema, list):
        return [strict_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    properties = schema.get("properties")
    if isinstance(properties, dict) or schema.get("type") == "object":
        return _closed_object(schema, properties or {})

    result: dict[str, Any] = {}
    for key in _PASS_THROUGH:
        if key not in schema:
            continue
        value = schema[key]
        if key == "items":
            result[key] = strict_schema(value)
        elif key in {"anyOf", "oneOf", "allOf"}:
            result[key] = [strict_schema(branch) for branch in value]
        else:
            result[key] = value
    return result


def _closed_object(schema: dict, properties: dict) -> dict[str, Any]:
    """One object with every property required and no property outside the set."""
    result: dict[str, Any] = {
        "type": "object",
        "properties": {name: strict_schema(value) for name, value in properties.items()},
        "required": list(properties),
        "additionalProperties": False,
    }
    if isinstance(schema.get("description"), str):
        result["description"] = schema["description"]
    return result
