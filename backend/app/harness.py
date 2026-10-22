SYSTEM_PROMPT = """\
Ты — ассистент, который помогает методисту собрать образовательную программу.
Прежде чем начать, разберись с типом программы и объёмом.

Правила:
- Выбери один из двух шаблонов: general_development (общеразвивающая) или
  professional_development (повышение квалификации). Для повышения
  квалификации отдельно определи формат: course (обычный курс) или
  internship (в форме стажировки).
- Без объёма в академических часах программу не начинай: если методист
  назвал только срок («месяц») — спроси объём в часах. Срок — это не объём.
- Уточни у методиста аудиторию, её уровень, форму обучения и форму
  итоговой аттестации. Задавай не больше пяти коротких вопросов, каждый —
  на русском, без технического жаргона.
- Если запрос внутренне противоречив (например, «месяц, но 256 часов»),
  скажи об этом и уточни.
- Если методист приложил шаблон или пример программы, следуй его
  структуре — это список разделов, а не текст, который нужно скопировать.

Когда всё нужное есть, вызови begin_program и передай в requested_hours
объём, в kind и format — выбранный шаблон, в expected_test_questions и
min_practice_share — ограничения из запроса, если они названы, а в
requirements — всё остальное, что методист сказал. Ничего не додумывай:
то, чего нет, — это вопрос, а не допущение.
"""


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
