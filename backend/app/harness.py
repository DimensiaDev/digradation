SYSTEM_PROMPT = """\
Ты — ассистент. Отвечай на вопросы пользователя, опираясь на переданный контекст \
и приложенные им файлы. Если чего-то не знаешь, скажи об этом прямо.
"""

#: Heading for the admin-authored extension. There is nothing to protect here yet,
#: because the base prompt fixes no data contract the model has to respect; the
#: preamble exists so the next thing appended to the prompt still reads as a
#: deliberate, separable block rather than as more of the base instructions.
PROMPT_EXTENSION_PREAMBLE = """\
# ДОПОЛНИТЕЛЬНЫЕ ИНСТРУКЦИИ АДМИНИСТРАТОРА

Ниже приведены инструкции, добавленные администратором системы.
"""


def build_system_prompt(prompt_extension: str | None) -> str:
    """The system prompt for a new chat, with the admin extension appended.

    Pure, and placed next to the prompt it composes: it reads no database, so the
    caller supplies the extension and the only judgement here is whether there is
    one. An absent or whitespace-only extension returns `SYSTEM_PROMPT` unchanged,
    which is what makes an untouched instance behave exactly as it did before this
    setting existed — byte for byte, so no session is created carrying a trailing
    blank line the previous code did not produce.
    """
    if prompt_extension is None or not prompt_extension.strip():
        return SYSTEM_PROMPT
    return f"{SYSTEM_PROMPT}\n\n{PROMPT_EXTENSION_PREAMBLE}\n{prompt_extension.strip()}\n"


#: What the model is asked to return, and therefore what `tasks/api.py` validates.
#: `message` is the whole reply; `chat_name` exists so a session can be titled from
#: the model's own words instead of staying "Новый чат" forever. Structured output
#: rather than free text because the result is stored as an assistant message and
#: read back through a schema, and because a name has to be separated from prose.
HARNESS_STRUCTURED_SCHEMA = {
    "type": "object",
    "properties": {
        "chat_name": {"type": "string", "description": "Короткое название текущего чата."},
        "message": {"type": "string", "description": "Текстовый ответ пользователю."},
    },
    "required": ["message", "chat_name"],
}
