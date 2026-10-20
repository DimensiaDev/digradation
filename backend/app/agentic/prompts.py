"""Prompts for the sequential programme stages.

The intake chat prompt lives in :mod:`app.harness`; this module owns the prompt a
stage task sends after the intake. Each stage gets one block of the document, the
schema of that block, and an explicit instruction to save it with the
``save_program_draft`` tool. The stage is also written into the prompt as
``STAGE: <name>`` because the offline provider uses it to return the matching
scripted block.
"""

from __future__ import annotations

import json

from app.agentic.models import Stage
from app.agentic.state import STAGE_BLOCKS, ProgramState
from app.providers.models import Content, Message

STAGE_SYSTEM_PROMPT = """\
Ты — ассистент-методист и сейчас работаешь над одним этапом образовательной
программы. Этап ведёт backend: ты заполняешь ровно один блок документа, сохраняешь
его вызовом save_program_draft и останавливаешься. Не пытайся собрать всю
программу сразу и не переходи к следующему этапу самостоятельно.

Правила:
- Опирайся на исходные требования и уже заполненные блоки из состояния ниже.
- Не выдумывай источники. Ссылаться можно только на документы, которые вернул
  search_sources; если поиск ничего не нашёл, оставь список источников пустым.
- Все идентификаторы должны соответствовать шаблонам схемы и быть уникальными.
- Вызови save_program_draft ровно один раз с текущим этапом и блоком в payload.
- После успешного сохранения ответь одним коротким предложением по-русски.
"""

STAGE_INSTRUCTIONS: dict[Stage, str] = {
    Stage.COMPETENCES: """\
Сформируй компетенции программы и результаты освоения. У каждой компетенции
должен быть хотя бы один результат: знание, умение или практический опыт.
Используй id comp-01, comp-02, ... и ro-k-01 / ro-s-01 / ro-e-01 для результатов.
Если компетенция опирается на профессиональный стандарт, укажи standard_source_id
только после того, как search_sources вернул этот документ.
""",
    Stage.HOURS: """\
Сформируй учебный план: модули, темы и распределение часов. Сумма часов по темам
должна быть равна заявленному объёму программы, а график — вмещать этот объём.
Используй id m-01, m-02, ... для модулей и t-01.1, t-01.2 для тем.
""",
    Stage.SOURCES: """\
Найди основания программы вызовом search_sources по теме, видам деятельности и
компетенциям. Сохрани в блоке sources только те документы, которые вернул поиск.
Если поиск ничего не нашёл, сохрани пустой список и не выдумывай идентификаторы.
""",
    Stage.SUMMARY: """\
Сформируй паспорт программы: тип, название и сводный блок summary (цель, задачи,
аудитория, форма обучения, документ по итогам, при необходимости — аннотация,
актуальность и основания). Название должно быть конкретным и отражать тему.
""",
}


def build_stage_system_prompt(stage: Stage) -> str:
    """The system prompt of one stage, including the block's JSON schema."""
    schema = STAGE_BLOCKS[stage].model_json_schema()
    return (
        f"{STAGE_SYSTEM_PROMPT}\n"
        f"STAGE: {stage.value}\n\n"
        f"{STAGE_INSTRUCTIONS[stage]}\n"
        "Схема блока payload (JSON Schema):\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
    )


def build_stage_messages(state: ProgramState) -> list[Message]:
    """The two provider messages that start a stage.

    The state is sent as JSON so the model sees exactly what earlier stages
    committed. It stays a user message rather than a system one: it is data, not
    instructions, and the stage prompt above is the instruction.
    """
    payload = {
        "status": state.status.value,
        "current_stage": state.current_stage.value if state.current_stage else None,
        "requested_hours": state.requested_hours,
        "requirements": state.requirements,
        "title": state.title,
        "kind": state.kind.value if state.kind else None,
        "summary": state.summary.model_dump(mode="json") if state.summary else None,
        "competencies": [item.model_dump(mode="json") for item in state.competencies],
        "curriculum": state.curriculum.model_dump(mode="json") if state.curriculum else None,
        "sources": [item.model_dump(mode="json") for item in state.sources],
    }
    return [
        Message(
            role="system",
            content=[Content(text=build_stage_system_prompt(state.current_stage or Stage.COMPETENCES))],
        ),
        Message(
            role="user",
            content=[
                Content(
                    text=(
                        "Состояние программы на начало этапа:\n"
                        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
                        "Заполни блок текущего этапа и сохрани его вызовом save_program_draft."
                    )
                )
            ],
        ),
    ]
