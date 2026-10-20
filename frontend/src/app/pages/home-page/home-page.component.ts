import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  signal,
  untracked,
  viewChild
} from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router } from '@angular/router';
import { LucideArrowUp, LucideMic, LucidePaperclip } from '@lucide/angular';
import { EMPTY, TimeoutError, catchError, finalize } from 'rxjs';
import { AuthModalService } from '../../core/services/auth-modal.service';
import { ChatStarterService } from '../../core/services/chat-starter.service';
import { InitialChatStateService } from '../../core/services/initial-chat-state.service';
import { MAX_FILE_SIZE } from '../../core/models/models';
import { NotificationService } from '../../core/services/notification.service';
import { PendingChatDraftService } from '../../core/services/pending-chat-draft.service';
import { ProfileService } from '../../core/services/profile.service';
import { SpeechRecognitionService } from '../../core/services/speech-recognition.service';
import { BrandLogoAnimatedComponent } from '../../shared/components/brand-logo-animated/brand-logo-animated.component';
import { FilePreviewComponent } from '../../shared/components/drag-n-drop/file-preview.component';
import { FileTileComponent } from '../../shared/components/file-tile/file-tile.component';
import { createId } from '../../shared/utils/create-id';
import { ACCEPTED_EXTENSIONS } from '../../shared/utils/file-types';
import { getFileExtension, formatBytes } from '../../shared/utils/upload-format';

interface PendingFile {
  readonly id: string;
  readonly name: string;
  readonly size: number;
  readonly file: File;
}

@Component({
  selector: 'app-home-page',
  imports: [
    BrandLogoAnimatedComponent,
    FilePreviewComponent,
    FileTileComponent,
    LucideArrowUp,
    LucideMic,
    LucidePaperclip
  ],
  templateUrl: './home-page.component.html',
  styleUrl: './home-page.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class HomePage {
  readonly examples: readonly string[] = [
    'Программа повышения квалификации на 72 часа по анализу данных на Python: модули, практикумы и итоговая аттестация.',
    'Курс профессиональной переподготовки на 256 часов по веб-разработке: учебный план, компетенции и формы контроля.',
    'Рабочая программа дисциплины «Основы машинного обучения» для бакалавриата: цели, темы занятий и литература.',
    'Корпоративная программа по кибербезопасности на 40 часов: практические кейсы и оценка результатов.'
  ];

  readonly acceptAttr = ACCEPTED_EXTENSIONS.join(',');

  readonly message = signal('');
  readonly files = signal<PendingFile[]>([]);
  readonly previewedFile = signal<File | null>(null);
  readonly isComposerActive = signal(false);
  readonly isDragging = signal(false);
  readonly isStarting = signal(false);

  readonly canSend = computed(() => this.message().trim().length > 0 && !this.isStarting());
  readonly hasContent = computed(() => this.message().length > 0 || this.files().length > 0);
  readonly isExpanded = computed(() => this.hasContent() || this.isDragging());

  private readonly profile = inject(ProfileService);
  private readonly authModal = inject(AuthModalService);
  private readonly pendingDraft = inject(PendingChatDraftService);
  private readonly chatStarter = inject(ChatStarterService);
  private readonly initialChatState = inject(InitialChatStateService);
  private readonly notifications = inject(NotificationService);
  private readonly router = inject(Router);
  private readonly speech = inject(SpeechRecognitionService);
  private readonly destroyRef = inject(DestroyRef);

  readonly isSpeechSupported = this.speech.isSupported;
  readonly isRecording = this.speech.isListening;
  readonly speechLabel = computed(() =>
    this.isRecording() ? 'Остановить голосовой ввод' : 'Начать голосовой ввод'
  );

  private readonly fileInput = viewChild<ElementRef<HTMLInputElement>>('fileInput');
  private readonly composerTextarea =
    viewChild<ElementRef<HTMLTextAreaElement>>('composerTextarea');

  private dragDepth = 0;
  private appliedSpeech = '';

  constructor() {
    effect(() => {
      if (!this.profile.user()) {
        return;
      }

      untracked(() => {
        const draft = this.pendingDraft.peek();
        if (draft) {
          this.startChat(draft.text, draft.files);
        }
      });
    });

    effect(() => {
      this.applyTranscript(this.speech.transcript());
    });

    inject(DestroyRef).onDestroy(() => {
      if (this.isRecording()) {
        this.speech.stop();
      }
    });
  }

  onInput(event: Event): void {
    if (this.isStarting()) {
      return;
    }
    this.message.set((event.target as HTMLTextAreaElement).value);
  }

  onFieldClick(event: MouseEvent): void {
    if ((event.target as HTMLElement).closest('button, input, textarea, a, label')) {
      return;
    }
    this.composerTextarea()?.nativeElement.focus();
  }

  onFocus(): void {
    this.isComposerActive.set(true);
  }

  onBlur(): void {
    this.isComposerActive.set(false);
  }

  applyExample(text: string): void {
    if (this.isStarting()) {
      return;
    }
    this.message.set(text);
  }

  openFilePicker(): void {
    if (this.isStarting()) {
      return;
    }
    this.fileInput()?.nativeElement.click();
  }

  onFilesSelected(event: Event): void {
    if (this.isStarting()) {
      return;
    }
    const input = event.target as HTMLInputElement;
    const selected = Array.from(input.files ?? []);
    input.value = '';
    this.addFiles(selected);
  }

  onDragEnter(event: DragEvent): void {
    if (this.isStarting() || !this.hasFiles(event)) {
      return;
    }
    event.preventDefault();
    this.dragDepth++;
    this.isDragging.set(true);
  }

  onDragOver(event: DragEvent): void {
    if (this.isStarting() || !this.hasFiles(event)) {
      return;
    }
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = 'copy';
    }
  }

  onDragLeave(): void {
    this.dragDepth = Math.max(0, this.dragDepth - 1);
    if (this.dragDepth === 0) {
      this.isDragging.set(false);
    }
  }

  onDrop(event: DragEvent): void {
    if (this.isStarting() || !this.hasFiles(event)) {
      return;
    }
    event.preventDefault();
    this.dragDepth = 0;
    this.isDragging.set(false);
    this.addFiles(Array.from(event.dataTransfer?.files ?? []));
  }

  removeFile(id: string): void {
    if (this.isStarting()) {
      return;
    }
    this.files.update((list) => list.filter((file) => file.id !== id));
  }

  openPreview(file: PendingFile): void {
    if (this.isStarting()) {
      return;
    }
    this.previewedFile.set(file.file);
  }

  async onSubmit(event: Event): Promise<void> {
    event.preventDefault();

    if (this.isStarting()) {
      return;
    }

    if (this.isRecording()) {
      // Recognition delivers its final words after `stop()`, so hold the
      // composer (and prevent a second submit) until `onend` has fired.
      this.isStarting.set(true);
      try {
        await this.speech.stopAndWait();
      } finally {
        this.isStarting.set(false);
      }
      // Effects run on a later tick, so fold the settled transcript in now:
      // the submit below must see the words the engine just finalised.
      this.applyTranscript(this.speech.transcript());
    }

    const text = this.message().trim();
    if (!text || this.isStarting()) {
      return;
    }

    const files = this.files().map((item) => item.file);

    if (this.profile.user()) {
      this.startChat(text, files);
      return;
    }

    this.pendingDraft.set({ text, files });
    this.authModal.open('/home');
  }

  toggleSpeech(): void {
    if (this.isStarting()) {
      return;
    }

    if (this.isRecording()) {
      this.speech.stop();
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

    const current = untracked(this.message);
    const previous = this.appliedSpeech;
    this.appliedSpeech = spoken;

    if (previous) {
      const index = current.lastIndexOf(previous);
      if (index !== -1) {
        this.message.set(current.slice(0, index) + spoken + current.slice(index + previous.length));
        return;
      }
    }

    this.message.set(current ? `${current.trimEnd()} ${spoken}` : spoken);
  }

  private reportSpeechError(code: string): void {
    if (code === 'no-speech') {
      this.notifications.info('Речь не распознана', {
        description: 'Попробуйте ещё раз.'
      });
      return;
    }

    const messages: Record<string, string> = {
      'not-allowed': 'Разрешите доступ к микрофону',
      'service-not-allowed': 'Доступ к микрофону запрещён браузером',
      'audio-capture': 'Микрофон не найден',
      network: 'Сервис распознавания речи недоступен',
      'language-not-supported': 'Сервис распознавания не поддерживает русский язык'
    };
    const microphoneBlocked = code === 'not-allowed' || code === 'service-not-allowed';

    let description: string | undefined;
    if (microphoneBlocked) {
      description = 'Разрешите микрофон в настройках браузера и попробуйте снова.';
    } else if (code === 'network') {
      description = 'Проверьте подключение или попробуйте Safari.';
    }

    this.notifications.error(messages[code] ?? 'Не удалось распознать речь', { description });
  }

  private startChat(text: string, files: readonly File[]): void {
    if (this.isStarting()) {
      return;
    }

    this.isStarting.set(true);
    this.chatStarter
      .start(text, files)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.isStarting.set(false)),
        catchError((err: unknown) => {
          if (err instanceof TimeoutError) {
            this.notifications.error('Превышено время ожидания', {
              description:
                'Сервер или хранилище не ответили вовремя. Проверьте соединение и попробуйте ещё раз.'
            });
          } else if (!(err instanceof HttpErrorResponse)) {
            this.notifications.error(
              'Не удалось создать чат: ' + ((err as Error).message || 'Ошибка сервера'),
              {
                description: 'Проверьте соединение и попробуйте ещё раз.'
              }
            );
          }
          return EMPTY;
        })
      )
      .subscribe((sessionId) => {
        this.pendingDraft.clear();
        this.notifications.success('Новый чат создан', {
          description: 'Агент уже получил ваше сообщение.'
        });
        this.initialChatState.set(sessionId, { text, files: [...files] });
        this.router.navigate(['/chats', sessionId]);
      });
  }

  private hasFiles(event: DragEvent): boolean {
    return Array.from(event.dataTransfer?.types ?? []).includes('Files');
  }

  private addFiles(selected: File[]): void {
    if (selected.length === 0) {
      return;
    }

    const additions: PendingFile[] = [];
    for (const file of selected) {
      if (!ACCEPTED_EXTENSIONS.includes(getFileExtension(file.name))) {
        this.notifications.error(`Файл «${file.name}» не поддерживается`, {
          description: `Допустимые форматы: ${ACCEPTED_EXTENSIONS.join(', ')}.`
        });
        continue;
      }

      if (file.size > MAX_FILE_SIZE) {
        this.notifications.error(`Файл «${file.name}» слишком большой`, {
          description: `Максимальный размер вложения — ${formatBytes(MAX_FILE_SIZE)}.`
        });
        continue;
      }

      const duplicate = [...this.files(), ...additions].some((item) => item.file === file);
      if (duplicate) {
        continue;
      }

      const id = createId();
      additions.push({ id, name: file.name, size: file.size, file });
    }

    if (additions.length > 0) {
      this.files.update((list) => [...list, ...additions]);
    }
  }
}
