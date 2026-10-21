import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  HostListener,
  computed,
  inject,
  input,
  output,
  signal,
  viewChild
} from '@angular/core';
import {
  LucideArchive,
  LucideBriefcase,
  LucideCalendar,
  LucideChartColumn,
  LucideCode2,
  LucideCopy,
  LucideDynamicIcon,
  LucideEllipsisVertical,
  LucidePencil,
  LucideRefreshCw,
  LucideShieldCheck,
  LucideTrash2,
  LucideUsers,
  type LucideIcon
} from '@lucide/angular';
import { PROGRAM_STATUS_LABELS, Program } from '../../../core/models/models';

export type ProgramAction = 'edit' | 'duplicate' | 'archive' | 'delete';

const PROGRAM_THEME_ICONS: Record<Program['theme'], LucideIcon> = {
  it: LucideCode2,
  analytics: LucideChartColumn,
  'soft-skills': LucideUsers,
  agile: LucideRefreshCw,
  management: LucideBriefcase,
  security: LucideShieldCheck
};

function formatDate(timestamp: string): string {
  return new Date(timestamp).toLocaleDateString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric'
  });
}

function initialsFor(name: string): string {
  const parts = name.split(/\s+/).filter(Boolean);
  const first = parts[0]?.[0] ?? '';
  const second = parts[1]?.[0] ?? '';
  return `${first}${second}`.toUpperCase();
}

@Component({
  selector: 'app-program-card',
  imports: [
    LucideArchive,
    LucideCalendar,
    LucideCopy,
    LucideDynamicIcon,
    LucideEllipsisVertical,
    LucidePencil,
    LucideTrash2
  ],
  templateUrl: './program-card.component.html',
  styleUrl: './program-card.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ProgramCardComponent {
  readonly program = input.required<Program>();
  readonly action = output<ProgramAction>();

  readonly icon = computed(() => PROGRAM_THEME_ICONS[this.program().theme]);
  readonly statusLabel = computed(() => PROGRAM_STATUS_LABELS[this.program().status]);
  readonly formattedDate = computed(() => formatDate(this.program().updatedAt));
  readonly initials = computed(() => initialsFor(this.program().author.name));
  readonly isArchived = computed(() => this.program().status === 'archived');
  readonly menuId = computed(() => `program-menu-${this.program().id}`);

  readonly menuOpen = signal(false);

  private readonly elementRef = inject(ElementRef<HTMLElement>);
  private readonly menuButton = viewChild<ElementRef<HTMLButtonElement>>('menuButton');

  toggleMenu(): void {
    this.menuOpen.update((open) => !open);
  }

  closeMenu(restoreFocus = false): void {
    if (!this.menuOpen()) {
      return;
    }

    this.menuOpen.set(false);
    if (restoreFocus) {
      this.menuButton()?.nativeElement.focus();
    }
  }

  run(action: ProgramAction): void {
    this.closeMenu();
    this.action.emit(action);
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent): void {
    if (!this.elementRef.nativeElement.contains(event.target as Node)) {
      this.closeMenu();
    }
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    this.closeMenu(true);
  }
}
