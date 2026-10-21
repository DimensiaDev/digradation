import { Injectable, signal } from '@angular/core';
import { PaginatedResponse, Program } from '../models/models';
import { createId } from '../../shared/utils/create-id';

export const PROGRAMS_PAGE_SIZE = 6;

export interface ProgramsQuery {
  page?: number;
  itemsPerPage?: number;
  type?: string;
  date?: string;
  tags?: readonly string[];
}

const MOCK_PROGRAMS: readonly Program[] = [
  {
    id: 'it-python',
    title: 'Python для анализа данных',
    direction: 'Повышение квалификации',
    hours: 72,
    tags: ['IT', 'Python', 'Аналитика'],
    status: 'published',
    theme: 'it',
    author: { name: 'Иванова А. В.', avatarUrl: null },
    updatedAt: '2025-10-12T09:00:00Z'
  },
  {
    id: 'corp-security',
    title: 'Кибербезопасность для сотрудников',
    direction: 'Корпоративное обучение',
    hours: 40,
    tags: ['IT', 'Безопасность'],
    status: 'in_progress',
    theme: 'security',
    author: { name: 'Петров Д. С.', avatarUrl: null },
    updatedAt: '2025-10-08T09:00:00Z'
  },
  {
    id: 'ml-basics',
    title: 'Основы машинного обучения',
    direction: 'Профессиональная переподготовка',
    hours: 256,
    tags: ['ML', 'Python', 'Аналитика'],
    status: 'review',
    theme: 'analytics',
    author: { name: 'Смирнова Е. К.', avatarUrl: null },
    updatedAt: '2025-10-05T09:00:00Z'
  },
  {
    id: 'agile-teams',
    title: 'Agile-трансформация команд',
    direction: 'Корпоративное обучение',
    hours: 16,
    tags: ['Agile', 'Soft skills'],
    status: 'published',
    theme: 'agile',
    author: { name: 'Иванова А. В.', avatarUrl: null },
    updatedAt: '2025-09-28T09:00:00Z'
  },
  {
    id: 'project-management',
    title: 'Управление проектами',
    direction: 'Повышение квалификации',
    hours: 36,
    tags: ['Менеджмент', 'Agile'],
    status: 'draft',
    theme: 'management',
    author: { name: 'Кузнецов М. А.', avatarUrl: null },
    updatedAt: '2025-09-24T09:00:00Z'
  },
  {
    id: 'soft-skills-leaders',
    title: 'Soft skills для руководителей',
    direction: 'Корпоративное обучение',
    hours: 24,
    tags: ['Soft skills', 'Коммуникация'],
    status: 'in_progress',
    theme: 'soft-skills',
    author: { name: 'Соколова Н. В.', avatarUrl: null },
    updatedAt: '2025-09-19T09:00:00Z'
  },
  {
    id: 'web-development',
    title: 'Веб-разработка: полный цикл',
    direction: 'Профессиональная переподготовка',
    hours: 256,
    tags: ['IT', 'JavaScript'],
    status: 'archived',
    theme: 'it',
    author: { name: 'Петров Д. С.', avatarUrl: null },
    updatedAt: '2025-08-30T09:00:00Z'
  },
  {
    id: 'product-analytics',
    title: 'Продуктовая аналитика',
    direction: 'Повышение квалификации',
    hours: 48,
    tags: ['Аналитика', 'Data'],
    status: 'published',
    theme: 'analytics',
    author: { name: 'Смирнова Е. К.', avatarUrl: null },
    updatedAt: '2025-08-22T09:00:00Z'
  }
];

function cloneProgram(program: Program): Program {
  return {
    ...program,
    tags: [...program.tags],
    author: { ...program.author }
  };
}

@Injectable({
  providedIn: 'root'
})
export class ProgramsService {
  private readonly state = signal<readonly Program[]>(MOCK_PROGRAMS.map(cloneProgram));

  readonly programs = this.state.asReadonly();

  query(query: ProgramsQuery = {}): PaginatedResponse<Program> {
    const page = Math.max(0, query.page ?? 0);
    const itemsPerPage = query.itemsPerPage ?? PROGRAMS_PAGE_SIZE;
    const type = query.type ?? 'all';
    const date = query.date ?? 'all';
    const tags = query.tags ?? [];

    const filtered = this.state().filter(
      (program) =>
        (type === 'all' || program.direction === type) &&
        (date === 'all' || program.updatedAt.startsWith(date)) &&
        (tags.length === 0 || tags.some((tag) => program.tags.includes(tag)))
    );
    const start = page * itemsPerPage;

    return {
      items: filtered.slice(start, start + itemsPerPage),
      total: filtered.length,
      page,
      items_per_page: itemsPerPage
    };
  }

  duplicate(id: string): void {
    this.state.update((programs) => {
      const source = programs.find((program) => program.id === id);
      if (!source) {
        return programs;
      }

      const copy: Program = {
        ...cloneProgram(source),
        id: createId(),
        title: `${source.title} (копия)`,
        status: 'draft',
        updatedAt: new Date().toISOString()
      };
      return [copy, ...programs];
    });
  }

  archive(id: string): void {
    this.state.update((programs) =>
      programs.map((program) => {
        if (program.id !== id) {
          return program;
        }

        const archived: Program = {
          ...cloneProgram(program),
          status: 'archived',
          updatedAt: new Date().toISOString()
        };
        return archived;
      })
    );
  }

  remove(id: string): void {
    this.state.update((programs) => programs.filter((program) => program.id !== id));
  }
}
