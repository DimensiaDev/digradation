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
