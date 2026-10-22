import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { By } from '@angular/platform-browser';
import { provideRouter, Router } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ProgramApiItem, ProgramStateApi, User } from '../../core/models/models';
import { ProfileService } from '../../core/services/profile.service';
import { ProgramsService } from '../../core/services/programs.service';
import { Select } from '../../shared/components/select/select.component';
import { ProgramsPage } from './programs-page.component';

const API_ITEMS: ProgramApiItem[] = [
  {
    session_id: 'it-python',
    title: 'Python для анализа данных',
    status: 'draft',
    updated_at: '2025-10-12T09:00:00Z'
  },
  {
    session_id: 'corp-security',
    title: 'Кибербезопасность для сотрудников',
    status: 'generating',
    updated_at: '2025-10-08T09:00:00Z'
  },
  {
    session_id: 'ml-basics',
    title: 'Основы машинного обучения',
    status: 'pending_approval',
    updated_at: '2025-10-05T09:00:00Z'
  },
  {
    session_id: 'agile-teams',
    title: 'Agile-трансформация команд',
    status: 'approved',
    updated_at: '2025-09-28T09:00:00Z'
  },
  {
    session_id: 'project-management',
    title: 'Управление проектами',
    status: 'draft',
    updated_at: '2025-09-24T09:00:00Z'
  },
  {
    session_id: 'soft-skills-leaders',
    title: 'Soft skills для руководителей',
    status: 'awaiting_answers',
    updated_at: '2025-09-19T09:00:00Z'
  },
  {
    session_id: 'web-development',
    title: 'Веб-разработка: полный цикл',
    status: 'approved',
    updated_at: '2025-08-30T09:00:00Z'
  },
  {
    session_id: 'product-analytics',
    title: 'Продуктовая аналитика',
    status: 'verified',
    updated_at: '2025-08-22T09:00:00Z'
  }
];

const PROGRAM_STATE: ProgramStateApi = {
  status: 'pending_approval',
  current_stage: 'summary',
  stages: {},
  running: false,
  busy: false,
  error: null,
  updated_at: '2025-10-13T10:00:00Z',
  version: 1,
  questions: [],
  draft: {
    kind: 'professional_development',
    title: 'Python для анализа данных',
    competencies: [{ id: 'comp-1', statement: 'Анализировать данные на Python.' }],
    curriculum: {
      modules: [
        {
          id: 'm-1',
          title: 'Введение',
          topics: [
            {
              id: 't-1.1',
              title: 'Основы Python',
              hours: { lecture: 10, practice: 6, lab: 2, self_study: 4 }
            }
          ]
        }
      ],
      attestation_hours: 2
    },
    sources: [],
    summary: {
      goal: 'Подготовить аналитика.',
      tasks: ['Освоить Python.'],
      target_audience: 'Специалисты',
      annotation: 'Практический курс по анализу данных.',
      focus: 'IT и аналитика'
    }
  },
  report: null,
  exports: null
};

const USER = signal<User | null>({
  id: 1,
  username: 'admin',
  first_name: 'Админ',
  last_name: 'Демо',
  role: 'superuser'
});

describe('ProgramsPage', () => {
  let fixture: ComponentFixture<ProgramsPage>;
  let service: ProgramsService;
  let http: HttpTestingController;
  let router: Router;

  const cards = () => fixture.debugElement.queryAll(By.css('.program-card'));
  const cardTitles = () =>
    cards().map((card) =>
      card.query(By.css('.program-card__title')).nativeElement.textContent.trim()
    );
  const emptyState = () => fixture.debugElement.query(By.css('.programs-page__empty'));
  const detailPanel = () => fixture.debugElement.query(By.css('.program-panel'));
  const openButtons = () => fixture.debugElement.queryAll(By.css('.program-card__open'));
  const filters = () => fixture.debugElement.queryAll(By.directive(Select));
  const pagination = () => fixture.debugElement.query(By.css('.pagination'));
  const paginationNav = () => fixture.debugElement.query(By.css('.pagination__nav'));
  const pageInfo = () => fixture.debugElement.query(By.css('.pagination__info'));
  const resultsRegion = () => fixture.debugElement.query(By.css('.programs-page__results'));
  const navigationButtons = () => fixture.debugElement.queryAll(By.css('.pagination__button'));
  const previousButton = () => navigationButtons()[0];
  const nextButton = () => navigationButtons()[navigationButtons().length - 1];
  const pageButton = (page: number) =>
    navigationButtons().find((button) => button.nativeElement.textContent.trim() === String(page));
  const dateSelect = () => filters()[0].componentInstance as Select;

  const selectDate = (label: string) => {
    const select = filters()[0];
    if (!(select.componentInstance as Select).isOpen()) {
      select.query(By.css('button[role="combobox"]')).nativeElement.click();
      fixture.detectChanges();
    }

    const option = select
      .queryAll(By.css('li[role="option"]'))
      .find((item) => item.nativeElement.textContent.trim() === label);
    if (!option) {
      throw new Error(`Filter option "${label}" not found`);
    }
    option.nativeElement.click();
    fixture.detectChanges();
  };

  const openFirstProgram = () => {
    const button = openButtons()[0];
    button.nativeElement.focus();
    button.nativeElement.click();
    fixture.detectChanges();
    http
      .match((request) => request.url.endsWith('/program'))
      .forEach((request) => request.flush(PROGRAM_STATE));
    TestBed.tick();
    fixture.detectChanges();
  };

  beforeEach(() => {
    USER.set({
      id: 1,
      username: 'admin',
      first_name: 'Админ',
      last_name: 'Демо',
      role: 'superuser'
    });

    TestBed.configureTestingModule({
      imports: [ProgramsPage],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: ProfileService, useValue: { user: USER } }
      ]
    });

    fixture = TestBed.createComponent(ProgramsPage);
    fixture.detectChanges();
    http = TestBed.inject(HttpTestingController);
    http
      .expectOne((request) => request.url === '/api/v1/chats/programmes')
      .flush({ items: API_ITEMS, total: API_ITEMS.length, page: 0, items_per_page: 100 });
    fixture.detectChanges();
    service = TestBed.inject(ProgramsService);
    router = TestBed.inject(Router);
  });

  afterEach(() => {
    http.verify();
    vi.restoreAllMocks();
  });

  it('renders the section heading and the catalog', () => {
    const heading = fixture.debugElement.query(By.css('h1'));

    expect(heading.nativeElement.textContent.trim()).toBe('Мои программы');
    expect(cardTitles().length).toBe(6);
    expect(cardTitles()[0]).toBe('Python для анализа данных');
    expect(pagination()).not.toBeNull();
  });

  it('paginates the catalog with numbered pages and a range summary', () => {
    expect(pageInfo().nativeElement.textContent).toContain('Показано 1–6 из 8 программ');
    expect(pageButton(1)?.nativeElement.getAttribute('aria-current')).toBe('page');
    expect(previousButton().nativeElement.disabled).toBe(true);

    nextButton().nativeElement.click();
    fixture.detectChanges();

    expect(cardTitles()).toEqual(['Веб-разработка: полный цикл', 'Продуктовая аналитика']);
    expect(pageInfo().nativeElement.textContent).toContain('Показано 7–8 из 8 программ');
    expect(pageButton(2)?.nativeElement.getAttribute('aria-current')).toBe('page');
    expect(previousButton().nativeElement.disabled).toBe(false);
    expect(nextButton().nativeElement.disabled).toBe(true);

    pageButton(1)?.nativeElement.click();
    fixture.detectChanges();

    expect(cardTitles().length).toBe(6);
  });

  it('announces and focuses the results after changing the page', () => {
    const results = resultsRegion().nativeElement as HTMLElement;
    results.scrollIntoView ??= () => undefined;
    const scroll = vi.spyOn(results, 'scrollIntoView');

    expect(pageInfo().nativeElement.getAttribute('aria-live')).toBe('polite');
    expect(pageInfo().nativeElement.getAttribute('aria-atomic')).toBe('true');

    nextButton().nativeElement.click();
    fixture.detectChanges();

    expect(document.activeElement).toBe(results);
    expect(scroll).toHaveBeenCalledWith({ block: 'start', behavior: 'auto' });
  });

  it('filters the cards by update date', () => {
    selectDate('Октябрь 2025');
    expect(cardTitles().length).toBe(3);

    selectDate('Август 2025');
    expect(cardTitles()).toEqual(['Веб-разработка: полный цикл', 'Продуктовая аналитика']);
    expect(paginationNav()).toBeNull();
  });

  it('returns to the first page when the date filter changes', () => {
    nextButton().nativeElement.click();
    fixture.detectChanges();

    selectDate('Октябрь 2025');

    expect(pageInfo().nativeElement.textContent).toContain('Показано 1–3 из 3 программ');
    expect(cardTitles().length).toBe(3);
  });

  it('offers only the update-date filter', () => {
    expect(filters().length).toBe(1);
    expect(dateSelect().value()).toBe('all');
    expect(
      dateSelect()
        .options()
        .map((option) => option.label)
    ).toEqual(['Все даты', 'Октябрь 2025', 'Сентябрь 2025', 'Август 2025']);
    expect(fixture.debugElement.query(By.css('.programs-page__filter--type'))).toBeNull();
    expect(fixture.debugElement.query(By.css('.programs-page__filter--tag'))).toBeNull();
  });

  it('does not render unavailable catalog actions', () => {
    expect(fixture.debugElement.query(By.css('.program-card__menu-button'))).toBeNull();
    expect(fixture.debugElement.query(By.css('.programs-page__create'))).toBeNull();
    expect(fixture.debugElement.query(By.css('a[href="/programs/new"]'))).toBeNull();
  });

  it('offers a reset when the filtered catalog is empty', async () => {
    selectDate('Август 2025');
    service.clear();
    fixture.detectChanges();

    expect(cardTitles().length).toBe(0);
    expect(emptyState().nativeElement.textContent).toContain('По фильтрам ничего не найдено');
    expect(emptyState().nativeElement.getAttribute('aria-live')).toBe('polite');
    expect(emptyState().nativeElement.getAttribute('aria-atomic')).toBe('true');

    fixture.debugElement.query(By.css('.programs-page__empty-action')).nativeElement.click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(dateSelect().value()).toBe('all');
    expect(emptyState().nativeElement.textContent).toContain('Пока нет программ');
    expect(document.activeElement).toBe(resultsRegion().nativeElement);
  });

  it('shows the empty catalog state', () => {
    service.clear();
    fixture.detectChanges();

    expect(cardTitles().length).toBe(0);
    expect(emptyState().nativeElement.textContent).toContain('Пока нет программ');
    expect(fixture.debugElement.query(By.css('.programs-page__empty-action'))).toBeNull();
  });

  it('opens the detail panel from a program card', () => {
    openFirstProgram();

    const text = detailPanel().nativeElement.textContent as string;
    expect(text).toContain('Python для анализа данных');
    expect(text).toContain('На согласовании');
    expect(text).toContain('24 часа');
    expect(text).toContain('Повышение квалификации');
    expect(text).toContain('IT и аналитика');
    expect(text).toContain('Практический курс по анализу данных.');
    expect(text).toContain('Освоить Python.');
  });

  it('does not render unavailable detail-panel tabs and actions', () => {
    openFirstProgram();
    const panel = detailPanel().nativeElement as HTMLElement;

    const tabs = [...panel.querySelectorAll('.program-panel__tab')].map((tab) =>
      tab.textContent?.trim()
    );
    expect(tabs).toEqual(['Описание', 'Структура', 'Компетенции']);
    expect(panel.querySelector('.program-panel__menu')).toBeNull();
    expect(panel.querySelector('.program-panel__material')).toBeNull();
    expect(
      [...panel.querySelectorAll('.program-panel__action')].map((action) =>
        action.textContent?.trim()
      )
    ).toEqual(['Открыть программу']);
  });

  it('opens the program chat from the detail panel', () => {
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);
    openFirstProgram();

    fixture.debugElement
      .queryAll(By.css('.program-panel__action'))
      .find((item) => item.nativeElement.textContent.trim() === 'Открыть программу')
      ?.nativeElement.click();

    expect(navigate).toHaveBeenCalledWith(['/chats', 'it-python']);
  });

  it('closes the panel and restores focus to the card', () => {
    const trigger = openButtons()[0].nativeElement as HTMLButtonElement;
    openFirstProgram();

    fixture.debugElement.query(By.css('.program-panel__close')).nativeElement.click();
    fixture.detectChanges();
    TestBed.tick();
    fixture.detectChanges();

    expect(detailPanel()).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});
