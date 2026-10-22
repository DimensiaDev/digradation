import {
  afterRenderEffect,
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  effect,
  ElementRef,
  inject,
  input,
  OnInit,
  output,
  signal,
  untracked,
  viewChild
} from '@angular/core';
import {
  LucideArrowUp,
  LucideDownload,
  LucideDynamicIcon,
  LucideEye,
  LucideMic,
  LucidePanelRightClose,
  LucidePanelRightOpen,
  LucidePaperclip,
  LucideTrash2
} from '@lucide/angular';
import type { LucideIconData } from '@lucide/angular';
import { HttpErrorResponse } from '@angular/common/http';
import { EMPTY, catchError, finalize } from 'rxjs';
import { DragNDropComponent } from '../../shared/components/drag-n-drop/drag-n-drop.component';
import { ChatMessageComponent } from './chat-message.component';
import {
  ChatMessage,
  ChatMessageAnswer,
  ChatMessageAttachment,
  ChatMessageStatus
} from './chat-message.model';
import { AgentStatusComponent } from './agent-status.component';
import { FilePreviewComponent } from '../../shared/components/drag-n-drop/file-preview.component';
import { NotificationService } from '../../core/services/notification.service';
import { InitialChatStateService } from '../../core/services/initial-chat-state.service';
import {
  ChatMessageApi,
  ChatSessionAttachment,
  ProgramAnswerApi,
  ProgramQuestionApi
} from '../../core/models/models';
import { ChatService } from '../../core/services/chat.service';
import { AttachmentDownloadService } from '../../core/services/attachment-download.service';
import { SpeechRecognitionService } from '../../core/services/speech-recognition.service';
import { extractApiErrorMessage } from '../../shared/utils/api-error';
import { speechErrorNotice } from '../../shared/utils/speech-error';
import { Router } from '@angular/router';
import { fileTypeStyleFor } from '../../shared/utils/file-types';
import { getFileExtension } from '../../shared/utils/upload-format';
import { DragOverlayComponent } from '../../shared/components/drag-n-drop/drag-overlay.component';
import { ChatQuestionFormComponent } from './chat-question-form.component';

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 5 * 60 * 1000;

@Component({
  selector: 'app-chat',
  imports: [
    LucidePanelRightClose,
    LucidePanelRightOpen,
    LucidePaperclip,
    LucideDownload,
    LucideEye,
    LucideDynamicIcon,
    DragNDropComponent,
    DragOverlayComponent,
    LucideTrash2,
    LucideArrowUp,
    LucideMic,
    ChatMessageComponent,
    AgentStatusComponent,
    FilePreviewComponent,
    ChatQuestionFormComponent
  ],
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '[class.files-open]': 'isFilesOpen()'
  },
  templateUrl: './chat.component.html',
  styleUrl: './chat.component.scss'
})
export class ChatComponent implements OnInit {
  readonly id = input.required<string>();
  readonly questions = input<readonly ProgramQuestionApi[]>([]);
  readonly turnStarted = output<void>();
  readonly turnFinished = output<void>();
  readonly refreshProgram = output<void>();

  private readonly speech = inject(SpeechRecognitionService);

  readonly isFilesOpen = signal<boolean>(false);
  readonly agentStatus = signal<'thinking' | null>(null);
  readonly attachedFiles = signal<File[]>([]);
  readonly isUploading = signal(false);
  readonly isSending = signal(false);
  readonly lastMessageFailed = signal(false);
  readonly previewedFile = signal<File | null>(null);
  /**
   * Files the session is holding that no message references yet, which is what
   * "still uploading" means from the user's side. Fetched from the backend rather
   * than derived from `sessionFiles()`, so a page reload or a second tab sees
   * the same list.
   */
  readonly pendingAttachments = signal<ChatSessionAttachment[]>([]);
  readonly answersSubmitting = signal(false);
  readonly answersSubmitted = signal(false);
  readonly isStoppingSpeech = signal(false);

  readonly isSpeechSupported = this.speech.isSupported;
  readonly isRecording = this.speech.isListening;
  readonly speechLabel = computed(() =>
    this.isRecording() ? 'Остановить голосовой ввод' : 'Начать голосовой ввод'
  );

  readonly sessionFiles = computed(() => {
    const seen = new Set<number>();
    const files: ChatMessageAttachment[] = [];
    for (const message of this.messages()) {
      for (const attachment of message.attachments ?? []) {
        if (attachment.attachmentId === undefined || seen.has(attachment.attachmentId)) {
          continue;
        }
        seen.add(attachment.attachmentId);
        files.push(attachment);
      }
    }
    return files;
  });

  fileIconFor(name: string): LucideIconData {
    return fileTypeStyleFor(getFileExtension(name)).icon;
  }

  fileColorFor(name: string): string {
    return fileTypeStyleFor(getFileExtension(name)).color;
  }

  attachmentStatusLabel(attachment: ChatSessionAttachment): string {
    switch (attachment.status) {
      case 'uploading':
        return 'Загружается';
      case 'processing':
        return 'Обрабатывается';
      case 'error':
        return 'Ошибка';
      case 'completed':
        return 'Готово';
      default:
        // The status key has a TTL, so an uploaded file can outlive it. The
        // durable `ready` flag still says whether the file may be sent.
        return attachment.ready ? 'Готово' : 'Статус неизвестен';
    }
  }

  deletePendingAttachment(attachment: ChatSessionAttachment): void {
    this.chatService
      .deleteAttachment(this.id(), attachment.id)
      .pipe(
        catchError((err: HttpErrorResponse) => {
          this.notifications.error('Не удалось удалить файл: ' + extractApiErrorMessage(err), {
            description: 'Файл остался в чате — попробуйте ещё раз.'
          });
          return EMPTY;
        })
      )
      .subscribe(() => {
        this.pendingAttachments.update((list) => list.filter((item) => item.id !== attachment.id));
        this.notifications.success(`Файл «${attachment.filename}» удалён`, {
          description: 'Файл убран из списка вложений.'
        });
      });
  }

  private loadPendingAttachments(sessionId: string): void {
    this.chatService
      .attachments(sessionId)
      .pipe(
        catchError((err: HttpErrorResponse) => {
          this.notifications.error(
            'Не удалось загрузить список файлов: ' + extractApiErrorMessage(err),
            { description: 'Попробуйте открыть список вложений ещё раз.' }
          );
          return EMPTY;
        })
      )
      .subscribe((attachments) => {
        if (this.id() !== sessionId) {
          return;
        }
        this.pendingAttachments.set(
          attachments.filter((attachment) => attachment.chat_message_id === null)
        );
      });
  }

  openAttachmentPreview(attachment: ChatMessageAttachment) {
    if (attachment.file) {
      this.previewedFile.set(attachment.file);
      return;
    }
    this.fetchServerAttachment(attachment, (file) => this.previewedFile.set(file));
  }

  downloadAttachment(attachment: ChatMessageAttachment) {
    if (attachment.file) {
      this.attachmentDownload.save(attachment.file, attachment.file.name);
      return;
    }
    this.fetchServerAttachment(attachment, (file) => this.attachmentDownload.save(file, file.name));
  }

  private fetchServerAttachment(
    attachment: ChatMessageAttachment,
    onLoaded: (file: File) => void
  ): void {
    const attachmentId = attachment.attachmentId;
    if (!attachmentId) {
      return;
    }
    this.attachmentDownload.fetchFile(this.id(), attachmentId).subscribe({
      next: onLoaded,
      error: (err: unknown) => {
        this.notifications.error(
          err instanceof HttpErrorResponse
            ? 'Не удалось получить файл: ' + extractApiErrorMessage(err)
            : err instanceof Error
              ? err.message
              : 'Не удалось получить файл',
          { description: 'Проверьте соединение и повторите попытку.' }
        );
      }
    });
  }

  private readonly chatMessages = viewChild<ElementRef<HTMLUListElement>>('chatMessages');
  private readonly messageTextarea = viewChild<ElementRef<HTMLTextAreaElement>>('messageTextarea');
  private readonly dragNDrop = viewChild(DragNDropComponent);

  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private pollTimeoutTimer: ReturnType<typeof setTimeout> | null = null;
  private appliedSpeech = '';
  private lastQuestionKey = '';

  private readonly notifications = inject(NotificationService);
  private readonly initialChatState = inject(InitialChatStateService);
  private readonly chatService = inject(ChatService);
  private readonly attachmentDownload = inject(AttachmentDownloadService);
  private readonly router = inject(Router);

  constructor() {
    inject(DestroyRef).onDestroy(() => {
      this.speech.stop();
      this.clearAgentTimers();
    });

    effect(() => {
      const sessionId = this.id();
      if (!sessionId) {
        return;
      }
      const isSessionSwitch = this.currentSessionId !== null;
      this.currentSessionId = sessionId;
      if (isSessionSwitch) {
        this.stopReplyPolling(false);
        this.speech.stop();
        this.messages.set([]);
        this.lastMessageFailed.set(false);
      }
      this.loadHistory(sessionId);
    });

    effect(() => {
      this.applyTranscript(this.speech.transcript());
    });

    effect(() => {
      const key = this.questions()
        .map((question) => question.id)
        .join('|');
      if (key === this.lastQuestionKey) {
        return;
      }
      this.lastQuestionKey = key;
      this.answersSubmitted.set(false);
    });

    afterRenderEffect({
      write: () => {
        const log = this.chatMessages()?.nativeElement;
        if (!log || (this.messages().length === 0 && this.agentStatus() === null)) {
          return;
        }
        const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        log.scrollTo({ top: log.scrollHeight, behavior: prefersReducedMotion ? 'auto' : 'smooth' });
      }
    });

    afterRenderEffect({
      write: () => {
        const textarea = this.messageTextarea()?.nativeElement;
        if (!textarea) {
          return;
        }
        this.messageInputValue();
        textarea.style.height = 'auto';
        textarea.style.height = `${textarea.scrollHeight}px`;
      }
    });
  }

  ngOnInit(): void {
    const initial = this.initialChatState.consume(this.id());

    this.hasPendingInitialMessage = initial !== null;
    if (initial) {
      this.messages.set([
        {
          id: this.nextLocalMessageId--,
          direction: 'outgoing',
          text: initial.text,
          time: this.formatTime(),
          status: 'sent',
          attachments: initial.files.length
            ? initial.files.map((file) => ({ name: file.name, size: file.size, file }))
            : undefined
        }
      ]);
    }
  }

  readonly messages = signal<ChatMessage[]>([]);
  private nextLocalMessageId = -1;
  private currentSessionId: string | null = null;
  private hasPendingInitialMessage = false;

  private loadHistory(sessionId: string): void {
    this.chatService
      .messages(sessionId)
      .pipe(
        catchError((err: HttpErrorResponse) => {
          if (err.status === 403) {
            this.notifications.error('Чат не найден', {
              description: 'Возможно, он был удалён.'
            });
          } else {
            this.notifications.error(
              'Не удалось загрузить историю чата: ' + extractApiErrorMessage(err),
              { description: 'Обновите страницу или попробуйте позже.' }
            );
          }

          return EMPTY;
        })
      )
      .subscribe((apiMessages) => {
        if (this.id() !== sessionId) {
          return;
        }
        this.mergeApiMessages(apiMessages);

        const wasInitialSend = this.hasPendingInitialMessage;
        this.hasPendingInitialMessage = false;

        const last = this.messages()[this.messages().length - 1];
        if (last && last.direction === 'outgoing') {
          if (wasInitialSend) {
            this.startReplyPolling();
          } else {
            this.resumePollingIfAgentRunning(sessionId);
          }
        }
      });
  }

  private resumePollingIfAgentRunning(sessionId: string): void {
    this.chatService
      .result(sessionId)
      .pipe(catchError(() => EMPTY))
      .subscribe((chatResult) => {
        if (chatResult.running && this.id() === sessionId) {
          this.startReplyPolling();
        }
      });
  }

  private mergeApiMessages(apiMessages: ChatMessageApi[]): void {
    if (apiMessages.length === 0) {
      return;
    }

    const mapped = apiMessages.map((message) => this.mapApiMessage(message));
    const pending = [...this.messages().filter((message) => message.id < 0)];
    const merged: ChatMessage[] = [];
    for (const message of mapped) {
      const candidates = pending.filter((local) => local.direction === message.direction);
      if (candidates.length !== 1) {
        merged.push(message);
        continue;
      }
      const pendingIndex = pending.indexOf(candidates[0]);
      merged.push({
        ...message,
        attachments: message.attachments ?? pending[pendingIndex].attachments,
        status: message.status ?? pending[pendingIndex].status,
        answers: message.answers ?? pending[pendingIndex].answers
      });
      pending.splice(pendingIndex, 1);
    }
    this.messages.set([...merged, ...pending]);
  }

  private mapApiMessage(message: ChatMessageApi): ChatMessage {
    return {
      id: message.id,
      direction: message.role === 'assistant' ? 'incoming' : 'outgoing',
      text: message.content,
      time: new Date(message.timestamp).toLocaleTimeString('ru-RU', {
        hour: '2-digit',
        minute: '2-digit'
      }),
      timestamp: message.timestamp,
      attachments: message.attachments.length
        ? message.attachments.map((attachment) => ({
            name: attachment.name,
            attachmentId: attachment.id
          }))
        : undefined
    };
  }

  readonly messageInputValue = signal('');

  handleFilesOpen() {
    this.isFilesOpen.set(!this.isFilesOpen());
    if (this.isFilesOpen()) {
      this.loadPendingAttachments(this.id());
    }
  }

  onUploadingChange(uploading: boolean) {
    this.isUploading.set(uploading);
    if (!uploading) {
      // A finished batch changes what the session is holding.
      this.loadPendingAttachments(this.id());
    }
  }

  onMessageInput(event: Event) {
    const target = event.target as HTMLTextAreaElement;
    this.messageInputValue.set(target.value);
  }

  focusMessageInput(event: MouseEvent): void {
    if ((event.target as HTMLElement).closest('button, input, textarea, a, label')) {
      return;
    }
    this.messageTextarea()?.nativeElement.focus();
  }

  onMessageKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      this.sendMessage();
    }
  }

  onFilesChange(files: File[]) {
    this.attachedFiles.set(files);
  }

  async sendMessage(event?: Event): Promise<void> {
    event?.preventDefault();

    if (this.isUploading()) {
      this.notifications.warning('Файлы ещё не все загрузились — дождитесь завершения');
      return;
    }
    if (this.isSending() || this.isStoppingSpeech() || this.agentStatus() !== null) {
      return;
    }

    if (this.isRecording()) {
      this.isStoppingSpeech.set(true);
      try {
        await this.speech.stopAndWait();
      } finally {
        this.isStoppingSpeech.set(false);
      }
      this.applyTranscript(this.speech.transcript());
    }

    const text = this.messageInputValue().trim();
    if (!text) {
      if (this.attachedFiles().length > 0) {
        this.notifications.error('Добавьте текст к сообщению');
      }
      return;
    }

    const attachments = this.attachedFiles().map((file) => ({
      name: file.name,
      size: file.size,
      file
    }));
    const messageId = this.nextLocalMessageId--;

    this.messages.update((messages) => [
      ...messages,
      {
        id: messageId,
        direction: 'outgoing',
        text,
        time: this.formatTime(),
        status: 'sending',
        attachments: attachments.length > 0 ? attachments : undefined
      }
    ]);
    this.messageInputValue.set('');

    this.isSending.set(true);
    this.chatService
      .send(this.id(), text)
      .pipe(
        finalize(() => this.isSending.set(false)),
        catchError((err: HttpErrorResponse) => {
          this.notifications.error(
            'Не удалось отправить сообщение: ' + extractApiErrorMessage(err),
            { description: 'Текст сохранён в поле ввода — попробуйте ещё раз.' }
          );
          this.messages.update((messages) => messages.filter((m) => m.id !== messageId));
          this.messageInputValue.set(text);
          return EMPTY;
        })
      )
      .subscribe(() => {
        this.setStatusForMessage(messageId, 'sent');
        this.attachedFiles.set([]);
        this.dragNDrop()?.reset();
        this.startReplyPolling();
      });
  }

  sendAnswers(answers: readonly ProgramAnswerApi[]): void {
    if (this.answersSubmitting() || this.questions().length === 0) {
      return;
    }

    const details = this.answerDetails(this.questions(), answers);
    const text = this.formatAnswers(details);
    const messageId = this.nextLocalMessageId--;

    this.messages.update((messages) => [
      ...messages,
      {
        id: messageId,
        direction: 'outgoing',
        text,
        time: this.formatTime(),
        status: 'sending',
        answers: details
      }
    ]);

    this.answersSubmitting.set(true);
    this.chatService
      .send(this.id(), '', answers)
      .pipe(
        finalize(() => this.answersSubmitting.set(false)),
        catchError((err: HttpErrorResponse) => {
          this.notifications.error('Не удалось отправить ответы: ' + extractApiErrorMessage(err), {
            description: 'Ответы сохранены в форме — попробуйте ещё раз.'
          });
          this.messages.update((messages) => messages.filter((m) => m.id !== messageId));
          this.refreshProgram.emit();
          return EMPTY;
        })
      )
      .subscribe(() => {
        this.setStatusForMessage(messageId, 'sent');
        this.answersSubmitted.set(true);
        this.startReplyPolling();
      });
  }

  toggleSpeech(): void {
    if (!this.isSpeechSupported) {
      this.notifications.warning('Голосовой ввод не поддерживается в этом браузере', {
        description: 'Попробуйте Chrome или Safari.'
      });
      return;
    }

    if (this.isRecording()) {
      this.speech.stop();
      return;
    }

    if (this.isSending() || this.isStoppingSpeech() || this.agentStatus() !== null) {
      return;
    }

    this.appliedSpeech = '';
    this.speech.start({
      lang: 'ru-RU',
      onError: (code) => this.reportSpeechError(code)
    });
  }

  private applyTranscript(transcript: { final: string; interim: string }): void {
    const spoken = [transcript.final, transcript.interim]
      .map((part) => part.trim())
      .filter(Boolean)
      .join(' ');
    if (!spoken) {
      return;
    }

    const current = untracked(this.messageInputValue);
    const previous = this.appliedSpeech;
    this.appliedSpeech = spoken;

    if (previous) {
      const index = current.lastIndexOf(previous);
      if (index !== -1) {
        this.messageInputValue.set(
          current.slice(0, index) + spoken + current.slice(index + previous.length)
        );
        return;
      }
    }

    this.messageInputValue.set(current ? `${current.trimEnd()} ${spoken}` : spoken);
  }

  private reportSpeechError(code: string): void {
    const notice = speechErrorNotice(code);
    if (notice.type === 'info') {
      this.notifications.info(notice.message, { description: notice.description });
      return;
    }
    this.notifications.error(notice.message, { description: notice.description });
  }

  private answerDetails(
    questions: readonly ProgramQuestionApi[],
    answers: readonly ProgramAnswerApi[]
  ): readonly ChatMessageAnswer[] {
    const byId = new Map(answers.map((answer) => [answer.question_id, answer.text.trim()]));
    return questions.map((question) => ({
      question: question.text,
      text: byId.get(question.id) ?? ''
    }));
  }

  private formatAnswers(answers: readonly ChatMessageAnswer[]): string {
    return [
      'Ответы на уточняющие вопросы по программе:',
      ...answers.map((answer) => `— ${answer.question}: ${answer.text}`)
    ].join('\n');
  }

  private setStatusForMessage(messageId: number, status: ChatMessageStatus): void {
    this.messages.update((messages) =>
      messages.map((message) => (message.id === messageId ? { ...message, status } : message))
    );
  }

  onRetryRequest(): void {
    this.chatService
      .retry(this.id())
      .pipe(
        catchError((err: HttpErrorResponse) => {
          this.notifications.error(
            'Не удалось отправить повторный запрос: ' + extractApiErrorMessage(err),
            { description: 'Попробуйте ещё раз через некоторое время.' }
          );
          return EMPTY;
        })
      )
      .subscribe(() => {
        this.notifications.success('Повторный запрос отправлен', {
          description: 'Агент обрабатывает запрос заново.'
        });
        this.startReplyPolling();
      });
  }

  private startReplyPolling() {
    this.stopReplyPolling(false);

    this.lastMessageFailed.set(false);
    this.agentStatus.set('thinking');
    this.turnStarted.emit();
    this.pollTimer = setInterval(() => this.checkForResult(), POLL_INTERVAL_MS);
    this.pollTimeoutTimer = setTimeout(() => {
      this.stopReplyPolling();
      this.lastMessageFailed.set(true);
      this.notifications.error('Агент не ответил — попробуйте позже', {
        description: 'Превышено время ожидания ответа.'
      });
    }, POLL_TIMEOUT_MS);
  }

  deleteSession(): void {
    this.chatService
      .remove(this.id())
      .pipe(
        catchError((err: HttpErrorResponse) => {
          const detail = extractApiErrorMessage(err);
          this.notifications.error(
            detail === 'Invalid session'
              ? 'Сессия не найдена или уже удалена'
              : 'Не удалось удалить сессию: ' + detail,
            { description: 'Чат не был удалён — обновите список чатов и попробуйте снова.' }
          );
          return EMPTY;
        })
      )
      .subscribe(() => {
        this.notifications.success('Сессия удалена', {
          description: 'Чат и его сообщения удалены безвозвратно.'
        });
        this.router.navigate(['/create']);
      });
  }

  private checkForResult() {
    const sessionId = this.id();
    this.chatService
      .result(sessionId)
      .pipe(catchError(() => EMPTY))
      .subscribe((chatResult) => {
        if (this.id() !== sessionId) {
          return;
        }
        if (chatResult.running || chatResult.result === null) {
          return;
        }

        this.stopReplyPolling();
        if (chatResult.result.type === 'error') {
          this.lastMessageFailed.set(true);
          this.notifications.error('Агент не смог обработать запрос — попробуйте позже', {
            description: 'На стороне сервиса произошла ошибка.'
          });
          return;
        }

        const reply = chatResult.result;
        this.messages.update((messages) => {
          if (
            messages.some(
              (message) => message.direction === 'incoming' && message.timestamp === reply.timestamp
            )
          ) {
            return messages;
          }
          return [
            ...messages,
            {
              id: this.nextLocalMessageId--,
              direction: 'incoming',
              text: reply.content,
              timestamp: reply.timestamp,
              time: new Date(reply.timestamp).toLocaleTimeString('ru-RU', {
                hour: '2-digit',
                minute: '2-digit'
              })
            }
          ];
        });
        this.notifications.info('Агент ответил на ваше сообщение');
      });
  }

  private stopReplyPolling(notifyFinished = true) {
    const wasActive = this.pollTimer !== null || this.pollTimeoutTimer !== null;

    if (this.pollTimer !== null) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    if (this.pollTimeoutTimer !== null) {
      clearTimeout(this.pollTimeoutTimer);
      this.pollTimeoutTimer = null;
    }
    this.agentStatus.set(null);

    if (wasActive && notifyFinished) {
      this.turnFinished.emit();
    }
  }

  private formatTime() {
    return new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }

  private clearAgentTimers() {
    if (this.pollTimer !== null) {
      clearInterval(this.pollTimer);
    }
    if (this.pollTimeoutTimer !== null) {
      clearTimeout(this.pollTimeoutTimer);
    }
  }

  openFilePicker(): void {
    this.dragNDrop()?.openPicker();
  }

  onFilesDropped(files: File[]): void {
    this.dragNDrop()?.enqueue(files);
  }
}
