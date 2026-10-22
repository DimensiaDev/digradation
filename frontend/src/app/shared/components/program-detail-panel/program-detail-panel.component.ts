import {
  afterRenderEffect,
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  ElementRef,
  HostListener,
  input,
  output,
  signal,
  viewChild,
  viewChildren
} from '@angular/core';
import {
  LucideArchive,
  LucideArrowLeft,
  LucideBookOpen,
  LucideCheck,
  LucideClock,
  LucideCompass,
  LucideCopy,
  LucideDynamicIcon,
  LucideEllipsisVertical,
  LucideFileText,
  LucideGraduationCap,
  LucideLayers,
  LucidePencil,
  LucideTarget,
  LucideTrash2
} from '@lucide/angular';
import { PROGRAM_STATUS_LABELS, Program } from '../../../core/models/models';
import {
  buildProgramCompetencies,
  buildProgramMaterials,
  buildProgramModules,
  formatProgramHours
} from '../../utils/program-detail-content';
import { nextMenuIndex } from '../../utils/menu-keyboard';
import { PROGRAM_THEME_ICONS } from '../../utils/program-theme';

type ProgramTab = 'description' | 'structure' | 'competencies' | 'materials';

interface ProgramTabItem {
  readonly id: ProgramTab;
  readonly label: string;
}

const PROGRAM_TABS: readonly ProgramTabItem[] = [
  { id: 'description', label: 'Описание' },
  { id: 'structure', label: 'Структура' },
  { id: 'competencies', label: 'Компетенции' },
  { id: 'materials', label: 'Материалы' }
];

@Component({
  selector: 'app-program-detail-panel',
  imports: [
    LucideArchive,
    LucideArrowLeft,
    LucideBookOpen,
    LucideCheck,
    LucideClock,
    LucideCompass,
    LucideCopy,
    LucideDynamicIcon,
    LucideEllipsisVertical,
    LucideFileText,
    LucideGraduationCap,
    LucideLayers,
    LucidePencil,
    LucideTarget,
    LucideTrash2
  ],
  templateUrl: './program-detail-panel.component.html',
  styleUrl: './program-detail-panel.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ProgramDetailPanelComponent {
  readonly program = input<Program | null>(null);
  readonly open = output<void>();
  readonly closed = output<void>();

  readonly tabs = PROGRAM_TABS;
  readonly activeTab = signal<ProgramTab>('description');
  readonly menuOpen = signal(false);
  readonly menuId = 'program-panel-menu-header';

  readonly themeIcon = computed(() => PROGRAM_THEME_ICONS[this.program()?.theme ?? 'it']);
  readonly statusLabel = computed(() => {
    const program = this.program();
    return program ? PROGRAM_STATUS_LABELS[program.status] : '';
  });
  readonly hoursLabel = computed(() => {
    const hours = this.program()?.hours ?? 0;
    return hours > 0 ? formatProgramHours(hours) : '—';
  });
  readonly modules = computed(() => {
    const program = this.program();
    return program ? buildProgramModules(program.hours, program.modules) : [];
  });
  readonly competencies = computed(() => {
    const program = this.program();
    return program ? buildProgramCompetencies(program) : [];
  });
  readonly materials = computed(() => buildProgramMaterials());
  readonly isArchived = computed(() => this.program()?.status === 'archived');
  readonly deleteMenuIndex = computed(() => (this.isArchived() ? 1 : 2));
  readonly formatHours = formatProgramHours;
  readonly activeMenuItem = signal(0);

  private readonly programId = computed(() => this.program()?.id ?? null);
  private readonly scroll = viewChild<ElementRef<HTMLElement>>('scroll');
  private readonly closeButton = viewChild<ElementRef<HTMLButtonElement>>('closeButton');
  private readonly tabButtons = viewChildren<ElementRef<HTMLButtonElement>>('tabButton');
  private readonly headerMenu = viewChild<ElementRef<HTMLElement>>('headerMenu');
  private readonly headerMenuButton = viewChild<ElementRef<HTMLButtonElement>>('headerMenuButton');
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

    effect(() => {
      if (!this.programId()) {
        return;
      }

      const scroll = this.scroll()?.nativeElement;
      if (scroll) {
        scroll.scrollTop = 0;
      }

      this.activeTab.set('description');
      this.menuOpen.set(false);
      this.closeButton()?.nativeElement.focus();
    });
  }

  selectTab(tab: ProgramTab): void {
    this.activeTab.set(tab);
  }

  onTabKeydown(event: KeyboardEvent): void {
    const lastIndex = this.tabs.length - 1;
    const currentIndex = this.tabs.findIndex((tab) => tab.id === this.activeTab());
    let nextIndex: number;

    switch (event.key) {
      case 'ArrowRight':
        nextIndex = currentIndex === lastIndex ? 0 : currentIndex + 1;
        break;
      case 'ArrowLeft':
        nextIndex = currentIndex === 0 ? lastIndex : currentIndex - 1;
        break;
      case 'Home':
        nextIndex = 0;
        break;
      case 'End':
        nextIndex = lastIndex;
        break;
      default:
        return;
    }

    const nextTab = this.tabs[nextIndex];
    if (!nextTab) {
      return;
    }

    event.preventDefault();
    this.activeTab.set(nextTab.id);
    this.tabButtons()[nextIndex]?.nativeElement.focus();
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
      this.headerMenuButton()?.nativeElement.focus();
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

  runMenuAction(): void {
    this.closeMenu();
  }

  close(): void {
    this.closed.emit();
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent): void {
    const target = event.target as Node;
    const insideMenu = this.headerMenu()?.nativeElement.contains(target);

    if (!insideMenu) {
      this.closeMenu();
    }
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    if (!this.programId()) {
      return;
    }

    if (this.menuOpen()) {
      this.closeMenu(true);
      return;
    }

    this.close();
  }
}
