import { TestBed } from '@angular/core/testing';
import { PROGRAMS_PAGE_SIZE, ProgramsService } from './programs.service';

describe('ProgramsService', () => {
  let service: ProgramsService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(ProgramsService);
  });

  it('starts with the mock catalog', () => {
    expect(service.programs().length).toBe(8);
    expect(service.programs().some((program) => program.status === 'draft')).toBe(true);
    expect(service.programs().some((program) => program.status === 'archived')).toBe(true);
    expect(service.programs()[0].type).toBe('Повышение квалификации');
    expect(service.programs()[0].direction).toBe('IT и аналитика');
    expect(service.programs()[0].goals.length).toBeGreaterThan(0);
  });

  it('paginates the catalog with a server-shaped response', () => {
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

  it('duplicates a program as a draft at the top', () => {
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
    service.duplicate('missing');

    expect(service.programs().length).toBe(8);
  });

  it('archives a program and refreshes its date', () => {
    const source = service.programs().find((program) => program.status === 'published');
    if (!source) {
      throw new Error('Expected a published seed program');
    }

    service.archive(source.id);

    const archived = service.programs().find((program) => program.id === source.id);
    expect(archived?.status).toBe('archived');
    expect(archived?.updatedAt).not.toBe(source.updatedAt);
  });

  it('leaves other programs untouched when archiving', () => {
    const source = service.programs()[0];
    const other = service.programs()[1];

    service.archive(source.id);

    expect(service.programs().find((program) => program.id === other.id)).toEqual(other);
  });

  it('republishes an archived program and refreshes its date', () => {
    const source = service.programs().find((program) => program.status === 'archived');
    if (!source) {
      throw new Error('Expected an archived seed program');
    }

    service.unarchive(source.id);

    const restored = service.programs().find((program) => program.id === source.id);
    expect(restored?.status).toBe('published');
    expect(restored?.updatedAt).not.toBe(source.updatedAt);
  });

  it('removes a program', () => {
    const source = service.programs()[0];

    service.remove(source.id);

    expect(service.programs().some((program) => program.id === source.id)).toBe(false);
    expect(service.programs().length).toBe(7);
  });
});
