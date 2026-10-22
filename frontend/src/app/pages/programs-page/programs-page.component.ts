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
import { LucideChevronLeft, LucideChevronRight, LucideFolderOpen } from '@lucide/angular';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { Program } from '../../core/models/models';
import { ProfileService } from '../../core/services/profile.service';
import { PROGRAMS_PAGE_SIZE, ProgramsService } from '../../core/services/programs.service';
import { ProgramDetailPanelComponent } from '../../shared/components/program-detail-panel/program-detail-panel.component';
import { ProgramCardComponent } from '../../shared/components/program-card/program-card.component';
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

function buildDateOptions(programs: readonly Program[]): SelectOption[] {
  const months = [...new Set(programs.map((program) => program.updatedAt.slice(0, 7)))].sort(
    (first, second) => second.localeCompare(first)
  );
  return [
    { value: 'all', label: 'Все даты' },
    ...months.map((month) => ({ value: month, label: monthLabel(month) }))
  ];
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
  private readonly profile = inject(ProfileService);
  private readonly router = inject(Router);

  constructor() {
    effect(() => {
      if (this.profile.user()) {
        this.programsService.load();
      } else {
        this.programsService.clear();
      }
    });
  }

  readonly dateFilter = signal('all');
  readonly page = signal(0);

  readonly dateOptions = computed(() => buildDateOptions(this.programsService.programs()));
  readonly selectedProgramId = signal<string | null>(null);

  private readonly resultsRegion = viewChild<ElementRef<HTMLElement>>('resultsRegion');
  private lastTrigger: HTMLElement | null = null;

  readonly pageSize = PROGRAMS_PAGE_SIZE;

  readonly hasActiveFilters = computed(() => this.dateFilter() !== 'all');

  readonly result = computed(() =>
    this.programsService.query({
      page: this.page(),
      itemsPerPage: this.pageSize,
      date: this.dateFilter()
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

  selectDate(value: string): void {
    this.dateFilter.set(value);
    this.page.set(0);
    this.scrollToResults();
  }

  resetFilters(focusResults = true): void {
    this.dateFilter.set('all');
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

  openProgram(program: Program, trigger: HTMLElement | null): void {
    this.lastTrigger = trigger;
    this.selectedProgramId.set(program.id);
    this.programsService.loadDetail(program.id);
  }

  closeProgram(): void {
    const trigger = this.lastTrigger;
    this.lastTrigger = null;
    this.selectedProgramId.set(null);
    this.programsService.stopDetail();

    if (trigger?.isConnected) {
      trigger.focus();
    }
  }

  openProgramChat(): void {
    const program = this.selectedProgram();
    if (!program) {
      return;
    }

    this.programsService.stopDetail();
    void this.router.navigate(['/chats', program.id]);
  }
}
