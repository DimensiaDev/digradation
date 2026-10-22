import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  viewChild
} from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  LucideBookOpen,
  LucideCheck,
  LucideClipboardCheck,
  LucideClock,
  LucideDownload,
  LucideDynamicIcon,
  LucideEye,
  LucideFileText,
  LucideLaptop,
  LucideLayers,
  LucideLayoutTemplate,
  LucidePaperclip,
  LucidePresentation,
  LucideRefreshCw,
  LucideTags,
  LucideTarget,
  LucideTrash2,
  LucideTriangleAlert
} from '@lucide/angular';
import type { LucideIconData } from '@lucide/angular';
import { ChatSessionAttachment, ProgramStateApi } from '../../core/models/models';
import { AttachmentDownloadService } from '../../core/services/attachment-download.service';
import { NotificationService } from '../../core/services/notification.service';
import { AccordionSectionComponent } from '../../shared/components/accordion-section/accordion-section.component';
import { extractApiErrorMessage } from '../../shared/utils/api-error';
import { fileTypeStyleFor } from '../../shared/utils/file-types';
import { getFileExtension } from '../../shared/utils/upload-format';
import { ChatMessageAttachment } from './chat-message.model';
import { ProgramReportComponent } from './program-report.component';
import {
  ProgramPreviewSection,
  programBadge,
  programCompetencies,
  programFormLabel,
  programGoal,
  programHours,
  programHoursLabel,
  programModules,
  programReport,
  programSources,
  programTopicCountLabel
} from './program-view';

const FOCUS_HIGHLIGHT_MS = 1_400;

export type ProgramAccordionSection = Exclude<ProgramPreviewSection, 'results'>;

@Component({
  selector: 'app-program-preview',
  imports: [
    AccordionSectionComponent,
    ProgramReportComponent,
    LucideBookOpen,
    LucideCheck,
    LucideClipboardCheck,
    LucideClock,
    LucideDownload,
    LucideDynamicIcon,
    LucideEye,
    LucideFileText,
    LucideLaptop,
    LucideLayers,
    LucideLayoutTemplate,
    LucidePaperclip,
    LucidePresentation,
    LucideRefreshCw,
    LucideTags,
    LucideTarget,
    LucideTrash2,
    LucideTriangleAlert
  ],
  templateUrl: './program-preview.component.html',
  styleUrl: './program-preview.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ProgramPreviewComponent {
  readonly sessionId = input.required<string>();
  readonly state = input<ProgramStateApi | null>(null);
  readonly loading = input(false);
  readonly error = input<string | null>(null);
  readonly sessionFiles = input<readonly ChatMessageAttachment[]>([]);
  readonly pendingFiles = input<readonly ChatSessionAttachment[]>([]);
  readonly reload = output<void>();
  readonly filesOpened = output<void>();
  readonly previewRequested = output<ChatMessageAttachment>();
  readonly downloadRequested = output<ChatMessageAttachment>();
  readonly deletePendingRequested = output<ChatSessionAttachment>();
  readonly deleteSessionRequested = output<void>();

  readonly badge = computed(() => programBadge(this.state()));
  readonly title = computed(() => this.state()?.draft.title?.trim() || null);
  readonly hours = computed(() => programHours(this.state()?.draft.curriculum));
  readonly hoursLabel = computed(() => programHoursLabel(this.hours()));
  readonly formLabel = computed(() => programFormLabel(this.state()));
  readonly goal = computed(() => programGoal(this.state()));
  readonly competencies = computed(() => programCompetencies(this.state()));
  readonly modules = computed(() => programModules(this.state()?.draft.curriculum));
  readonly sources = computed(() => programSources(this.state()));
  readonly report = computed(() => programReport(this.state()));
  readonly documentExportId = computed(() => this.state()?.exports?.document_attachment_id ?? null);
  readonly slidesExportId = computed(() => this.state()?.exports?.slides_attachment_id ?? null);
  readonly hasContent = computed(
    () =>
      Boolean(this.title()) ||
      Boolean(this.goal()) ||
      this.competencies().length > 0 ||
      this.modules().length > 0 ||
      this.sources().length > 0 ||
      Boolean(this.report()) ||
      this.documentExportId() !== null ||
      this.slidesExportId() !== null ||
      Boolean(this.state()?.draft.summary)
  );
  readonly justUpdated = signal(false);
  readonly focused = signal<ProgramPreviewSection | null>(null);
  readonly expandedSection = signal<ProgramAccordionSection | null>('summary');
  readonly downloading = signal<'document' | 'slides' | null>(null);

  private readonly scroll = viewChild<ElementRef<HTMLElement>>('scroll');
  private readonly attachmentDownload = inject(AttachmentDownloadService);
  private readonly notifications = inject(NotificationService);
  private readonly destroyRef = inject(DestroyRef);
  private lastVersion: number | null = null;
  private updateTimer: ReturnType<typeof setTimeout> | null = null;
  private focusTimer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    effect(() => {
      const version = this.state()?.version ?? null;

      if (version === null) {
        this.lastVersion = null;
        return;
      }

      if (this.lastVersion !== null && version !== this.lastVersion) {
        this.markUpdated();
      }
      this.lastVersion = version;
    });

    this.destroyRef.onDestroy(() => {
      this.clearUpdateTimer();
      this.clearFocusTimer();
    });
  }

  formatHours(hours: number): string {
    return programHoursLabel(hours);
  }

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

  previewFile(attachment: ChatMessageAttachment): void {
    this.previewRequested.emit(attachment);
  }

  downloadFile(attachment: ChatMessageAttachment): void {
    this.downloadRequested.emit(attachment);
  }

  deletePendingFile(attachment: ChatSessionAttachment): void {
    this.deletePendingRequested.emit(attachment);
  }

  deleteSession(): void {
    this.deleteSessionRequested.emit();
  }

  topicCountLabel(count: number): string {
    return programTopicCountLabel(count);
  }

  downloadExport(kind: 'document' | 'slides'): void {
    const attachmentId = kind === 'document' ? this.documentExportId() : this.slidesExportId();
    if (attachmentId === null || this.downloading() !== null) {
      return;
    }

    this.downloading.set(kind);
    this.attachmentDownload
      .fetchFile(this.sessionId(), attachmentId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (file) => {
          this.attachmentDownload.save(file, file.name);
          this.downloading.set(null);
        },
        error: (error: unknown) => {
          this.downloading.set(null);
          this.notifications.error(
            error instanceof HttpErrorResponse
              ? `Не удалось скачать файл: ${extractApiErrorMessage(error)}`
              : error instanceof Error
                ? error.message
                : 'Не удалось скачать файл',
            { description: 'Проверьте соединение и повторите попытку.' }
          );
        }
      });
  }

  isExpanded(section: ProgramAccordionSection): boolean {
    return this.expandedSection() === section;
  }

  toggleSection(section: ProgramAccordionSection): void {
    const opening = this.expandedSection() !== section;
    this.expandedSection.set(opening ? section : null);
    if (section === 'files' && opening) {
      this.filesOpened.emit();
    }
  }

  focusSection(section: ProgramPreviewSection): void {
    if (section !== 'results') {
      this.expandedSection.set(section);
    }

    const target = this.scroll()?.nativeElement.querySelector<HTMLElement>(
      `[data-section="${section}"]`
    );

    this.focused.set(section);
    this.clearFocusTimer();
    this.focusTimer = setTimeout(() => this.focused.set(null), FOCUS_HIGHLIGHT_MS);

    if (typeof target?.scrollIntoView === 'function') {
      const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      target.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
    }
  }

  private markUpdated(): void {
    this.justUpdated.set(true);
    this.clearUpdateTimer();
    this.updateTimer = setTimeout(() => this.justUpdated.set(false), FOCUS_HIGHLIGHT_MS);
  }

  private clearUpdateTimer(): void {
    if (this.updateTimer !== null) {
      clearTimeout(this.updateTimer);
      this.updateTimer = null;
    }
  }

  private clearFocusTimer(): void {
    if (this.focusTimer !== null) {
      clearTimeout(this.focusTimer);
      this.focusTimer = null;
    }
  }
}
