"""Tools of the programme pipeline: the specs the model sees and their handlers.

The specs are one half of the contract and the block models of
:mod:`app.agentic.state` are the other: the model fills the arguments from the
spec, the dispatcher validates them against the model, and the handler works with
a validated block.

Handlers keep no storage of their own. They move the run state on
:class:`ToolContext` and leave it to the caller to checkpoint, which is what lets
a worker that dies mid-stage lose nothing.
"""

import copy
import json
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Annotated, Any

import structlog.stdlib
from pydantic import BaseModel, Field, ValidationError

from app.agentic import actions, sources, websearch
from app.agentic.errors import ProgramError
from app.agentic.models import ProgramFormat, ProgramKind, ProgramStatus, Source, SourceKind, Stage
from app.agentic.sources import RagUnavailable
from app.agentic.state import (
    MAX_QUESTION_LENGTH,
    STAGE_ORDER,
    AssessmentBlock,
    CompetencesBlock,
    HoursBlock,
    MaterialsBlock,
    ProgramState,
    SourcesBlock,
    SummaryBlock,
    new_state,
)
from app.providers.models import FunctionCall, FunctionDefinition, Tool, ToolCall
from app.rag.catalog import canonical_source_id

log = structlog.stdlib.get_logger(__name__)

#: One name for the save tool, with the arguments changing per stage, so the
#: model has one habit to learn.
SAVE_PROGRAM_DRAFT = "save_program_draft"

#: The block model of each stage, in the order the stages run.
STAGE_BLOCK: dict[Stage, type[BaseModel]] = {
    Stage.COMPETENCES: CompetencesBlock,
    Stage.HOURS: HoursBlock,
    Stage.SOURCES: SourcesBlock,
    Stage.SUMMARY: SummaryBlock,
    Stage.ASSESSMENT: AssessmentBlock,
    Stage.MATERIALS: MaterialsBlock,
}

#: Stages that may look documents up before saving their block.
SEARCHING_STAGES = frozenset({Stage.COMPETENCES, Stage.SOURCES, Stage.SUMMARY})


@dataclass
class ToolContext:
    """What a handler needs beyond the arguments of the call."""

    session_id: uuid.UUID
    state: ProgramState | None = None
    dirty: bool = False
    #: Whether the stage's block was accepted in this turn.
    saved: bool = False
    #: The person the turn belongs to. A decision the methodist takes is
    #: recorded against them, so the state says who approved the programme.
    user_id: uuid.UUID | None = None
    #: What the last refused call was told to fix. A stage that gives up hands
    #: it to the retry, which otherwise starts from the same wrong plan.
    last_problems: list[str] = field(default_factory=list)


class ToolResult(BaseModel):
    """A refusal shares the shape of a success, so the model can correct itself."""

    ok: bool = True
    error: str | None = None
    details: list[str] | None = None
    note: str | None = None


class SearchSourcesResult(ToolResult):
    query: str
    found: bool = False
    fragments: list[sources.SourceFragment] = Field(default_factory=list)


class SaveProgramDraftResult(ToolResult):
    status: ProgramStatus | None = None
    stage: Stage | None = None


class BeginProgramResult(ToolResult):
    stage: Stage | None = None


class ApproveProgramDraftResult(ToolResult):
    """What confirming the drafted programme tells the model."""


class ReviseProgramResult(ToolResult):
    """What a requested change tells the model."""

    stage: Stage | None = None


class SearchSourcesArguments(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=500,
        description="Поисковый запрос: тема, вид деятельности или компетенция",
    )
    kinds: list[SourceKind] = Field(default_factory=list, description="Ограничить типы источников (необязательно)")
    limit: int = Field(default=5, ge=1, le=10, description="Сколько фрагментов вернуть")


class BeginProgramArguments(BaseModel):
    requirements: str = Field(
        min_length=1,
        max_length=4000,
        description=(
            "Всё, что методист сказал о программе: тема, аудитория, цель, требования, "
            "пожелания к содержанию и объёму"
        ),
    )
    requested_hours: float | None = Field(
        default=None,
        gt=0,
        le=1000,
        description="Объём программы в академических часах, если методист его назвал",
    )
    kind: ProgramKind | None = Field(
        default=None,
        description="Шаблон программы: general_development или professional_development",
    )
    format: ProgramFormat | None = Field(
        default=None,
        description="Формат: course или internship. Заполняется только для professional_development",
    )
    expected_test_questions: int | None = Field(
        default=None,
        gt=0,
        le=200,
        description="Сколько вопросов в итоговом тесте попросил методист, если он это назвал",
    )
    min_practice_share: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Минимальная доля практических часов от общего объёма, если методист её назвал",
    )
    questions: list[Annotated[str, Field(max_length=MAX_QUESTION_LENGTH)]] = Field(
        default_factory=list,
        max_length=5,
        description=(
            "Вопросы методисту, без которых программу начать нельзя — не больше пяти. Если список "
            "непуст, программа не формируется, пока методист на них не ответит."
        ),
    )


class ApproveProgramDraftArguments(BaseModel):
    """Arguments of :func:`approve_program_draft`: the tool takes none."""


class ReviseProgramArguments(BaseModel):
    """What the methodist wants changed in one part of the programme."""

    stage: Stage = Field(
        description=(
            "Какой блок переделать: competences, hours, sources, summary, assessment или materials. "
            "Ставь тот блок, в котором нужно изменение, а не тот, который нужно закончить."
        )
    )
    notes: str = Field(
        min_length=1,
        max_length=2000,
        description="Что именно изменить: например, заменить тему 2.3 или усложнить тест",
    )


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline ``$defs``/``$ref`` so the parameters are one self-contained object."""
    definitions = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                return resolve(copy.deepcopy(definitions[reference.removeprefix("#/$defs/")]))
            return {key: resolve(value) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


SEARCH_SOURCES_SPEC = FunctionDefinition(
    name="search_sources",
    description=(
        "Найти релевантные фрагменты в базе источников: ФГОС, профессиональные стандарты, "
        "примеры образовательных программ, а также в интернете. Возвращает фрагменты с "
        "идентификаторами документов; ссылаться в программе можно только на документы из "
        "результата поиска. Документ, которого нет в базе источников, попадёт в программу "
        "помеченным как неподтверждённый."
    ),
    parameters=_inline_refs(SearchSourcesArguments.model_json_schema()),
)

BEGIN_PROGRAM_SPEC = FunctionDefinition(
    name="begin_program",
    description=(
        "Запустить формирование образовательной программы по заданию методиста. "
        "Вызывай, когда из сообщения понятны тема и аудитория программы. Не вызывай повторно: "
        "программа формируется этапами сама. Если чего-то не хватает, перечисли это в questions — "
        "программа начнёт формироваться после ответов методиста."
    ),
    parameters=_inline_refs(BeginProgramArguments.model_json_schema()),
)

APPROVE_PROGRAM_DRAFT_SPEC = FunctionDefinition(
    name="approve_program_draft",
    description=(
        "Подтвердить готовый черновик программы. Вызывай только после того, как методист явно "
        "согласился с черновиком; при сомнении уточни, чего не хватает."
    ),
    parameters=_inline_refs(ApproveProgramDraftArguments.model_json_schema()),
)

REVISE_PROGRAM_SPEC = FunctionDefinition(
    name="revise_program",
    description=(
        "Переделать одну часть готовой программы по просьбе методиста: например, заменить тему 2.3 "
        "или усложнить тест. Вызывай, когда методист попросил изменить конкретную часть программы; "
        "пересобирается только эта часть, остальное остаётся как есть. В notes напиши, что именно "
        "изменить."
    ),
    parameters=_inline_refs(ReviseProgramArguments.model_json_schema()),
)

#: What each stage's save call is for, in the words the model reads.
_SAVE_DESCRIPTIONS: dict[Stage, str] = {
    Stage.COMPETENCES: (
        "Сохранить название и тип программы, а также компетенции с результатами освоения: "
        "знаниями, умениями и практическим опытом."
    ),
    Stage.HOURS: (
        "Сохранить учебный план: модули, темы с часами и график обучения. Часы модуля считаются по "
        "его темам; сумма часов тем вместе с итоговой аттестацией должна совпадать с заявленным "
        "объёмом программы."
    ),
    Stage.SOURCES: (
        "Сохранить источники, на которые опирается программа. Ссылайся только на документы, "
        "которые вернул search_sources."
    ),
    Stage.SUMMARY: "Сохранить паспорт программы: цель, задачи, аудиторию, форму обучения и документ по итогам.",
    Stage.ASSESSMENT: (
        "Сохранить оценочные средства: тестовые вопросы с ключами, задания для семинаров с критериями "
        "оценки, формы текущего контроля и итоговую аттестацию."
    ),
    Stage.MATERIALS: "Сохранить планы лекций и слайды к темам учебного плана.",
}

#: The save specification of each stage, built from that stage's block model, so
#: the model never sees the fields of a block it is not writing.
STAGE_SPECS: dict[Stage, FunctionDefinition] = {
    stage: FunctionDefinition(
        name=SAVE_PROGRAM_DRAFT,
        description=_SAVE_DESCRIPTIONS[stage],
        parameters=_inline_refs(STAGE_BLOCK[stage].model_json_schema()),
    )
    for stage in STAGE_ORDER
}


async def search_sources(_ctx: ToolContext, arguments: SearchSourcesArguments) -> SearchSourcesResult:
    """Search the catalogue, the index and the web.

    Three places hold documents: the curated catalogue, the pgvector index over
    the parsed corpus, and the web. The search asks the first two together and
    the third on its own, so a document the corpus holds and a document only the
    web knows about are both found.
    """
    unavailable = False
    try:
        fragments = await sources.search(arguments.query, kinds=arguments.kinds or None, limit=arguments.limit)
    except RagUnavailable as error:
        log.warning("source_search_unavailable", query=arguments.query, error=str(error))
        unavailable = True
        fragments = await sources.search_corpus(
            arguments.query,
            kinds=arguments.kinds or None,
            limit=arguments.limit,
        )
    for document in await websearch.search_web(arguments.query, limit=arguments.limit):
        fragments.append(
            sources.SourceFragment(
                fragment_id=document.url,
                source=document.as_source(),
                section=document.title,
                text=document.snippet,
            )
        )
    if not fragments:
        note = (
            "Поиск по источникам временно недоступен. Продолжай без ссылок на документы: "
            "не выдумывай источники и оставь ссылки пустыми, если поиск не вернул фрагменты."
            if unavailable
            else (
                "По запросу ничего не найдено. Сформулируй запрос иначе или уточни вид деятельности; "
                "не выдумывай источники и не ссылайся на документы, которых нет в результатах."
            )
        )
        return SearchSourcesResult(query=arguments.query, found=False, note=note)
    note = None
    if unavailable:
        note = (
            "Индекс источников недоступен, ответы дал каталог. Если этого мало, скажи методисту: "
            "ссылаться на документы, которых нет в ответе, нельзя."
        )
    return SearchSourcesResult(query=arguments.query, found=True, fragments=fragments, note=note)


@dataclass(frozen=True)
class _Citations:
    """What resolving a set of citations against the catalog found."""

    #: The records the catalog holds for the cited ids.
    sources: list[Source]
    #: Cited ids the catalog does not hold: kept, but marked as unconfirmed.
    unknown: list[str]
    #: Professional standards a competence cites that the catalog does not hold.
    unknown_standards: list[str]
    #: Cited ids that are in the catalog but are not professional standards.
    not_standards: list[str]


def _resolve_sources(source_ids: Iterable[str], *, standards: Iterable[str] = ()) -> _Citations:
    """Bind the cited ids to the catalogue, collecting what could not be bound.

    A source the catalogue does not hold is not a refusal: the programme keeps it
    and marks it as unconfirmed, because the methodist has to see that nobody
    verified the document. A professional standard a competence rests on is a
    refusal, because a competence has to stand on a standard that exists.
    """
    citations = _Citations(sources=[], unknown=[], unknown_standards=[], not_standards=[])
    seen: set[str] = set()
    for raw_source_id in source_ids:
        source_id = canonical_source_id(raw_source_id)
        if source_id in seen:
            continue
        seen.add(source_id)
        record = sources.get_source(source_id)
        if record is None:
            citations.unknown.append(source_id)
        else:
            citations.sources.append(record)
    for source_id in dict.fromkeys(standards):
        source_id = canonical_source_id(source_id)
        record = sources.get_source(source_id)
        if record is None:
            citations.unknown_standards.append(source_id)
        elif record.kind is not SourceKind.PROFESSIONAL_STANDARD:
            citations.not_standards.append(source_id)
    return citations


def _citation_error(citations: _Citations) -> SaveProgramDraftResult | None:
    """The refusal a bad citation earns, or ``None`` when every one resolved."""
    if citations.unknown_standards:
        return SaveProgramDraftResult(
            ok=False,
            error="Профессиональные стандарты, которых нет в каталоге: " + ", ".join(citations.unknown_standards),
            details=[
                "Найди профессиональный стандарт вызовом search_sources и укажи его id: "
                "компетенция должна опираться на стандарт, который существует."
            ],
        )
    if citations.not_standards:
        return SaveProgramDraftResult(
            ok=False,
            error="Не профессиональные стандарты в standard_source_id: " + ", ".join(citations.not_standards),
            details=["Компетенция опирается на профстандарт: у стандарта kind = professional_standard."],
        )
    return None


def _no_ids(_block: Any) -> Iterable[str]:
    return ()


def _catalogued(source: Source) -> Source:
    """The catalogue record of a source, where the catalogue holds one.

    A document the catalogue does not hold keeps what the model wrote about
    it and is marked as unconfirmed: nobody verified it, and the methodist has
    to see that.
    """
    source_id = canonical_source_id(source.id)
    record = sources.get_source(source_id)
    if record is None:
        return source.model_copy(update={"id": source_id, "confirmed": False})
    return record.model_copy(update={"primary": source.primary, "url": source.url or record.url})


def _no_problems(_state: ProgramState, _block: Any) -> list[str]:
    return []


def _standard_ids(block: CompetencesBlock) -> Iterable[str]:
    return [competence.standard_source_id for competence in block.competencies if competence.standard_source_id]


def _source_ids(block: SourcesBlock) -> Iterable[str]:
    return [source.id for source in block.sources]


def _basis_ids(block: SummaryBlock) -> Iterable[str]:
    return block.summary.basis_source_ids


def _basis_problems(state: ProgramState, block: SummaryBlock) -> list[str]:
    """The passport may only rest on sources the programme already lists."""
    known = {source.id for source in state.sources}
    return [
        f"источник {source_id} упомянут в паспорте, но не входит в список источников"
        for source_id in block.summary.basis_source_ids
        if source_id not in known
    ]


def _derived_module_hours(block: HoursBlock) -> HoursBlock:
    """Replace every declared module volume with the sum of its topics.

    The model states both the module's volume and its topics, and the two
    disagree: the attestation module is the usual case, because its hours live
    in ``attestation_hours`` and its topic carries none. The topics are what
    the plan is built from, so the declared number is replaced rather than
    refused, and a plan can no longer contradict itself.
    """
    modules = [module.model_copy(update={"hours": module.topic_hours}) for module in block.curriculum.modules]
    return block.model_copy(update={"curriculum": block.curriculum.model_copy(update={"modules": modules})})


def _hours_problems(state: ProgramState, block: HoursBlock) -> list[str]:
    declared = {
        outcome.id
        for competence in state.competencies
        for outcome in (*competence.experience, *competence.skills, *competence.knowledge)
    }
    return (
        state.topic_problems(block.curriculum, declared)
        + state.uncovered_outcome_problems(block.curriculum, declared)
        + state.curriculum_problems(block.curriculum)
    )


def _assessment_problems(state: ProgramState, block: AssessmentBlock) -> list[str]:
    """The assessment has to check the programme that is already committed.

    It also has to be something: an assessment without a question, without a
    seminar task and without a form of control checks nothing.
    """
    if state.curriculum is None:
        return ["учебный план ещё не собран"]
    assessment = block.assessment
    problems = state.assessment_problems(assessment, state.curriculum)
    if not (assessment.questions or assessment.seminar_tasks or assessment.tools):
        problems.append("оценочные средства не содержат ни вопросов, ни заданий, ни форм контроля")
    return problems


def _materials_problems(state: ProgramState, block: MaterialsBlock) -> list[str]:
    """Every plan and every deck has to belong to a topic of the plan.

    The materials also have to be there: a curriculum with no lecture plan and
    no slides is a curriculum with nothing in it, and the criterion asks for
    the slides of at least one module.
    """
    if state.curriculum is None:
        return ["учебный план ещё не собран"]
    topics = {topic.id for module in state.curriculum.modules for topic in module.topics}
    problems = []
    if not block.materials.lecture_plans:
        problems.append("материалы не содержат ни одного плана лекции")
    if not block.materials.slide_decks:
        problems.append("материалы не содержат ни одного набора слайдов")
    for plan in block.materials.lecture_plans:
        if plan.topic_id not in topics:
            problems.append(f"план лекции {plan.id} ссылается на несуществующую тему {plan.topic_id}")
    for deck in block.materials.slide_decks:
        if deck.topic_id not in topics:
            problems.append(f"слайды {deck.id} ссылаются на несуществующую тему {deck.topic_id}")
    return problems


@dataclass(frozen=True)
class _Save:
    """How one stage's block becomes state."""

    apply: Callable[[ProgramState, Any, list[Source]], ProgramState]
    note: str
    cited: Callable[[Any], Iterable[str]] = _no_ids
    standards: Callable[[Any], Iterable[str]] = _no_ids
    problems: Callable[[ProgramState, Any], list[str]] = _no_problems
    rejected: str = "Блок не принят"
    retry: str | None = None


_SAVERS: dict[type[BaseModel], _Save] = {
    CompetencesBlock: _Save(
        apply=lambda state, block, sources: state.with_competences(block, sources),
        cited=_standard_ids,
        standards=_standard_ids,
        note="Компетенции сохранены. Кратко подтверди, что блок готов, и остановись.",
    ),
    HoursBlock: _Save(
        apply=lambda state, block, _sources: state.with_curriculum(block),
        problems=_hours_problems,
        rejected="Учебный план не принят",
        retry="Исправь часы и ссылки на результаты освоения и сохрани блок снова.",
        note="Учебный план сохранён. Кратко подтверди, что блок готов, и остановись.",
    ),
    SourcesBlock: _Save(
        apply=lambda state, block, _sources: state.with_sources(block),
        cited=_source_ids,
        note="Источники сохранены. Кратко подтверди, что блок готов, и остановись.",
    ),
    SummaryBlock: _Save(
        apply=lambda state, block, sources: state.with_summary(block, sources),
        cited=_basis_ids,
        problems=_basis_problems,
        rejected="Паспорт программы не принят",
        retry="Укажи в списке источников те документы, на которые опирается программа, и сохрани блок снова.",
        note="Паспорт программы сохранён. Черновик собирается, подтверждение попросит методист.",
    ),
    AssessmentBlock: _Save(
        apply=lambda state, block, _sources: state.with_assessment(block),
        problems=_assessment_problems,
        rejected="Оценочные средства не приняты",
        retry="Исправь привязки к темам и результатам обучения и добавь второе средство проверки "
        "для каждого результата.",
        note="Оценочные средства сохранены. Кратко подтверди, что блок готов, и остановись.",
    ),
    MaterialsBlock: _Save(
        apply=lambda state, block, _sources: state.with_materials(block),
        problems=_materials_problems,
        rejected="Материалы не приняты",
        retry="Укажи темы из учебного плана и сохрани блок снова.",
        note="Материалы сохранены. Кратко подтверди, что блок готов, и остановись.",
    ),
}


async def save_program_draft(ctx: ToolContext, arguments: BaseModel) -> SaveProgramDraftResult:
    """Keep the block the stage produced, after binding its citations."""
    state = ctx.state
    if state is None:
        return SaveProgramDraftResult(ok=False, error="Программа ещё не начата")
    rule = _SAVERS.get(type(arguments))
    if rule is None:
        return SaveProgramDraftResult(ok=False, error="Неизвестный блок программы")
    if isinstance(arguments, HoursBlock):
        arguments = _derived_module_hours(arguments)
    if problems := rule.problems(state, arguments):
        return SaveProgramDraftResult(ok=False, error=rule.rejected, details=problems, note=rule.retry)
    citations = _resolve_sources(rule.cited(arguments), standards=rule.standards(arguments))
    if error := _citation_error(citations):
        return error
    if isinstance(arguments, SourcesBlock):
        # Where the catalogue holds the document, its record is what the draft
        # cites; a document the catalogue does not hold keeps what the model
        # wrote and is marked as unconfirmed, because nobody verified it.
        arguments = arguments.model_copy(update={"sources": [_catalogued(source) for source in arguments.sources]})
    ctx.state = rule.apply(state, arguments, citations.sources)
    ctx.dirty = ctx.saved = True
    return SaveProgramDraftResult(
        status=ctx.state.status,
        stage=ctx.state.current_stage,
        note=_note(rule, citations),
    )


def _note(rule: _Save, citations: _Citations) -> str:
    """What the stage is told after its block was accepted.

    A source the catalog does not hold is not refused, but the model has to
    hear about it: the programme will mark the source as unconfirmed, and the
    methodist has to know that nobody verified the document.
    """
    if not citations.unknown:
        return rule.note
    listed = ", ".join(citations.unknown)
    return (
        f"{rule.note} Каталог не подтвердил: {listed} — в программе они помечены как "
        "неподтверждённые, скажи об этом методисту."
    )


async def begin_program(ctx: ToolContext, arguments: BeginProgramArguments) -> BeginProgramResult:
    """Start a run from the methodist's task.

    A chat holds one programme: only a failed run may be replaced, because a run
    that is working, awaiting confirmation or approved would lose its blocks.

    A run with questions does not generate yet. It waits for the methodist's
    answers, and the questions are what the client shows one by one.

    A run without a volume and without questions is refused. A month is not a
    volume, and a programme that starts without one writes a curriculum no
    check can compare it to. The model is told to ask, not to guess.
    """
    if ctx.state is not None and not ctx.state.failed:
        waiting = ctx.state.pending_questions
        if waiting:
            listed = "; ".join(question.text for question in waiting)
            return BeginProgramResult(
                ok=False,
                error="В этом чате уже есть программа",
                note=(
                    f"Программа ждёт ответов методиста на {len(waiting)} вопросов: {listed}. "
                    "Перечисли их методисту и предложи ответить в чате."
                ),
            )
        return BeginProgramResult(
            ok=False,
            error="В этом чате уже есть программа",
            note=(
                "Не начинай новую программу: скажи методисту, что в этом чате уже есть черновик "
                "или идёт генерация, и предложи продолжить в нём."
            ),
        )
    if arguments.requested_hours is None and not arguments.questions:
        return BeginProgramResult(
            ok=False,
            error="не указан объём программы",
            note=(
                "Спроси у методиста объём в академических часах: без него программу не начать. "
                "Срок вроде «месяц» — это не объём. Задай вопрос и дождись ответа."
            ),
        )
    ctx.state = new_state(
        ctx.session_id,
        requested_hours=arguments.requested_hours,
        requirements=arguments.requirements,
        questions=arguments.questions,
        kind=arguments.kind,
        format=arguments.format or ProgramFormat.COURSE,
        expected_test_questions=arguments.expected_test_questions,
        min_practice_share=arguments.min_practice_share,
    )
    ctx.dirty = True
    log.info(
        "program_started",
        chat_session_id=str(ctx.session_id),
        requested_hours=arguments.requested_hours,
        kind=arguments.kind.value if arguments.kind else None,
        format=arguments.format.value if arguments.format else None,
        questions=len(ctx.state.pending_questions),
    )
    if ctx.state.awaiting_answers:
        listed = "; ".join(question.text for question in ctx.state.pending_questions)
        return BeginProgramResult(
            stage=ctx.state.current_stage,
            note=(f"Программа начнёт формироваться после ответов методиста. " f"Задай ему эти вопросы: {listed}"),
        )
    return BeginProgramResult(
        stage=ctx.state.current_stage,
        note="Программа запущена и формируется этапами. Скажи методисту, что генерация началась.",
    )


async def approve_program_draft(
    ctx: ToolContext, _arguments: ApproveProgramDraftArguments
) -> ApproveProgramDraftResult:
    """Confirm the rendered draft; before it is written the methodist saw nothing."""
    state = ctx.state
    if state is None:
        return ApproveProgramDraftResult(ok=False, error="В этом чате нет программы")
    try:
        ctx.state = actions.approve(state, by=ctx.user_id)
    except ProgramError as error:
        return ApproveProgramDraftResult(ok=False, error=str(error), note=error.note)
    ctx.dirty = True
    log.info("program_approved", chat_session_id=str(ctx.session_id))
    return ApproveProgramDraftResult(note="Черновик утверждён. Скажи методисту, что программа принята.")


async def revise_program(ctx: ToolContext, arguments: ReviseProgramArguments) -> ReviseProgramResult:
    """Ask for one part of the programme to be redone.

    The change is not a new run: the blocks that are already committed stay,
    the named stage is run again with the methodist's notes, and the rest of
    the programme is checked against the block that changed.
    """
    state = ctx.state
    if state is None:
        return ReviseProgramResult(ok=False, error="В этом чате нет программы")
    try:
        ctx.state = actions.revise(state, arguments.stage, arguments.notes, by=ctx.user_id)
    except ProgramError as error:
        return ReviseProgramResult(ok=False, error=str(error), note=error.note)
    ctx.dirty = True
    log.info(
        "program_revision_requested",
        chat_session_id=str(ctx.session_id),
        stage=arguments.stage.value,
    )
    return ReviseProgramResult(
        stage=arguments.stage,
        note=(
            f"Блок «{arguments.stage.value}» будет переделан: {arguments.notes}. "
            "Скажи методисту, что правка взята в работу, остальное остаётся как было."
        ),
    )


#: A handler takes the context and its own arguments model, and answers a result.
Handler = Callable[[ToolContext, Any], Awaitable[ToolResult]]


@dataclass(frozen=True)
class AgentTool:
    """One tool as the dispatcher needs it: the spec, its arguments, its handler.

    ``arguments`` is ``None`` for :func:`save_program_draft`, whose payload is the
    block of the stage the run is on rather than one fixed model.
    """

    spec: FunctionDefinition
    arguments: type[BaseModel] | None
    handler: Handler


#: Every tool the model may call, keyed by the name in the specification. What the
#: caller offers is a subset: :data:`INTAKE_TOOLS` or :func:`stage_tools`.
AGENT_TOOL_REGISTRY: dict[str, AgentTool] = {
    SEARCH_SOURCES_SPEC.name: AgentTool(SEARCH_SOURCES_SPEC, SearchSourcesArguments, search_sources),
    BEGIN_PROGRAM_SPEC.name: AgentTool(BEGIN_PROGRAM_SPEC, BeginProgramArguments, begin_program),
    APPROVE_PROGRAM_DRAFT_SPEC.name: AgentTool(
        APPROVE_PROGRAM_DRAFT_SPEC, ApproveProgramDraftArguments, approve_program_draft
    ),
    REVISE_PROGRAM_SPEC.name: AgentTool(REVISE_PROGRAM_SPEC, ReviseProgramArguments, revise_program),
    SAVE_PROGRAM_DRAFT: AgentTool(STAGE_SPECS[Stage.COMPETENCES], None, save_program_draft),
}

#: Tools offered while the methodist is still describing the programme.
INTAKE_TOOLS: list[Tool] = [
    Tool(function=spec) for spec in (BEGIN_PROGRAM_SPEC, APPROVE_PROGRAM_DRAFT_SPEC, REVISE_PROGRAM_SPEC)
]


def stage_tools(stage: Stage) -> list[Tool]:
    """The tools offered at one stage: its own block, and the search where it cites."""
    specs = [STAGE_SPECS[stage]]
    if stage in SEARCHING_STAGES:
        specs.insert(0, SEARCH_SOURCES_SPEC)
    return [Tool(function=spec) for spec in specs]


async def execute_tool(ctx: ToolContext, call: FunctionCall) -> ToolResult:
    """Run one function call: validate its arguments, then hand them to the tool.

    A broken call is not a broken generation: the model may correct itself in the
    next round, so every failure becomes a result rather than a raise.
    """
    tool = AGENT_TOOL_REGISTRY.get(call.name)
    if tool is None:
        log.warning("unknown_tool_call", tool=call.name)
        return ToolResult(ok=False, error=f"Неизвестная функция: {call.name}")
    arguments_model = tool.arguments
    if arguments_model is None:
        if ctx.state is None:
            return ToolResult(ok=False, error="Программа ещё не начата")
        arguments_model = STAGE_BLOCK.get(ctx.state.current_stage)
        if arguments_model is None:
            return ToolResult(ok=False, error="Для этого этапа нет сохраняемого блока")
    try:
        arguments = json.loads(call.arguments)
    except json.JSONDecodeError as error:
        log.warning("tool_arguments_invalid", tool=call.name, exc=error)
        return ToolResult(ok=False, error="Некорректные аргументы вызова")
    try:
        parsed = arguments_model.model_validate(arguments)
    except ValidationError as error:
        log.warning("tool_arguments_invalid", tool=call.name, errors=_describe_validation_error(error))
        return ToolResult(ok=False, error="Некорректные аргументы вызова", details=_describe_validation_error(error))
    except ValidationError as error:
        log.warning("tool_arguments_invalid", tool=call.name, errors=_describe_validation_error(error))
        return ToolResult(ok=False, error="Некорректные аргументы вызова", details=_describe_validation_error(error))
    except Exception as error:
        # A validator that crashes on an unexpected shape is a bug in the
        # schema, not in the model's arguments. It still has to be a tool
        # result, so the stage retries with a message instead of dying with
        # its budget unspent.
        log.error("tool_validator_crashed", tool=call.name, exc=error)
        return ToolResult(
            ok=False,
            error="Внутренняя ошибка проверки аргументов",
            details=[f"{type(error).__name__}: {error}"],
        )
    try:
        result = await tool.handler(ctx, parsed)
    except Exception as error:
        log.error("tool_call_failed", tool=call.name, exc=error)
        return ToolResult(ok=False, error="Внутренняя ошибка инструмента")
    log.info("tool_call", tool=call.name, ok=result.ok)
    return result


def call_executor(ctx: ToolContext, offered: list[Tool]) -> Callable[[ToolCall], Awaitable[ToolResult]]:
    """A call handler that runs only the tools the caller offered.

    The registry holds every tool; this keeps a model that remembers
    ``begin_program`` from starting a second run in the middle of a stage.
    """
    names = {tool.function.name for tool in offered}

    async def call(tool_call: ToolCall) -> ToolResult:
        if tool_call.function.name not in names:
            log.warning("tool_not_offered", tool=tool_call.function.name)
            return ToolResult(ok=False, error=f"Функция {tool_call.function.name} недоступна на этом шаге")
        return await execute_tool(ctx, tool_call.function)

    return call


def _describe_validation_error(error: ValidationError) -> list[str]:
    """Flatten pydantic errors into ``path: message`` lines the model can act on."""
    return [f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()]
