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
import { ChatSessionAttachment, ProgramStateApi } from '../../core/models/models';
import { FilePreviewComponent } from '../../shared/components/drag-n-drop/file-preview.component';
import { extractApiErrorMessage } from '../../shared/utils/api-error';
import { ChatMessageAttachment } from './chat-message.model';
import { ChatComponent } from './chat.component';
import { ProgramPreviewComponent } from './program-preview.component';
import { ProgramStepperComponent } from './program-stepper.component';

const PROGRAM_POLL_INTERVAL_MS = 2_000;

type MobileView = 'chat' | 'preview';

@Component({
  selector: 'app-chat-page',
  imports: [
    ChatComponent,
    ProgramPreviewComponent,
    ProgramStepperComponent,
    FilePreviewComponent,
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
  readonly previewedFile = signal<File | null>(null);

  readonly questions = computed(() =>
    this.turnInFlight() ? [] : (this.program()?.questions ?? [])
  );

  readonly exportAttachmentIds = computed(() => {
    const exports = this.program()?.exports;
    return [exports?.document_attachment_id, exports?.slides_attachment_id].filter(
      (id): id is number => typeof id === 'number'
    );
  });

  readonly sessionFiles = computed(() => this.chatView()?.sessionFiles() ?? []);
  readonly pendingFiles = computed(() => this.chatView()?.visiblePendingAttachments() ?? []);

  private readonly chatView = viewChild(ChatComponent);
  private readonly chatService = inject(ChatService);
  private readonly destroyRef = inject(DestroyRef);

  private currentSessionId: string | null = null;
  private programPollTimer: ReturnType<typeof setTimeout> | null = null;
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

  showChat(): void {
    this.mobileView.set('chat');
  }

  showPreview(): void {
    this.mobileView.set('preview');
  }

  loadFiles(): void {
    this.chatView()?.loadPendingFiles();
  }

  previewFile(file: ChatMessageAttachment): void {
    this.chatView()?.openAttachmentPreview(file);
  }

  downloadFile(file: ChatMessageAttachment): void {
    this.chatView()?.downloadAttachment(file);
  }

  deletePendingFile(file: ChatSessionAttachment): void {
    this.chatView()?.deletePendingAttachment(file);
  }

  deleteSession(): void {
    this.chatView()?.deleteSession();
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
}
