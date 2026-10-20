import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  output,
  signal
} from '@angular/core';
import { LucideDynamicIcon, LucideX } from '@lucide/angular';
import { fileTypeStyleFor, previewKindFor } from '../../utils/file-types';
import { getFileExtension } from '../../utils/upload-format';

@Component({
  selector: 'app-file-tile',
  imports: [LucideDynamicIcon, LucideX],
  templateUrl: './file-tile.component.html',
  styleUrl: './file-tile.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class FileTileComponent {
  readonly file = input.required<File>();
  readonly preview = output<void>();
  readonly remove = output<void>();

  readonly name = computed(() => this.file().name);
  readonly extension = computed(() => getFileExtension(this.file().name));
  readonly icon = computed(() => fileTypeStyleFor(this.extension()).icon);
  readonly color = computed(() => fileTypeStyleFor(this.extension()).color);
  readonly previewable = computed(() => previewKindFor(this.extension()) !== null);
  readonly thumbnail = signal<string | null>(null);

  private thumbnailUrl: string | null = null;

  constructor() {
    effect(() => {
      const file = this.file();
      this.releaseThumbnail();

      if (previewKindFor(getFileExtension(file.name)) !== 'image') {
        return;
      }

      this.thumbnailUrl = this.createThumbnail(file);
      this.thumbnail.set(this.thumbnailUrl);
    });

    inject(DestroyRef).onDestroy(() => this.releaseThumbnail());
  }

  private createThumbnail(file: File): string | null {
    if (typeof URL.createObjectURL !== 'function') {
      return null;
    }
    return URL.createObjectURL(file);
  }

  private releaseThumbnail(): void {
    if (this.thumbnailUrl && typeof URL.revokeObjectURL === 'function') {
      URL.revokeObjectURL(this.thumbnailUrl);
    }

    this.thumbnailUrl = null;
    this.thumbnail.set(null);
  }
}
