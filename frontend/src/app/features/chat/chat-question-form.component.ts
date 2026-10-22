import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  input,
  output,
  signal
} from '@angular/core';
import {
  LucideCheck,
  LucideChevronLeft,
  LucideChevronRight,
  LucideMessageCircleQuestion
} from '@lucide/angular';
import { MAX_ANSWER_LENGTH, ProgramAnswerApi, ProgramQuestionApi } from '../../core/models/models';
import { InputComponent } from '../../shared/components/input/input.component';

@Component({
  selector: 'app-chat-question-form',
  imports: [
    InputComponent,
    LucideCheck,
    LucideChevronLeft,
    LucideChevronRight,
    LucideMessageCircleQuestion
  ],
  templateUrl: './chat-question-form.component.html',
  styleUrl: './chat-question-form.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ChatQuestionFormComponent {
  readonly questions = input.required<readonly ProgramQuestionApi[]>();
  readonly submitting = input(false);
  readonly answersSubmitted = output<readonly ProgramAnswerApi[]>();

  readonly maxLength = MAX_ANSWER_LENGTH;
  readonly currentIndex = signal(0);
  readonly answers = signal<Readonly<Record<string, string>>>({});

  readonly currentQuestion = computed(() => this.questions()[this.currentIndex()] ?? null);
  readonly currentAnswer = computed(() => {
    const question = this.currentQuestion();
    return question ? (this.answers()[question.id] ?? '') : '';
  });
  readonly isFirst = computed(() => this.currentIndex() === 0);
  readonly isLast = computed(() => this.currentIndex() >= this.questions().length - 1);
  readonly canProceed = computed(() => this.currentAnswer().trim().length > 0);

  private lastQuestionKey = '';

  constructor() {
    effect(() => {
      const key = this.questions()
        .map((question) => question.id)
        .join('|');

      if (key === this.lastQuestionKey) {
        return;
      }
      this.lastQuestionKey = key;
      this.currentIndex.set(0);
      this.answers.set({});
    });
  }

  answerFor(question: ProgramQuestionApi): string {
    return this.answers()[question.id]?.trim() ?? '';
  }

  setAnswer(value: string): void {
    const question = this.currentQuestion();
    if (!question) {
      return;
    }
    this.answers.update((answers) => ({ ...answers, [question.id]: value }));
  }

  previous(): void {
    if (this.isFirst() || this.submitting()) {
      return;
    }
    this.currentIndex.update((index) => index - 1);
  }

  next(): void {
    if (this.isLast() || !this.canProceed() || this.submitting()) {
      return;
    }
    this.currentIndex.update((index) => index + 1);
  }

  submit(): void {
    if (!this.isLast() || !this.canProceed() || this.submitting()) {
      return;
    }

    const answers = this.questions().map((question) => ({
      question_id: question.id,
      text: this.answerFor(question)
    }));

    if (answers.some((answer) => !answer.text)) {
      return;
    }

    this.answersSubmitted.emit(answers);
  }

  onSubmit(event: Event): void {
    event.preventDefault();
    this.submit();
  }

  onKeydown(event: KeyboardEvent): void {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing) {
      return;
    }

    event.preventDefault();
    if (this.isLast()) {
      this.submit();
    } else {
      this.next();
    }
  }
}
