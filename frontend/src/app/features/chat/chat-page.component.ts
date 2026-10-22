import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  signal,
  viewChild
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { LucideArrowLeft, LucideLayoutTemplate, LucideMessageSquareText } from '@lucide/angular';
import { HttpErrorResponse } from '@angular/common/http';
import { ChatService } from '../../core/services/chat.service';
import { ProgramStateApi } from '../../core/models/models';
import { extractApiErrorMessage } from '../../shared/utils/api-error';
import { ChatComponent } from './chat.component';
import { ProgramPreviewComponent } from './program-preview.component';
import { ProgramStepperComponent } from './program-stepper.component';
import { ProgramMilestone, ProgramPreviewSection, programMilestones } from './program-view';

const PROGRAM_POLL_INTERVAL_MS = 2_000;

type MobileView = 'chat' | 'preview';

@Component({
  selector: 'app-chat-page',
  imports: [
    ChatComponent,
    ProgramPreviewComponent,
    ProgramStepperComponent,
    RouterLink,
    LucideArrowLeft,
    LucideLayoutTemplate,
    LucideMessageSquareText
  ],
  templateUrl: './chat-page.component.html',
  styleUrl: './chat-page.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ChatPage {
  readonly id = input.required<string>();

  readonly program = signal<ProgramStateApi | null>(null);
  readonly programLoading = signal(true);
  readonly programError = signal<string | null>(null);
  readonly turnInFlight = signal(false);
  readonly mobileView = signal<MobileView>('chat');

  readonly questions = computed(() =>
    this.turnInFlight() ? [] : (this.program()?.questions ?? [])
  );
  readonly milestones = computed<readonly ProgramMilestone[]>(() =>
    programMilestones(this.program())
  );

  private readonly chatService = inject(ChatService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly preview = viewChild(ProgramPreviewComponent);

  private currentSessionId: string | null = null;
  private programPollTimer: ReturnType<typeof setTimeout> | null = null;
  private focusTimer: ReturnType<typeof setTimeout> | null = null;
  private programRequestId = 0;

  constructor() {
    effect(() => {
      const sessionId = this.id();

      if (sessionId === this.currentSessionId) {
        return;
      }

      this.currentSessionId = sessionId;
      this.stopProgramPolling();
      this.program.set(null);
      this.programLoading.set(true);
      this.programError.set(null);
      this.turnInFlight.set(false);
      this.mobileView.set('chat');
      this.fetchProgram(sessionId);
    });

    this.destroyRef.onDestroy(() => {
      this.stopProgramPolling();
      this.clearFocusTimer();
    });
  }

  onTurnStarted(): void {
    this.turnInFlight.set(true);
    this.fetchProgram(this.id());
  }

  onTurnFinished(): void {
    this.turnInFlight.set(false);
    this.fetchProgram(this.id());
  }

  refreshProgram(): void {
    this.programLoading.set(true);
    this.programError.set(null);
    this.fetchProgram(this.id());
  }

  onPreviewFocus(section: ProgramPreviewSection): void {
    this.mobileView.set('preview');
    this.clearFocusTimer();
    this.focusTimer = setTimeout(() => {
      this.focusTimer = null;
      this.preview()?.focusSection(section);
    }, 0);
  }

  showChat(): void {
    this.mobileView.set('chat');
  }

  showPreview(): void {
    this.mobileView.set('preview');
  }

  private fetchProgram(sessionId: string): void {
    this.clearProgramPollTimer();
    const requestId = ++this.programRequestId;

    this.chatService.program(sessionId).subscribe({
      next: (program) => {
        if (requestId !== this.programRequestId || this.id() !== sessionId) {
          return;
        }

        this.program.set(program);
        this.programLoading.set(false);
        this.programError.set(null);

        if (this.shouldPoll(program)) {
          this.scheduleProgramPoll(sessionId);
        }
      },
      error: (error: unknown) => {
        if (requestId !== this.programRequestId || this.id() !== sessionId) {
          return;
        }

        this.programLoading.set(false);
        this.programError.set(
          error instanceof HttpErrorResponse
            ? `Не удалось загрузить каркас: ${extractApiErrorMessage(error)}`
            : 'Не удалось загрузить каркас программы'
        );
      }
    });
  }

  private shouldPoll(program: ProgramStateApi): boolean {
    if (this.turnInFlight()) {
      return true;
    }
    if (program.status === 'awaiting_answers') {
      return false;
    }
    return program.busy;
  }

  private scheduleProgramPoll(sessionId: string): void {
    if (this.programPollTimer !== null) {
      return;
    }

    this.programPollTimer = setTimeout(() => {
      this.programPollTimer = null;
      if (this.id() === sessionId) {
        this.fetchProgram(sessionId);
      }
    }, PROGRAM_POLL_INTERVAL_MS);
  }

  private stopProgramPolling(): void {
    this.clearProgramPollTimer();
    this.programRequestId++;
  }

  private clearProgramPollTimer(): void {
    if (this.programPollTimer !== null) {
      clearTimeout(this.programPollTimer);
      this.programPollTimer = null;
    }
  }

  private clearFocusTimer(): void {
    if (this.focusTimer !== null) {
      clearTimeout(this.focusTimer);
      this.focusTimer = null;
    }
  }
}
