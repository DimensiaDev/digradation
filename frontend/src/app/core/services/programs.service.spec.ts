import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ProgramApiItem, ProgramStateApi } from '../models/models';
import { PROGRAMS_PAGE_SIZE, ProgramsService } from './programs.service';

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
  stages: {
    competences: 'completed',
    hours: 'completed',
    assessment: 'pending',
    materials: 'pending',
    sources: 'completed',
    summary: 'completed'
  },
  running: false,
  busy: false,
  error: null,
  updated_at: '2025-10-13T10:00:00Z',
  version: 3,
  questions: [],
  report: null,
  exports: null,
  revision: null,
  last_revision: null,
  approved_by: null,
  approved_at: null,
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
      tasks: ['Освоить Python.', 'Научиться визуализировать данные.'],
      target_audience: 'Специалисты',
      annotation: 'Практический курс по анализу данных.',
      focus: 'IT и аналитика'
    }
  }
};

describe('ProgramsService', () => {
  let service: ProgramsService;
  let http: HttpTestingController;

  const load = (items: ProgramApiItem[] = API_ITEMS) => {
    service.load();
    http
      .expectOne(
        (request) =>
          request.url === '/api/v1/chats/programmes' &&
          request.params.get('page') === '0' &&
          request.params.get('items_per_page') === '100'
      )
      .flush({ items, total: items.length, page: 0, items_per_page: 100 });
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()]
    });
    service = TestBed.inject(ProgramsService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('loads the catalogue from the API', () => {
    load();

    expect(service.loaded()).toBe(true);
    expect(service.loading()).toBe(false);
    expect(service.programs().length).toBe(8);
    expect(service.programs()[0]).toMatchObject({
      id: 'it-python',
      title: 'Python для анализа данных',
      status: 'draft',
      updatedAt: '2025-10-12T09:00:00Z'
    });
  });

  it('keeps fields the catalogue does not return empty', () => {
    load();

    expect(service.programs()[0].type).toBe('');
    expect(service.programs()[0].direction).toBe('');
    expect(service.programs()[0].hours).toBe(0);
    expect(service.programs()[0].description).toBe('');
    expect(service.programs()[0].goals).toEqual([]);
    expect(service.programs()[0].modules).toBeUndefined();
    expect(service.programs()[0].competencyStatements).toBeUndefined();
  });

  it('keeps the API order and paginates the catalogue locally', () => {
    load();

    const first = service.query();
    const second = service.query({ page: 1 });

    expect(first.items.length).toBe(PROGRAMS_PAGE_SIZE);
    expect(first.total).toBe(8);
    expect(first.page).toBe(0);
    expect(first.items_per_page).toBe(PROGRAMS_PAGE_SIZE);
    expect(second.items.map((program) => program.id)).toEqual([
      'web-development',
      'product-analytics'
    ]);
  });

  it('filters by update month before paginating', () => {
    load();

    const result = service.query({ date: '2025-08' });

    expect(result.items.map((program) => program.title)).toEqual([
      'Веб-разработка: полный цикл',
      'Продуктовая аналитика'
    ]);
    expect(result.total).toBe(2);
  });

  it('reports a failed load without erasing the catalogue', () => {
    load();
    service.load();
    http
      .expectOne((request) => request.url === '/api/v1/chats/programmes')
      .flush(null, { status: 500, statusText: 'Server Error' });

    expect(service.error()).toBe('Не удалось загрузить программы');
    expect(service.loaded()).toBe(true);
    expect(service.programs().length).toBe(8);
  });

  it('fetches every API page when the catalogue is larger than one page', () => {
    service.load();
    const first = API_ITEMS.slice(0, 3);
    http
      .expectOne((request) => request.params.get('page') === '0')
      .flush({ items: first, total: 4, page: 0, items_per_page: 100 });
    http
      .expectOne((request) => request.params.get('page') === '1')
      .flush({ items: [API_ITEMS[3]], total: 4, page: 1, items_per_page: 100 });

    expect(service.programs().length).toBe(4);
  });

  it('loads the programme detail and maps its blocks', () => {
    load();
    service.loadDetail('it-python');
    http.expectOne('/api/v1/chats/it-python/program').flush(PROGRAM_STATE);

    const program = service.programs().find((item) => item.id === 'it-python');
    expect(program?.status).toBe('pending_approval');
    expect(program?.updatedAt).toBe('2025-10-13T10:00:00Z');
    expect(program?.type).toBe('Повышение квалификации');
    expect(program?.direction).toBe('IT и аналитика');
    expect(program?.hours).toBe(24);
    expect(program?.description).toBe('Практический курс по анализу данных.');
    expect(program?.goals).toEqual(['Освоить Python.', 'Научиться визуализировать данные.']);
    expect(program?.competencyStatements).toEqual(['Анализировать данные на Python.']);
    expect(program?.modules?.map((module) => module.title)).toEqual(['Введение']);
  });

  it('polls the detail while the run is busy and stops when it is not', () => {
    vi.useFakeTimers();

    try {
      load();
      service.loadDetail('it-python');
      http
        .expectOne('/api/v1/chats/it-python/program')
        .flush({ ...PROGRAM_STATE, status: 'generating', busy: true });

      vi.advanceTimersByTime(2_000);
      http.expectOne('/api/v1/chats/it-python/program').flush({ ...PROGRAM_STATE, busy: false });

      vi.advanceTimersByTime(4_000);
      expect(http.match('/api/v1/chats/it-python/program').length).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not apply a detail response after the panel was closed', () => {
    load();
    service.loadDetail('it-python');
    service.stopDetail();
    const request = http.expectOne('/api/v1/chats/it-python/program');
    request.flush(PROGRAM_STATE);

    const program = service.programs().find((item) => item.id === 'it-python');
    expect(program?.status).toBe('draft');
    expect(program?.hours).toBe(0);
  });

  it('clears the catalogue', () => {
    load();
    service.clear();

    expect(service.programs()).toEqual([]);
    expect(service.loaded()).toBe(false);
  });
});
