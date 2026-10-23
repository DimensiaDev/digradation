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
from app.providers.models import Content, FileSchema, Message

#: What every stage is told about its job, in the same words each time.
STAGE_SYSTEM_PROMPT = """\
Ты — методист, который собирает образовательную программу по частям. На каждом этапе ты \
заполняешь один блок документа и сохраняешь его вызовом save_program_draft, после чего пишешь \
одну короткую фразу и останавливаешься. Пиши по-русски, опирайся на задание методиста и уже \
сохранённые блоки и не противоречь им. Не выдумывай документы: ссылаться можно только на то, \
что вернул search_sources или что уже сохранено в программе. Если search_sources вернул \
found=false или пустой список, оставляй ссылки пустыми. Не вызывай инструменты, которых нет в \
этом запросе."""

#: The job of each stage, in the words the model reads.
STAGE_TASKS: dict[Stage, str] = {
    Stage.COMPETENCES: (
        "Сформируй вид и название программы, а также компетенции с результатами освоения. "
        "Вид программы укажи в kind: professional_development (повышение квалификации) или "
        "general_development (общеразвивающая). Для повышения квалификации укажи в format: "
        "course (обычный курс) или internship (в форме стажировки); для общеразвивающей — "
        "оставь format = course. Компетенции нумеруй comp-01, comp-02, …; знания — ro-k-01, "
        "умения — ro-s-01, практический опыт — ro-e-01, нумерация сквозная по программе. "
        "Если компетенция опирается на профессиональный стандарт, сначала найди его через "
        "search_sources и укажи standard_source_id; если поиск ничего не нашёл, оставь "
        "standard_source_id пустым. Сохрани блок вызовом save_program_draft."
    ),
    Stage.HOURS: (
        "Составь учебный план: модули (m-01, m-02, …) и темы (t-01.1 внутри модуля m-01) с часами "
        "по видам занятий и график обучения. Часы модуля не указывай: они считаются суммой часов "
        'его тем. У каждой темы поле hours — объект {"lecture": ..., "practice": ..., '
        '"lab": ..., "self_study": ...}; не пиши просто число. '
        "Часы итоговой аттестации укажи только в attestation_hours, отдельным полем, а не "
        "отдельным модулем и не отдельной темой. Сумма часов тем плюс attestation_hours должна "
        "совпасть с заявленным объёмом. "
        "Пример для 72 часов с аттестацией в 6 часов: модули с темами на 66 часов плюс "
        "attestation_hours: 6 дают 66 + 6 = 72. Отдельный модуль «Итоговая аттестация» делать "
        "не нужно: его часы уже в attestation_hours. "
        "У каждой темы outcome_ids — это идентификаторы результатов обучения (ro-k-01, ro-s-01, "
        "ro-e-01), взятые из полей knowledge, skills и experience уже сохранённых компетенций, "
        "а не идентификаторы компетенций (comp-01). Каждый результат обучения должен быть раскрыт "
        "хотя бы в одной теме. Если программа очно-заочная, укажи, сколько часов проходит "
        "дистанционно. Сохрани блок вызовом save_program_draft."
    ),
    Stage.ASSESSMENT: (
        "Составь оценочные средства программы: тестовые вопросы с ключами, задания для семинаров "
        "с критериями оценки, формы текущего контроля по темам и итоговую аттестацию. Каждый "
        "результат обучения проверяй хотя бы двумя средствами, а вопросы и задания привязывай к "
        "темам учебного плана через topic_ids. Часы итоговой аттестации должны совпадать с "
        "часами, отведёнными на неё в учебном плане. Сохрани блок вызовом save_program_draft."
    ),
    Stage.MATERIALS: (
        "Подготовь материалы к занятиям: план лекции со слайдами для каждой темы учебного плана. "
        "Планы лекций и слайды готовятся хотя бы для одного модуля, но лучше для всех тем, "
        "у которых есть часы лекций. Сохрани блок вызовом save_program_draft."
    ),
    Stage.SOURCES: (
        "Подбери источники программы: нормативные акты, профессиональные стандарты, литературу. "
        "Ищи через search_sources и сохраняй только то, что вернул поиск, с теми же id. Если поиск "
        "ничего не нашёл, сохрани пустой список — выдумывать документы запрещено. Документ, которого "
        "нет в каталоге, всё равно можно указать: он попадёт в программу помеченным как "
        "неподтверждённый, и методист его проверит. Сохрани блок вызовом save_program_draft."
    ),
    Stage.SUMMARY: (
        "Составь паспорт программы: цель, задачи, целевую аудиторию, требования к слушателям, "
        "аннотацию, актуальность, форму обучения, документ по итогам и направленность. "
        "Документ по итогам зависит от вида программы: для professional_development это "
        "«Удостоверение о повышении квалификации», для general_development — «Сертификат об "
        "освоении программы». Не подставляй сертификат программе повышения квалификации. "
        "В basis_source_ids перечисли id уже сохранённых источников, на которые программа "
        "опирается; если источников нет, оставь список пустым. Сохрани блок вызовом "
        "save_program_draft."
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
    if state.revision is not None:
        parts.append(
            f"Правка от методиста к этому блоку:\n{state.revision.notes}\n"
            "Переделай только этот блок, сохранив согласованность с остальной программой: "
            "идентификаторы, объём часов и связи с результатами обучения не меняй без нужды."
        )
    if state.error:
        # The previous attempt of this stage died on this, and the pipeline did
        # not keep its conversation. Without it the retry starts from the same
        # wrong plan and spends its budget on the same mistake.
        parts.append(f"Прошлая попытка этого этапа не удалась:\n{state.error}")
    blocks = saved_blocks(state)
    saved = json.dumps(blocks, ensure_ascii=False, indent=2) if blocks else "пока ничего"
    parts.append(f"Уже сохранено:\n{saved}")
    parts.append(f"Твой этап: {STAGE_TASKS[state.current_stage]}")
    return "\n\n".join(parts)


def _answered_questions(state: ProgramState) -> list[tuple[ClarificationQuestion, ClarificationAnswer]]:
    """The questions and the answers that belong to them, in the order asked."""
    by_id = {answer.question_id: answer for answer in state.answers}
    return [(question, by_id[question.id]) for question in state.questions if question.id in by_id]


def stage_messages(state: ProgramState, files: list[str] | None = None) -> list[Message]:
    """The conversation of one stage: the standing rules and the brief.

    The files are the ones the methodist attached to the request. The provider
    reads them the way it reads a message, so a stage sees the template or the
    approved programme the request came with, and not only the description of
    it that the intake wrote down.
    """
    attachments = [FileSchema(id=file_id) for file_id in files or []]
    return [
        Message(role="system", content=[Content(text=STAGE_SYSTEM_PROMPT)]),
        Message(role="user", content=[Content(text=stage_brief(state), files=attachments)]),
    ]
