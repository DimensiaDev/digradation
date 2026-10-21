import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  inject,
  Injector,
  signal,
  viewChild
} from '@angular/core';
import { LucideChevronLeft, LucideChevronRight, LucideFolderOpen } from '@lucide/angular';
import { FormsModule } from '@angular/forms';
import { Program } from '../../core/models/models';
import { NotificationService } from '../../core/services/notification.service';
import { PROGRAMS_PAGE_SIZE, ProgramsService } from '../../core/services/programs.service';
import { ProgramDetailPanelComponent } from '../../shared/components/program-detail-panel/program-detail-panel.component';
import {
  ProgramActionEvent,
  ProgramCardComponent
} from '../../shared/components/program-card/program-card.component';
import { Select, SelectOption } from '../../shared/components/select/select.component';

const MONTH_LABELS = [
  'Январь',
  'Февраль',
  'Март',
  'Апрель',
  'Май',
  'Июнь',
  'Июль',
  'Август',
  'Сентябрь',
  'Октябрь',
  'Ноябрь',
  'Декабрь'
];

function monthLabel(value: string): string {
  const [year, month] = value.split('-');
  return `${MONTH_LABELS[Number(month) - 1]} ${year}`;
}

function buildTypeOptions(programs: readonly Program[]): SelectOption[] {
  const types = [...new Set(programs.map((program) => program.type))];
  return [
    { value: 'all', label: 'Все типы' },
    ...types.map((type) => ({ value: type, label: type }))
  ];
}

function buildTagOptions(programs: readonly Program[]): SelectOption[] {
  const tags = [...new Set(programs.flatMap((program) => program.tags))].sort((first, second) =>
    first.localeCompare(second, 'ru')
  );
  return [{ value: 'all', label: 'Все теги' }, ...tags.map((tag) => ({ value: tag, label: tag }))];
}

function buildDateOptions(programs: readonly Program[]): SelectOption[] {
  const months = [...new Set(programs.map((program) => program.updatedAt.slice(0, 7)))].sort(
    (first, second) => second.localeCompare(first)
  );
  return [
    { value: 'all', label: 'Все даты' },
    ...months.map((month) => ({ value: month, label: monthLabel(month) }))
  ];
}

function includeSelectedOptions(
  options: SelectOption[],
  selected: readonly string[],
  labelFor: (value: string) => string = (value) => value
): SelectOption[] {
  const available = new Set(options.map((option) => option.value));
  const missing = selected.filter((value) => value !== 'all' && !available.has(value));
  if (missing.length === 0) {
    return options;
  }

  return [...options, ...missing.map((value) => ({ value, label: labelFor(value) }))];
}

@Component({
  selector: 'app-programs-page',
  imports: [
    ProgramCardComponent,
    ProgramDetailPanelComponent,
    FormsModule,
    Select,
    LucideChevronLeft,
    LucideChevronRight,
    LucideFolderOpen
  ],
  templateUrl: './programs-page.component.html',
  styleUrl: './programs-page.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ProgramsPage {
  private readonly programsService = inject(ProgramsService);
  private readonly notifications = inject(NotificationService);
  private readonly injector = inject(Injector);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  readonly typeFilter = signal('all');
  readonly dateFilter = signal('all');
  readonly tagFilter = signal<readonly string[]>([]);
  readonly page = signal(0);

  readonly typeOptions = computed(() =>
    includeSelectedOptions(buildTypeOptions(this.programsService.programs()), [this.typeFilter()])
  );
  readonly dateOptions = computed(() =>
    includeSelectedOptions(
      buildDateOptions(this.programsService.programs()),
      [this.dateFilter()],
      monthLabel
    )
  );
  readonly tagOptions = computed(() =>
    includeSelectedOptions(buildTagOptions(this.programsService.programs()), this.tagFilter())
  );
  readonly selectedProgramId = signal<string | null>(null);

  private readonly resultsRegion = viewChild<ElementRef<HTMLElement>>('resultsRegion');
  private lastTrigger: HTMLElement | null = null;

  readonly pageSize = PROGRAMS_PAGE_SIZE;

  readonly hasActiveFilters = computed(
    () => this.typeFilter() !== 'all' || this.dateFilter() !== 'all' || this.tagFilter().length > 0
  );

  readonly result = computed(() =>
    this.programsService.query({
      page: this.page(),
      itemsPerPage: this.pageSize,
      type: this.typeFilter(),
      date: this.dateFilter(),
      tags: this.tagFilter()
    })
  );

  readonly programs = computed(() => this.result().items);
  readonly selectedProgram = computed(() => {
    const id = this.selectedProgramId();
    return id
      ? (this.programsService.programs().find((program) => program.id === id) ?? null)
      : null;
  });
  readonly total = computed(() => this.result().total);
  readonly totalPages = computed(() => Math.max(1, Math.ceil(this.total() / this.pageSize)));
  readonly canGoBack = computed(() => this.page() > 0);
  readonly canGoForward = computed(() => this.page() + 1 < this.totalPages());
  readonly rangeStart = computed(() => (this.total() === 0 ? 0 : this.page() * this.pageSize + 1));
  readonly rangeEnd = computed(() => Math.min((this.page() + 1) * this.pageSize, this.total()));

  readonly visiblePages = computed<readonly (number | 'ellipsis')[]>(() => {
    const total = this.totalPages();
    if (total <= 7) {
      return Array.from({ length: total }, (_, index) => index + 1);
    }

    const current = this.page() + 1;
    const pages = new Set<number>([1, total]);
    for (let page = current - 2; page <= current + 2; page++) {
      if (page >= 1 && page <= total) {
        pages.add(page);
      }
    }

    const sorted = [...pages].sort((first, second) => first - second);
    const items: (number | 'ellipsis')[] = [];
    sorted.forEach((page, index) => {
      if (index > 0 && page - sorted[index - 1] > 1) {
        items.push('ellipsis');
      }
      items.push(page);
    });
    return items;
  });

  selectType(value: string): void {
    this.typeFilter.set(value);
    this.page.set(0);
    this.scrollToResults();
  }

  selectDate(value: string): void {
    this.dateFilter.set(value);
    this.page.set(0);
    this.scrollToResults();
  }

  selectTags(values: string[]): void {
    this.tagFilter.set(values);
    this.page.set(0);
  }

  resetFilters(focusResults = true): void {
    this.typeFilter.set('all');
    this.dateFilter.set('all');
    this.tagFilter.set([]);
    this.page.set(0);
    if (focusResults) {
      this.focusResults();
    }
  }

  previousPage(): void {
    if (this.canGoBack()) {
      this.page.update((page) => page - 1);
      this.focusResults();
    }
  }

  nextPage(): void {
    if (this.canGoForward()) {
      this.page.update((page) => page + 1);
      this.focusResults();
    }
  }

  goToPage(page: number): void {
    if (page >= 1 && page <= this.totalPages()) {
      this.page.set(page - 1);
      this.focusResults();
    }
  }

  private focusResults(): void {
    const results = this.resultsRegion()?.nativeElement;
    if (!results) {
      return;
    }

    results.focus({ preventScroll: true });
    results.scrollIntoView?.({ block: 'start', behavior: 'auto' });
  }

  private scrollToResults(): void {
    this.resultsRegion()?.nativeElement.scrollIntoView?.({ block: 'start', behavior: 'auto' });
  }

  private clampPage(): void {
    const lastPage = Math.max(0, this.totalPages() - 1);
    if (this.page() > lastPage) {
      this.page.set(lastPage);
    }
  }

  openProgram(program: Program, trigger: HTMLElement | null): void {
    this.lastTrigger = trigger;
    this.selectedProgramId.set(program.id);
  }

  closeProgram(): void {
    const trigger = this.lastTrigger;
    this.lastTrigger = null;
    this.selectedProgramId.set(null);

    if (trigger?.isConnected) {
      trigger.focus();
    }
  }

  onAction(program: Program, event: ProgramActionEvent): void {
    const index = this.programs().findIndex((item) => item.id === program.id);
    let removed = false;

    switch (event.action) {
      case 'duplicate':
        this.programsService.duplicate(program.id);
        this.resetFilters(false);
        this.notifications.success('Программа продублирована', {
          description: 'Копия сохранена как черновик.'
        });
        break;
      case 'archive':
        this.programsService.archive(program.id);
        this.clampPage();
        this.notifications.info('Программа архивирована');
        break;
      case 'unarchive':
        this.programsService.unarchive(program.id);
        this.clampPage();
        this.notifications.info('Программа восстановлена');
        break;
      case 'delete':
        if (!window.confirm(`Удалить программу «${program.title}»?`)) {
          return;
        }
        if (this.selectedProgramId() === program.id) {
          this.selectedProgramId.set(null);
          this.lastTrigger = null;
        }
        this.programsService.remove(program.id);
        this.clampPage();
        removed = true;
        this.notifications.info('Программа удалена');
        break;
    }

    this.restoreActionFocus(event.trigger, index, removed);
  }

  private restoreActionFocus(trigger: HTMLElement | null, index: number, removed: boolean): void {
    afterNextRender(
      () => {
        if (!removed && trigger?.isConnected) {
          trigger.focus();
          return;
        }

        const buttons = this.host.nativeElement.querySelectorAll<HTMLElement>(
          '.program-card__menu-button'
        );
        const target = buttons[Math.max(0, Math.min(index, buttons.length - 1))];
        if (target) {
          target.focus();
          return;
        }

        this.focusResults();
      },
      { injector: this.injector }
    );
  }
}
