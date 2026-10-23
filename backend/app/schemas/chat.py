from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agentic.models import (
    Competence,
    Curriculum,
    ProgramKind,
    ProgramSummary,
    Source,
    Stage,
    StageStatuses,
)
from app.models.chat import GenerationResultType, UserRole


class ChatAttachment(BaseModel):
    """
    Attachment reference as it appears embedded in a chat message.
    Use the id with the attachment endpoints to fetch its download URL or status.
    """

    id: int
    filename: str = Field(alias="name")

    model_config = {"from_attributes": True}


class ChatMessageSchema(BaseModel):
    id: int
    role: UserRole
    content: str = Field(
        description="Text of the message. For user messages, does not include file descriptions added on the backend"
    )
    attachments: list[ChatAttachment] = Field(description="Only attachments that finished processing are included")
    timestamp: datetime

    model_config = {"from_attributes": True}


class UserChatSchema(BaseModel):
    """Summary of a chat session, for use in a chat list view."""

    session_id: uuid.UUID
    last_message: ChatMessageSchema
    name: str = Field(description="Chat title, may be auto-generated and updated as the conversation progresses")


class UserChatPageSchema(BaseModel):
    """One page of the session list, as returned by `GET /chats/`.

    `items` holds at most `items_per_page` entries; `total` counts every matching
    session, so a client can render page numbers without walking the rest.
    """

    items: list[UserChatSchema]
    total: int = Field(description="Number of matching sessions across all pages")
    page: int = Field(ge=0, description="Zero-based index of this page")
    items_per_page: int = Field(ge=1, description="Page size that was applied")


class ResultSchemaContent(BaseModel):
    """The latest generation outcome for a chat session: a completed assistant message or an error."""

    type: GenerationResultType
    content: str = Field(description="Assistant's reply text, or error message when type is ERROR")
    timestamp: datetime
    attachment_id: int | None = Field(None, description="Set if the assistant generated a file as part of this result")
    update_name: str | None = Field(None, description="If chat updated the name, this will be the new chat name")

    model_config = {"from_attributes": True}


class ClarificationQuestionSchema(BaseModel):
    """One question the methodist has to answer before the programme can start."""

    id: str = Field(description="Идентификатор вопроса; ответ присылается с тем же идентификатором")
    text: str = Field(description="Текст вопроса")

    model_config = ConfigDict(
        json_schema_extra={"example": {"id": "9f1c2b7a4d3e4f5a8b6c1d2e3f4a5b6c", "text": "Для кого программа?"}}
    )


class ResultSchema(BaseModel):
    """
    Response for polling generation status.
    If running is true, result reflects the previous turn, if any, and should be ignored.
    """

    running: bool = Field(description="True if a generation is currently in progress for this user")
    result: ResultSchemaContent | None = Field(None, description="Null if no message has been sent in this chat yet")
    questions: list[ClarificationQuestionSchema] = Field(
        default_factory=list,
        description=(
            "Questions the methodist still has to answer before the programme can start. "
            "Send the answers back with POST /chats/{session_id}, one per question id."
        ),
    )


class ProgramViewStatus(StrEnum):
    """Where a programme stands, as the client sees it.

    A failed run is a status of its own here: the run keeps the status it had
    when it died, and only this projection says the run is over.
    """

    NONE = "none"
    DRAFT = "draft"
    GENERATING = "generating"
    AWAITING_ANSWERS = "awaiting_answers"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    VERIFIED = "verified"
    FAILED = "failed"


class ClarificationAnswerRequest(BaseModel):
    """The methodist's answer to one question."""

    question_id: str = Field(description="Идентификатор вопроса, на который дан ответ")
    text: str = Field(min_length=1, max_length=4000, description="Текст ответа")


class ProgramBlocksSchema(BaseModel):
    """The document as it stands: the blocks that are already saved.

    A programme is assembled block by block, so most of these are empty until
    the stage that fills them has run.
    """

    kind: ProgramKind | None = Field(default=None, description="Тип программы, если он уже определён")
    title: str | None = Field(default=None, description="Название программы, если оно уже определено")
    competencies: list[Competence] = Field(default_factory=list, description="Компетенции с результатами освоения")
    curriculum: Curriculum | None = Field(default=None, description="Учебный план, если он уже сохранён")
    sources: list[Source] = Field(default_factory=list, description="Источники, уже сохранённые в программе")
    summary: ProgramSummary | None = Field(default=None, description="Паспорт программы, если он уже сохранён")


class ProgramCheckSchema(BaseModel):
    """One check of the programme and what it found."""

    name: str = Field(description="Что проверялось")
    ok: bool = Field(description="Проверка пройдена")
    problems: list[str] = Field(default_factory=list, description="Что не сошлось")
    details: dict[str, Any] = Field(default_factory=dict, description="Числа проверки")


class ProgramExportsSchema(BaseModel):
    """The files the programme was exported to.

    They are attachments of the chat, so the client fetches them with the
    attachment route it already uses for a file the methodist uploaded.
    """

    document_attachment_id: int | None = Field(default=None, description="Вложение с документом программы")
    slides_attachment_id: int | None = Field(default=None, description="Вложение со слайдами программы")
    json_attachment_id: int | None = Field(default=None, description="Вложение с данными программы в JSON")


class ProgramRevisionSchema(BaseModel):
    """A change the methodist asked for in one part of the programme."""

    stage: Stage = Field(description="Блок программы, который переделывается")
    notes: str = Field(description="Что именно изменить")
    requested_by: uuid.UUID | None = Field(default=None, description="Кто попросил переделку")
    requested_at: datetime | None = Field(default=None, description="Когда попросили переделку")


class ProgramRevisionRequest(BaseModel):
    """The methodist's request to redo one part of the programme.

    The stage names the block to redo and the notes say what to change in it.
    The rest of the programme stays as it is.
    """

    stage: Stage = Field(description="Блок программы, который нужно переделать")
    notes: str = Field(min_length=1, max_length=2000, description="Что именно изменить")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "stage": "hours",
                "notes": "Замени тему 2.3: добавить разбор кейса из практикума",
            }
        }
    )


class ProgramReportSchema(BaseModel):
    """The checks the programme passed, for the methodist to read."""

    ok: bool = Field(default=True, description="Все проверки пройдены")
    checks: list[ProgramCheckSchema] = Field(default_factory=list, description="Проверки по порядку")
    notes: list[str] = Field(default_factory=list, description="Замечания, не влияющие на итог")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "ok": True,
                "checks": [
                    {
                        "name": "часы",
                        "ok": True,
                        "problems": [],
                        "details": {
                            "заявлено": 72,
                            "всего": 72,
                            "темы": 66,
                            "итоговая аттестация": 6,
                            "аудиторных": 32,
                            "самостоятельной работы": 34,
                        },
                    },
                    {"name": "покрытие", "ok": True, "problems": [], "details": {}},
                ],
            }
        }
    )


class ProgramStateSchema(BaseModel):
    """Where the programme of a chat stands."""

    status: ProgramViewStatus = Field(description="Состояние программы")
    current_stage: Stage | None = Field(default=None, description="Этап, над которым идёт работа")
    stages: StageStatuses = Field(description="Состояние каждого этапа")
    running: bool = Field(
        description=(
            "True while a worker holds the current stage's lock. It is a live hint, not a "
            "completion signal: a stage waiting in the queue is not running yet, and the "
            "finalisation has no lock at all. Poll on `busy` instead."
        )
    )
    busy: bool = Field(
        description=(
            "True while the programme is still being worked on, the finalisation included. "
            "A client polls until it is false, and the status says why the run stopped."
        )
    )
    error: str | None = Field(default=None, description="Причина, по которой программа не собралась")
    updated_at: datetime | None = Field(default=None, description="Когда состояние менялось в последний раз")
    version: int = Field(description="Счётчик изменений состояния")
    questions: list[ClarificationQuestionSchema] = Field(
        default_factory=list, description="Вопросы, на которые программа ждёт ответов"
    )
    draft: ProgramBlocksSchema | None = Field(default=None, description="Документ в том виде, в каком он уже есть")
    report: ProgramReportSchema | None = Field(default=None, description="Отчёт автопроверки программы")
    exports: ProgramExportsSchema | None = Field(default=None, description="Выгрузки программы: документ и слайды")
    revision: ProgramRevisionSchema | None = Field(
        default=None, description="Правка, которую методист попросил внести, пока она не сделана"
    )
    last_revision: ProgramRevisionSchema | None = Field(
        default=None, description="Последняя сделанная правка: кто её просил и когда"
    )
    approved_by: uuid.UUID | None = Field(default=None, description="Кто утвердил черновик программы")
    approved_at: datetime | None = Field(default=None, description="Когда черновик был утверждён")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "generating",
                "current_stage": "hours",
                "stages": {
                    "competences": "completed",
                    "hours": "in_progress",
                    "assessment": "pending",
                    "materials": "pending",
                    "sources": "pending",
                    "summary": "pending",
                },
                "running": True,
                "busy": True,
                "error": None,
                "updated_at": "2026-10-21T12:00:00+00:00",
                "version": 7,
                "questions": [],
                "draft": {
                    "kind": "professional_development",
                    "title": "Анализ данных на Python",
                    "competencies": [],
                    "curriculum": None,
                    "sources": [],
                    "summary": None,
                },
                "exports": {"document_attachment_id": 12, "slides_attachment_id": 13},
                "report": {
                    "ok": False,
                    "checks": [
                        {
                            "name": "часы",
                            "ok": False,
                            "problems": ["учебный план ещё не собран"],
                            "details": {"заявлено": 72, "всего": 0},
                        }
                    ],
                },
            }
        }
    )


class UserProgrammeSchema(BaseModel):
    """One programme of the user, as the catalogue lists it."""

    session_id: uuid.UUID = Field(description="Идентификатор чата с программой")
    title: str = Field(description="Название программы")
    status: ProgramViewStatus = Field(description="Состояние программы")
    updated_at: datetime | None = Field(default=None, description="Когда программа менялась в последний раз")


class UserProgrammePageSchema(BaseModel):
    """One page of the programme catalogue.

    `total` counts every programme across all pages, so a page number past the
    end returns no items but still reports the real total.
    """

    items: list[UserProgrammeSchema] = Field(description="Программы на этой странице")
    total: int = Field(description="Всего программ у пользователя")
    page: int = Field(ge=0, description="Номер страницы, с нуля")
    items_per_page: int = Field(ge=1, description="Сколько программ на странице")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [
                    {
                        "session_id": "3f7c1d2e-5b6a-4c8d-9e0f-1a2b3c4d5e6f",
                        "title": "Анализ данных на Python",
                        "status": "pending_approval",
                        "updated_at": "2026-10-21T12:04:00+00:00",
                    }
                ],
                "total": 1,
                "page": 0,
                "items_per_page": 20,
            }
        }
    )


class ChatCreatedSchema(BaseModel):
    session_id: uuid.UUID


class SendMessageSchema(BaseModel):
    content: str = Field(max_length=5000, description="Текст сообщения")
    answers: list[ClarificationAnswerRequest] = Field(
        default_factory=list,
        max_length=10,
        description=(
            "Ответы на вопросы, заданные программой. Ответ принимается только на тот вопрос, "
            "который программа действительно задала, и на каждый открытый вопрос нужен ровно один ответ."
        ),
    )

    @model_validator(mode="after")
    def _something_to_send(self) -> SendMessageSchema:
        """A turn carries either a message or the answers to the open questions."""
        if not self.content.strip() and not self.answers:
            raise ValueError("сообщение пустое и ответов на вопросы нет")
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content": "",
                "answers": [{"question_id": "9f1c2b7a4d3e4f5a8b6c1d2e3f4a5b6c", "text": "Для руководителей отделов"}],
            }
        }
    )


class ChatDeletedResponse(BaseModel):
    deleted: bool = Field(description="False if the chat session did not exist")
