import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { LucideChevronLeft, LucideChevronRight, LucideFolderOpen } from '@lucide/angular';
import { FormsModule } from '@angular/forms';
import { Program } from '../../core/models/models';
import { NotificationService } from '../../core/services/notification.service';
import { PROGRAMS_PAGE_SIZE, ProgramsService } from '../../core/services/programs.service';
import {
  ProgramAction,
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
  const directions = [...new Set(programs.map((program) => program.direction))];
  return [
    { value: 'all', label: 'Все типы' },
    ...directions.map((direction) => ({ value: direction, label: direction }))
  ];
}

function buildTagOptions(programs: readonly Program[]): SelectOption[] {
  const tags = [...new Set(programs.flatMap((program) => program.tags))].sort((first, second) =>
    first.localeCompare(second, 'ru')
  );
  return [
    { value: 'all', label: 'Все технологии' },
    ...tags.map((tag) => ({ value: tag, label: tag }))
  ];
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
  private readonly router = inject(Router);

  readonly typeOptions = computed(() => buildTypeOptions(this.programsService.programs()));
  readonly dateOptions = computed(() => buildDateOptions(this.programsService.programs()));
  readonly tagOptions = computed(() => buildTagOptions(this.programsService.programs()));

  readonly typeFilter = signal('all');
  readonly dateFilter = signal('all');
  readonly tagFilter = signal<readonly string[]>([]);
  readonly page = signal(0);

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
  }

  selectDate(value: string): void {
    this.dateFilter.set(value);
    this.page.set(0);
  }

  selectTags(values: string[]): void {
    this.tagFilter.set(values);
    this.page.set(0);
  }

  resetFilters(): void {
    this.typeFilter.set('all');
    this.dateFilter.set('all');
    this.tagFilter.set([]);
    this.page.set(0);
  }

  previousPage(): void {
    if (this.canGoBack()) {
      this.page.update((page) => page - 1);
    }
  }

  nextPage(): void {
    if (this.canGoForward()) {
      this.page.update((page) => page + 1);
    }
  }

  goToPage(page: number): void {
    if (page >= 1 && page <= this.totalPages()) {
      this.page.set(page - 1);
    }
  }

  private clampPage(): void {
    const lastPage = Math.max(0, this.totalPages() - 1);
    if (this.page() > lastPage) {
      this.page.set(lastPage);
    }
  }

  onAction(program: Program, action: ProgramAction): void {
    switch (action) {
      case 'edit':
        this.router.navigate(['/programs/new']);
        return;
      case 'duplicate':
        this.programsService.duplicate(program.id);
        this.resetFilters();
        this.notifications.success('Программа продублирована', {
          description: 'Копия сохранена как черновик.'
        });
        return;
      case 'archive':
        this.programsService.archive(program.id);
        this.clampPage();
        this.notifications.info('Программа архивирована');
        return;
      case 'delete':
        this.programsService.remove(program.id);
        this.clampPage();
        this.notifications.info('Программа удалена');
        return;
    }
  }
}
