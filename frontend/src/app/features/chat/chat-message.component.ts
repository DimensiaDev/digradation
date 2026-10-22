import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import {
  LucideBot,
  LucideCheck,
  LucideClock,
  LucideCopy,
  LucideDownload,
  LucideDynamicIcon,
  LucideEye,
  LucideRefreshCw
} from '@lucide/angular';
import type { LucideIconData } from '@lucide/angular';
import { fileTypeStyleFor } from '../../shared/utils/file-types';
import { formatBytes, getFileExtension } from '../../shared/utils/upload-format';
import { renderMarkdown } from '../../shared/utils/markdown';
import { ChatMessage, ChatMessageAnswer, ChatMessageAttachment } from './chat-message.model';

@Component({
  selector: 'app-chat-message',
  imports: [
    LucideBot,
    LucideCheck,
    LucideClock,
    LucideCopy,
    LucideDownload,
    LucideDynamicIcon,
    LucideEye,
    LucideRefreshCw
  ],
  standalone: true,
  templateUrl: './chat-message.component.html',
  styleUrl: './chat-message.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '[class.message--incoming]': 'isIncoming()',
    '[class.message--outgoing]': '!isIncoming()'
  }
})
export class ChatMessageComponent {
  readonly message = input.required<ChatMessage>();
  readonly failed = input(false, {
    transform: (value: boolean | string) => value === true || value === ''
  });

  readonly preview = output<ChatMessageAttachment>();
  readonly download = output<ChatMessageAttachment>();
  readonly reply = output<void>();

  readonly copied = signal(false);
  private copiedTimer: ReturnType<typeof setTimeout> | null = null;

  readonly isIncoming = computed(() => this.message().direction === 'incoming');

  readonly renderedText = computed(() => renderMarkdown(this.message().text));

  readonly answers = computed<readonly ChatMessageAnswer[]>(() => {
    const explicit = this.message().answers;
    return explicit?.length ? explicit : parseAnswerTranscript(this.message().text);
  });

  readonly hasAnswers = computed(() => this.answers().length > 0);

  readonly hasAttachments = computed(() => (this.message().attachments?.length ?? 0) > 0);

  copyText(): void {
    navigator.clipboard.writeText(this.message().text).catch(() => undefined);
    this.copied.set(true);
    if (this.copiedTimer !== null) {
      clearTimeout(this.copiedTimer);
    }
    this.copiedTimer = setTimeout(() => this.copied.set(false), 1500);
  }

  iconFor(name: string): LucideIconData {
    return fileTypeStyleFor(getFileExtension(name)).icon;
  }

  colorFor(name: string): string {
    return fileTypeStyleFor(getFileExtension(name)).color;
  }

  formatBytes(bytes: number): string {
    return formatBytes(bytes);
  }
}

const ANSWERS_HEADER = 'Ответы на уточняющие вопросы по программе:';

function parseAnswerTranscript(text: string): readonly ChatMessageAnswer[] {
  const lines = text.split('\n');
  if (lines[0]?.trim() !== ANSWERS_HEADER) {
    return [];
  }

  return lines.slice(1).flatMap((line) => {
    const body = line.trim().replace(/^—\s*/, '');
    const separator = body.indexOf(': ');
    if (separator === -1) {
      return [];
    }
    return [
      {
        question: body.slice(0, separator).trim(),
        text: body.slice(separator + 2).trim()
      }
    ];
  });
}
