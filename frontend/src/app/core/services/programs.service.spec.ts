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
    updated_at: '2025-10-12T09:00:00Z',
    type: 'Повышение квалификации',
    direction: 'IT и аналитика',
    hours: 72,
    tags: ['IT', 'Python', 'Аналитика'],
    author: { name: 'Админ Демо', avatarUrl: null },
    theme: 'it',
    description: 'Практический курс',
    goals: ['Освоить Python.']
  },
  {
    session_id: 'corp-security',
    title: 'Кибербезопасность для сотрудников',
    status: 'generating',
    updated_at: '2025-10-08T09:00:00Z',
    type: 'Корпоративное обучение',
    direction: 'Информационная безопасность',
    hours: 40,
    tags: ['IT', 'Безопасность'],
    theme: 'security'
  },
  {
    session_id: 'ml-basics',
    title: 'Основы машинного обучения',
    status: 'pending_approval',
    updated_at: '2025-10-05T09:00:00Z',
    type: 'Профессиональная переподготовка',
    direction: 'Искусственный интеллект',
    hours: 256,
    tags: ['ML', 'Python', 'Аналитика'],
    theme: 'analytics'
  },
  {
    session_id: 'agile-teams',
    title: 'Agile-трансформация команд',
    status: 'approved',
    updated_at: '2025-09-28T09:00:00Z',
    type: 'Корпоративное обучение',
    direction: 'Управление и процессы',
    hours: 16,
    tags: ['Agile', 'Soft skills'],
    theme: 'agile'
  },
  {
    session_id: 'project-management',
    title: 'Управление проектами',
    status: 'draft',
    updated_at: '2025-09-24T09:00:00Z',
    type: 'Повышение квалификации',
    direction: 'Управление и процессы',
    hours: 36,
    tags: ['Менеджмент', 'Agile'],
    theme: 'management'
  },
  {
    session_id: 'soft-skills-leaders',
    title: 'Soft skills для руководителей',
    status: 'awaiting_answers',
    updated_at: '2025-09-19T09:00:00Z',
    type: 'Корпоративное обучение',
    direction: 'Развитие руководителей',
    hours: 24,
    tags: ['Soft skills', 'Коммуникация'],
    theme: 'soft-skills'
  },
  {
    session_id: 'web-development',
    title: 'Веб-разработка: полный цикл',
    status: 'approved',
    updated_at: '2025-08-30T09:00:00Z',
    type: 'Профессиональная переподготовка',
    direction: 'IT и аналитика',
    hours: 256,
    tags: ['IT', 'JavaScript'],
    theme: 'it'
  },
  {
    session_id: 'product-analytics',
    title: 'Продуктовая аналитика',
    status: 'verified',
    updated_at: '2025-08-22T09:00:00Z',
    type: 'Повышение квалификации',
    direction: 'IT и аналитика',
    hours: 48,
    tags: ['Аналитика', 'Data'],
    theme: 'analytics'
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
      ]
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
      type: 'Повышение квалификации',
      hours: 72,
      status: 'draft'
    });
    expect(service.programs()[0].author.name).toBe('Админ Демо');
  });

  it('falls back to the current user and placeholders for absent fields', () => {
    load([
      {
        session_id: 'new-programme',
        title: 'Новая программа',
        status: 'generating',
        updated_at: '2025-10-20T09:00:00Z'
      }
    ]);

    const [program] = service.programs();
    expect(program.type).toBe('Образовательная программа');
    expect(program.direction).toBe('—');
    expect(program.hours).toBe(0);
    expect(program.tags).toEqual([]);
    expect(program.author.name).toBe('');
    expect(program.theme).toBe('it');
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

  it('filters before paginating', () => {
    load();

    const result = service.query({
      type: 'Корпоративное обучение',
      date: '2025-09',
      tags: ['Soft skills']
    });

    expect(result.items.map((program) => program.title)).toEqual([
      'Agile-трансформация команд',
      'Soft skills для руководителей'
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
    expect(program?.hours).toBe(22);
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
    expect(program?.hours).toBe(72);
  });

  it('clears the catalogue', () => {
    load();
    service.clear();

    expect(service.programs()).toEqual([]);
    expect(service.loaded()).toBe(false);
  });

  it('duplicates a program as a draft at the top', () => {
    load();
    const source = service.programs()[0];

    service.duplicate(source.id);

    const [copy] = service.programs();
    expect(service.programs().length).toBe(9);
    expect(copy.id).not.toBe(source.id);
    expect(copy.title).toBe(`${source.title} (копия)`);
    expect(copy.status).toBe('draft');
    expect(copy.tags).toEqual(source.tags);
    expect(copy.tags).not.toBe(source.tags);
    expect(copy.goals).toEqual(source.goals);
    expect(copy.goals).not.toBe(source.goals);
  });

  it('does nothing when duplicating an unknown program', () => {
    load();
    service.duplicate('missing');

    expect(service.programs().length).toBe(8);
  });

  it('archives a program and refreshes its date', () => {
    load();
    const source = service.programs()[0];

    service.archive(source.id);

    const archived = service.programs().find((program) => program.id === source.id);
    expect(archived?.status).toBe('archived');
    expect(archived?.updatedAt).not.toBe(source.updatedAt);
  });

  it('leaves other programs untouched when archiving', () => {
    load();
    const source = service.programs()[0];
    const other = service.programs()[1];

    service.archive(source.id);

    expect(service.programs().find((program) => program.id === other.id)).toEqual(other);
  });

  it('republishes an archived program and refreshes its date', () => {
    load();
    const source = service.programs()[0];
    service.archive(source.id);
    const archived = service.programs().find((program) => program.id === source.id);
    if (!archived) {
      throw new Error('Expected an archived program');
    }

    service.unarchive(source.id);

    const restored = service.programs().find((program) => program.id === source.id);
    expect(restored?.status).toBe('published');
  });

  it('removes a program', () => {
    load();
    const source = service.programs()[0];

    service.remove(source.id);

    expect(service.programs().some((program) => program.id === source.id)).toBe(false);
    expect(service.programs().length).toBe(7);
  });
});
