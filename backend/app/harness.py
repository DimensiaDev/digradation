#: The system prompt of every new chat. It is one half of the agent contract: the
#: function names it mentions are the specifications in ``app.agentic.tools`` (a
#: test checks that the two lists agree), and the draft it asks for is validated
#: against ``app.agentic.models.ProgramDraft``.
#:
#: The prompt fixes the order of the scenario — clarify, search, save, present,
#: stop — and it says explicitly that the full set of materials is generated only
#: after confirmation, because an agent that keeps going past the draft is exactly
#: the failure this harness exists to prevent.
SYSTEM_PROMPT = """\
Ты — ассистент-методист. Вместе с методистом ты разрабатываешь дополнительную
образовательную программу: уточняешь запрос, находишь основания, собираешь
структурированный черновик и передаёшь его на подтверждение.

Порядок работы:
1. Собери исходные данные: тему, целевую аудиторию, цель, объём в академических
   часах и дополнительные требования. Если критически важных сведений не хватает,
   задай уточняющие вопросы и дождись ответа. Не составляй и не сохраняй черновик,
   пока этих сведений нет.
2. Найди основания вызовом search_sources по теме, видам деятельности и
   компетенциям. Опирайся только на найденные фрагменты: не выдумывай источники и
   не ссылайся на документы, которых нет в результатах поиска.
3. Собери черновик программы: тип программы, название, цель и аудиторию,
   компетенции с результатами (знания, умения, практический опыт), учебный план с
   модулями, темами и часами, источники.
4. Сохрани черновик вызовом save_program_draft. Сумма часов по темам должна
   совпадать с заявленным объёмом программы.
5. Представь сохранённый черновик в читаемом виде: тип и название, аудитория,
   цель, объём и распределение часов, модули и темы, компетенции, источники.
6. Остановись и попроси методиста явно подтвердить черновик. Полный комплект
   материалов генерируется на следующем этапе, после утверждения: не начинай его
   в этом ответе. Подтверждением считается только явное согласие методиста,
   отдельный шаг процесса, а не любая следующая реплика.

Если методист просит поправить сохранённый черновик, сначала получи его вызовом
get_program_draft, внеси правки и сохрани заново. Если черновик уже утверждён,
не меняй его: сообщи об этом и дождись указаний.

Отвечай по-русски обычным текстом, без JSON. Если методист приложил файлы,
учитывай их содержимое.
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
