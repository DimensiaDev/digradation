"""Failures of the programme pipeline.

One base class so a caller can handle the whole pipeline at once, and a
``reason`` on every subclass — the sentence the methodist is told when the run
gives up.
"""

from __future__ import annotations


class ProgramError(Exception):
    """Base of every failure the pipeline raises."""

    reason = "не удалось продолжить формирование программы"
    #: What the model is told to do next, when there is something to do. The
    #: HTTP endpoints answer with the reason alone, because the client shows
    #: the reason and acts on it.
    note: str | None = None

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


class StageSuperseded(ProgramError):
    """Another worker owns the stage now; this one must stop without retrying."""

    reason = "этап перехвачен другим воркером"


class AnswersDoNotMatch(ProgramError):
    """The answers do not match the questions the run is waiting for."""

    reason = "ответы не совпадают с заданными вопросами"


class IncompleteDraft(ProgramError):
    """The committed blocks cannot produce a valid draft.

    Retrying the same blocks would produce the same refusal, so the caller ends
    the run instead of spending the stage budget on it.
    """

    reason = "черновик неполон"

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class AlreadyApproved(ProgramError):
    """The programme is complete, so there is nothing left to approve."""

    reason = "Программа уже утверждена"
    note = "Ничего делать не нужно."


class DraftNotReady(ProgramError):
    """The draft was never shown, so there is nothing to confirm."""

    reason = "Черновик ещё не готов к подтверждению"
    note = "Дождись, когда черновик будет показан методисту, и только потом подтверждай."


class DraftIncomplete(ProgramError):
    """The skeleton has a hole in it, so there is nothing whole to approve."""

    reason = "Черновик ещё не собран"
    note = "Дождись, когда все блоки черновика будут собраны, и только потом подтверждай."


class ProgrammeFailed(ProgramError):
    """The run died, so nothing can be done to it any more."""

    reason = "Программа не собрана"


class WaitingForAnswers(ProgramError):
    """The run is on hold until the methodist answers the open questions."""

    reason = "Программа ждёт ответов методиста"
    note = "Сначала ответь на вопросы программы, потом проси переделку."


class RevisionPending(ProgramError):
    """A change the methodist asked for is still being carried out."""

    reason = "Правка уже взята в работу"
    note = "Дождись, пока текущая правка будет сделана, и проси следующую."


class StageBusy(ProgramError):
    """A worker is writing a stage right now."""

    reason = "Программа сейчас пишется"
    note = "Подожди, пока этап будет дописан, и проси переделку после этого."


class StageNotReady(ProgramError):
    """The named stage was never written, or what it is written from is not."""

    reason = "Этот блок программы ещё не написан"
    note = "Проси переделку того блока, который уже есть в программе."


class SkeletonNotApproved(ProgramError):
    """The content is written from an approved skeleton and from nothing else."""

    reason = "Черновик программы ещё не утверждён"
    note = "Сначала утверди черновик, потом проси переделку содержания."
