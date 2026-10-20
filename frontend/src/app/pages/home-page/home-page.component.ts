import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  signal,
  viewChild
} from '@angular/core';
import type { LucideIconData } from '@lucide/angular';
import { LucideArrowUp, LucideDynamicIcon, LucidePaperclip, LucideX } from '@lucide/angular';
import { BrandLogoAnimatedComponent } from '../../shared/components/brand-logo-animated/brand-logo-animated.component';
import { createId } from '../../shared/utils/create-id';
import { ACCEPTED_EXTENSIONS, fileTypeStyleFor } from '../../shared/utils/file-types';
import { formatBytes, getFileExtension } from '../../shared/utils/upload-format';

interface PendingFile {
  readonly id: string;
  readonly name: string;
  readonly size: number;
  readonly extension: string;
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

  readonly canSend = computed(() => this.message().trim().length > 0);
  readonly isExpanded = computed(
    () => this.message().length > 0 || this.files().length > 0 || this.isDragging()
  );

  private readonly fileInput = viewChild<ElementRef<HTMLInputElement>>('fileInput');

  private dragDepth = 0;

  onInput(event: Event): void {
    this.message.set((event.target as HTMLTextAreaElement).value);
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
          extension: getFileExtension(file.name)
        });
      }
      return next;
    });
  }
}
