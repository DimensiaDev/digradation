"""Prompts of the sequential programme pipeline.

Each stage is one model turn with one job: fill one block of the document and
save it. The prompt is rebuilt from the state, never from a stored transcript,
because the state is the only thing that survives a worker death.
"""

from __future__ import annotations

import json
from typing import Any

from app.agentic.models import Stage
from app.agentic.state import ClarificationAnswer, ClarificationQuestion, ProgramState
from app.providers.models import Content, Message

#: What every stage is told about its job, in the same words each time.
STAGE_SYSTEM_PROMPT = """\
Ты — методист, который собирает образовательную программу по частям. На каждом этапе ты \
заполняешь один блок документа и сохраняешь его вызовом save_program_draft, после чего пишешь \
одну короткую фразу и останавливаешься. Пиши по-русски, опирайся на задание методиста и уже \
сохранённые блоки и не противоречь им. Не выдумывай документы: ссылаться можно только на то, \
что вернул search_sources или что уже сохранено в программе. Если поиск ничего не нашёл, \
оставляй ссылки пустыми. Не вызывай инструменты, которых нет в этом запросе."""

#: The job of each stage, in the words the model reads.
STAGE_TASKS: dict[Stage, str] = {
    Stage.COMPETENCES: (
        "Сформируй вид и название программы, а также компетенции с результатами освоения. "
        "Вид программы: professional_development (повышение квалификации) или general_development "
        "(общеразвивающая). Компетенции нумеруй comp-01, comp-02, …; знания — ro-k-01, умения — "
        "ro-s-01, практический опыт — ro-e-01, нумерация сквозная по программе. Если компетенция "
        "опирается на профессиональный стандарт, сначала найди его через search_sources и укажи "
        "standard_source_id; если поиск ничего не нашёл, оставь standard_source_id пустым. Сохрани "
        "блок вызовом save_program_draft."
    ),
    Stage.HOURS: (
        "Составь учебный план: модули (m-01, m-02, …) и темы (t-01.1 внутри модуля m-01) с часами "
        "по видам занятий и график обучения. Темы ссылаются на уже сохранённые результаты освоения "
        "через outcome_ids. Сохрани блок вызовом save_program_draft."
    ),
    Stage.SOURCES: (
        "Подбери источники программы: нормативные акты, профессиональные стандарты, литературу. "
        "Ищи через search_sources и сохраняй только то, что вернул поиск, с теми же id. Если поиск "
        "ничего не нашёл, сохрани пустой список — выдумывать документы запрещено. Сохрани блок "
        "вызовом save_program_draft."
    ),
    Stage.SUMMARY: (
        "Составь паспорт программы: цель, задачи, целевую аудиторию, требования к слушателям, "
        "аннотацию, актуальность, форму обучения, документ по итогам и направленность. В "
        "basis_source_ids перечисли id уже сохранённых источников, на которые программа опирается; "
        "если источников нет, оставь список пустым. Сохрани блок вызовом save_program_draft."
    ),
}


def saved_blocks(state: ProgramState) -> dict[str, Any]:
    """The blocks the earlier stages committed, as the model should see them."""
    blocks: dict[str, Any] = {}
    if state.kind is not None:
        blocks["вид программы"] = state.kind.value
    if state.title:
        blocks["название"] = state.title
    if state.competencies:
        blocks["компетенции"] = [competence.model_dump(mode="json") for competence in state.competencies]
    if state.curriculum is not None:
        blocks["учебный план"] = state.curriculum.model_dump(mode="json")
    if state.sources:
        blocks["источники"] = [source.model_dump(mode="json") for source in state.sources]
    if state.summary is not None:
        blocks["паспорт"] = state.summary.model_dump(mode="json")
    return blocks


def stage_brief(state: ProgramState) -> str:
    """The user turn of a stage: the task, the volume and what is already saved."""
    parts = [f"Задание методиста:\n{state.requirements or 'не задано'}"]
    if state.requested_hours is not None:
        parts.append(f"Заявленный объём программы: {state.requested_hours:g} академических часов.")
    if state.answers:
        answered = "\n".join(f"- {question.text}: {answer.text}" for question, answer in _answered_questions(state))
        parts.append(f"Ответы методиста на уточняющие вопросы:\n{answered}")
    blocks = saved_blocks(state)
    saved = json.dumps(blocks, ensure_ascii=False, indent=2) if blocks else "пока ничего"
    parts.append(f"Уже сохранено:\n{saved}")
    parts.append(f"Твой этап: {STAGE_TASKS[state.current_stage]}")
    return "\n\n".join(parts)


def _answered_questions(state: ProgramState) -> list[tuple[ClarificationQuestion, ClarificationAnswer]]:
    """The questions and the answers that belong to them, in the order asked."""
    by_id = {answer.question_id: answer for answer in state.answers}
    return [(question, by_id[question.id]) for question in state.questions if question.id in by_id]


def stage_messages(state: ProgramState) -> list[Message]:
    """The conversation of one stage: the standing rules and the brief."""
    return [
        Message(role="system", content=[Content(text=STAGE_SYSTEM_PROMPT)]),
        Message(role="user", content=[Content(text=stage_brief(state))]),
    ]
