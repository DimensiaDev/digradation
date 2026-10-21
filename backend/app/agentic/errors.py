"""Failures of the programme pipeline.

One base class so a caller can handle the whole pipeline at once, and a
``reason`` on every subclass — the sentence the methodist is told when the run
gives up.
"""

from __future__ import annotations


class ProgramError(Exception):
    """Base of every failure the pipeline raises."""

    reason = "не удалось продолжить формирование программы"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.reason)


class ProviderDidNotRespond(ProgramError):
    reason = "провайдер не ответил"


class ToolCallFailed(ProgramError):
    reason = "модель вернула некорректный вызов инструмента"


class AnswerTruncated(ProgramError):
    reason = "ответ модели обрезан по длине"


class ToolLoopExceeded(ProgramError):
    reason = "модель не смогла завершить блок"


class StageNotCompleted(ProgramError):
    reason = "модель не сохранила блок этапа"


class MissingChat(ProgramError):
    reason = "чат удалён"


class StaleState(ProgramError):
    reason = "состояние программы устарело"


class IncompleteDraft(ProgramError):
    """The committed blocks cannot produce a valid draft.

    Retrying the same blocks would produce the same refusal, so the caller ends
    the run instead of spending the stage budget on it.
    """

    reason = "черновик неполон"

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems
