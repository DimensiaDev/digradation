import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  signal,
  viewChild
} from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { Router } from '@angular/router';
import type { LucideIconData } from '@lucide/angular';
import { LucideArrowUp, LucideDynamicIcon, LucidePaperclip, LucideX } from '@lucide/angular';
import { EMPTY, catchError, finalize } from 'rxjs';
import { AuthModalService } from '../../core/services/auth-modal.service';
import { ChatStarterService } from '../../core/services/chat-starter.service';
import { InitialChatStateService } from '../../core/services/initial-chat-state.service';
import { NotificationService } from '../../core/services/notification.service';
import { PendingChatDraftService } from '../../core/services/pending-chat-draft.service';
import { ProfileService } from '../../core/services/profile.service';
import { BrandLogoAnimatedComponent } from '../../shared/components/brand-logo-animated/brand-logo-animated.component';
import { createId } from '../../shared/utils/create-id';
import { ACCEPTED_EXTENSIONS, fileTypeStyleFor } from '../../shared/utils/file-types';
import { formatBytes, getFileExtension } from '../../shared/utils/upload-format';

interface PendingFile {
  readonly id: string;
  readonly name: string;
  readonly size: number;
  readonly extension: string;
  readonly file: File;
}

@Component({
  selector: 'app-home-page',
  imports: [BrandLogoAnimatedComponent, LucideArrowUp, LucideDynamicIcon, LucidePaperclip, LucideX],
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

  private readonly fileInput = viewChild<ElementRef<HTMLInputElement>>('fileInput');
  private readonly composerTextarea =
    viewChild<ElementRef<HTMLTextAreaElement>>('composerTextarea');

  private dragDepth = 0;

  constructor() {
    effect(() => {
      if (!this.profile.user()) {
        return;
      }

      const draft = this.pendingDraft.consume();
      if (draft) {
        this.startChat(draft.text, draft.files);
      }
    });
  }

  onInput(event: Event): void {
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
    this.message.set(text);
  }

  openFilePicker(): void {
    this.fileInput()?.nativeElement.click();
  }

  onFilesSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const selected = Array.from(input.files ?? []);
    input.value = '';
    this.addFiles(selected);
  }

  onDragEnter(event: DragEvent): void {
    if (!this.hasFiles(event)) {
      return;
    }
    event.preventDefault();
    this.dragDepth++;
    this.isDragging.set(true);
  }

  onDragOver(event: DragEvent): void {
    if (!this.hasFiles(event)) {
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
    event.preventDefault();
    this.dragDepth = 0;
    this.isDragging.set(false);
    this.addFiles(Array.from(event.dataTransfer?.files ?? []));
  }

  removeFile(id: string): void {
    this.files.update((list) => list.filter((file) => file.id !== id));
  }

  onSubmit(event: Event): void {
    event.preventDefault();

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

  private startChat(text: string, files: readonly File[]): void {
    if (this.isStarting()) {
      return;
    }

    this.isStarting.set(true);
    this.chatStarter
      .start(text, files)
      .pipe(
        finalize(() => this.isStarting.set(false)),
        catchError((err: HttpErrorResponse | Error) => {
          if (!(err instanceof HttpErrorResponse)) {
            this.notifications.error(
              'Не удалось создать чат: ' + (err.message || 'Ошибка сервера'),
              {
                description: 'Проверьте соединение и попробуйте ещё раз.'
              }
            );
          }
          return EMPTY;
        })
      )
      .subscribe((sessionId) => {
        this.notifications.success('Новый чат создан', {
          description: 'Агент уже получил ваше сообщение.'
        });
        this.initialChatState.set(sessionId, { text, files: [...files] });
        this.router.navigate(['/chats', sessionId]);
      });
  }

  colorFor(file: PendingFile): string {
    return fileTypeStyleFor(file.extension).color;
  }

  iconFor(file: PendingFile): LucideIconData {
    return fileTypeStyleFor(file.extension).icon;
  }

  sizeLabel(file: PendingFile): string {
    return formatBytes(file.size);
  }

  private hasFiles(event: DragEvent): boolean {
    return Array.from(event.dataTransfer?.types ?? []).includes('Files');
  }

  private addFiles(selected: File[]): void {
    if (selected.length === 0) {
      return;
    }

    this.files.update((list) => {
      const next = [...list];
      for (const file of selected) {
        const duplicate = next.some((item) => item.name === file.name && item.size === file.size);
        if (duplicate) {
          continue;
        }
        next.push({
          id: createId(),
          name: file.name,
          size: file.size,
          extension: getFileExtension(file.name),
          file
        });
      }
      return next;
    });
  }
}
