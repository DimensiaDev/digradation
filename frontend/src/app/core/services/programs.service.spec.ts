import { TestBed } from '@angular/core/testing';
import { ProgramsService } from './programs.service';

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

  it('removes a program', () => {
    const source = service.programs()[0];

    service.remove(source.id);

    expect(service.programs().some((program) => program.id === source.id)).toBe(false);
    expect(service.programs().length).toBe(7);
  });
});
