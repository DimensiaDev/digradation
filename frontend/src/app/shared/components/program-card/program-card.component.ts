import {
  afterRenderEffect,
  ChangeDetectionStrategy,
  Component,
  computed,
  ElementRef,
  HostListener,
  inject,
  input,
  output,
  signal,
  viewChild,
  viewChildren
} from '@angular/core';
import {
  LucideArchive,
  LucideArchiveRestore,
  LucideCalendar,
  LucideCopy,
  LucideDynamicIcon,
  LucideEllipsisVertical,
  LucideTrash2
} from '@lucide/angular';
import { PROGRAM_STATUS_LABELS, Program, ProgramAction } from '../../../core/models/models';
import { nextMenuIndex } from '../../utils/menu-keyboard';
import { PROGRAM_THEME_ICONS } from '../../utils/program-theme';

function formatDate(timestamp: string): string {
  return new Date(timestamp).toLocaleDateString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric'
  });
}

export interface ProgramActionEvent {
  readonly action: ProgramAction;
  readonly trigger: HTMLElement | null;
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
    LucideArchiveRestore,
    LucideCalendar,
    LucideCopy,
    LucideDynamicIcon,
    LucideEllipsisVertical,
    LucideTrash2
  ],
  templateUrl: './program-card.component.html',
  styleUrl: './program-card.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ProgramCardComponent {
  readonly program = input.required<Program>();
  readonly open = output<HTMLElement | null>();
  readonly action = output<ProgramActionEvent>();

  readonly icon = computed(() => PROGRAM_THEME_ICONS[this.program().theme]);
  readonly statusLabel = computed(() => PROGRAM_STATUS_LABELS[this.program().status]);
  readonly formattedDate = computed(() => formatDate(this.program().updatedAt));
  readonly initials = computed(() => initialsFor(this.program().author.name));
  readonly isArchived = computed(() => this.program().status === 'archived');
  readonly menuId = computed(() => `program-menu-${this.program().id}`);

  readonly menuOpen = signal(false);
  readonly activeMenuItem = signal(0);

  private readonly elementRef = inject(ElementRef<HTMLElement>);
  private readonly menuButton = viewChild<ElementRef<HTMLButtonElement>>('menuButton');
  private readonly menuItems = viewChildren<ElementRef<HTMLButtonElement>>('menuItem');
  private pendingMenuFocus = false;

  constructor() {
    afterRenderEffect({
      write: () => {
        if (!this.menuOpen() || !this.pendingMenuFocus) {
          return;
        }

        this.pendingMenuFocus = false;
        this.menuItems()[this.activeMenuItem()]?.nativeElement.focus();
      }
    });
  }

  openProgram(event: Event): void {
    this.closeMenu();
    this.open.emit(event.currentTarget instanceof HTMLElement ? event.currentTarget : null);
  }

  toggleMenu(): void {
    if (this.menuOpen()) {
      this.closeMenu();
      return;
    }

    this.activeMenuItem.set(0);
    this.pendingMenuFocus = true;
    this.menuOpen.set(true);
  }

  closeMenu(restoreFocus = false): void {
    if (!this.menuOpen()) {
      return;
    }

    this.pendingMenuFocus = false;
    this.menuOpen.set(false);
    if (restoreFocus) {
      this.menuButton()?.nativeElement.focus();
    }
  }

  onMenuKeydown(event: KeyboardEvent): void {
    const items = this.menuItems();
    const nextIndex = nextMenuIndex(event.key, this.activeMenuItem(), items.length);
    if (nextIndex === null) {
      return;
    }

    event.preventDefault();
    this.activeMenuItem.set(nextIndex);
    items[nextIndex]?.nativeElement.focus();
  }

  run(action: ProgramAction): void {
    const trigger = this.menuButton()?.nativeElement ?? null;
    this.closeMenu(true);
    this.action.emit({ action, trigger });
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
