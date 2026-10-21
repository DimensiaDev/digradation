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
    type: 'Повышение квалификации',
    direction: 'IT и аналитика',
    hours: 72,
    tags: ['IT', 'Python', 'Аналитика'],
    status: 'published',
    theme: 'it',
    author: { name: 'Иванова А. В.', avatarUrl: null },
    updatedAt: '2025-10-12T09:00:00Z',
    description:
      'Практический курс для специалистов, которые хотят освоить Python и научиться применять его для сбора, очистки и анализа данных.',
    goals: [
      'Уверенно работать с синтаксисом Python и стандартной библиотекой.',
      'Обрабатывать и визуализировать данные с помощью pandas и matplotlib.',
      'Самостоятельно выполнять аналитические проекты от постановки задачи до выводов.'
    ]
  },
  {
    id: 'corp-security',
    title: 'Кибербезопасность для сотрудников',
    type: 'Корпоративное обучение',
    direction: 'Информационная безопасность',
    hours: 40,
    tags: ['IT', 'Безопасность'],
    status: 'in_progress',
    theme: 'security',
    author: { name: 'Петров Д. С.', avatarUrl: null },
    updatedAt: '2025-10-08T09:00:00Z',
    description:
      'Базовый курс по информационной безопасности: от распознавания фишинга до безопасной работы с корпоративными данными и устройствами.',
    goals: [
      'Распознавать типовые схемы социальной инженерии и фишинга.',
      'Соблюдать правила работы с паролями и корпоративными данными.',
      'Действовать по регламенту при подозрении на инцидент.'
    ]
  },
  {
    id: 'ml-basics',
    title: 'Основы машинного обучения',
    type: 'Профессиональная переподготовка',
    direction: 'Искусственный интеллект',
    hours: 256,
    tags: ['ML', 'Python', 'Аналитика'],
    status: 'review',
    theme: 'analytics',
    author: { name: 'Смирнова Е. К.', avatarUrl: null },
    updatedAt: '2025-10-05T09:00:00Z',
    description:
      'Программа для тех, кто хочет системно разобраться в машинном обучении: от подготовки данных и выбора модели до оценки качества решения.',
    goals: [
      'Понимать основные классы задач машинного обучения.',
      'Готовить данные и подбирать признаки для моделей.',
      'Оценивать качество моделей и интерпретировать результаты.'
    ]
  },
  {
    id: 'agile-teams',
    title: 'Agile-трансформация команд',
    type: 'Корпоративное обучение',
    direction: 'Управление и процессы',
    hours: 16,
    tags: ['Agile', 'Soft skills'],
    status: 'published',
    theme: 'agile',
    author: { name: 'Иванова А. В.', avatarUrl: null },
    updatedAt: '2025-09-28T09:00:00Z',
    description:
      'Практикум для команд, которые переходят на Agile и хотят выстроить прозрачные процессы, роли и ритуалы без потери результативности.',
    goals: [
      'Понимать ценности и принципы Agile.',
      'Настраивать роли, ритуалы и артефакты под задачи команды.',
      'Выявлять узкие места процесса и улучшать поток работы.'
    ]
  },
  {
    id: 'project-management',
    title: 'Управление проектами',
    type: 'Повышение квалификации',
    direction: 'Управление и процессы',
    hours: 36,
    tags: ['Менеджмент', 'Agile'],
    status: 'draft',
    theme: 'management',
    author: { name: 'Кузнецов М. А.', avatarUrl: null },
    updatedAt: '2025-09-24T09:00:00Z',
    description:
      'Курс о планировании и реализации проектов: как определить содержание, построить дорожную карту, управлять рисками и коммуникациями.',
    goals: [
      'Формулировать цели проекта и декомпозировать их на задачи.',
      'Планировать сроки, ресурсы и риски.',
      'Выстраивать коммуникацию с командой и заказчиком.'
    ]
  },
  {
    id: 'soft-skills-leaders',
    title: 'Soft skills для руководителей',
    type: 'Корпоративное обучение',
    direction: 'Развитие руководителей',
    hours: 24,
    tags: ['Soft skills', 'Коммуникация'],
    status: 'in_progress',
    theme: 'soft-skills',
    author: { name: 'Соколова Н. В.', avatarUrl: null },
    updatedAt: '2025-09-19T09:00:00Z',
    description:
      'Программа развивает коммуникацию, обратную связь и управление конфликтами — навыки, без которых сложно вести команду.',
    goals: [
      'Проводить сложные разговоры и давать развивающую обратную связь.',
      'Управлять конфликтами и сохранять рабочие отношения.',
      'Поддерживать мотивацию и вовлечённость команды.'
    ]
  },
  {
    id: 'web-development',
    title: 'Веб-разработка: полный цикл',
    type: 'Профессиональная переподготовка',
    direction: 'IT и аналитика',
    hours: 256,
    tags: ['IT', 'JavaScript'],
    status: 'archived',
    theme: 'it',
    author: { name: 'Петров Д. С.', avatarUrl: null },
    updatedAt: '2025-08-30T09:00:00Z',
    description:
      'Полный цикл веб-разработки: вёрстка, клиентская логика, серверная часть и основы развёртывания приложений.',
    goals: [
      'Создавать адаптивные интерфейсы на HTML, CSS и JavaScript.',
      'Разрабатывать клиентскую логику и подключать API.',
      'Собирать, тестировать и разворачивать веб-приложение.'
    ]
  },
  {
    id: 'product-analytics',
    title: 'Продуктовая аналитика',
    type: 'Повышение квалификации',
    direction: 'IT и аналитика',
    hours: 48,
    tags: ['Аналитика', 'Data'],
    status: 'published',
    theme: 'analytics',
    author: { name: 'Смирнова Е. К.', avatarUrl: null },
    updatedAt: '2025-08-22T09:00:00Z',
    description:
      'Курс для продуктовых команд: как выбирать метрики, строить воронки и принимать решения на основе данных, а не догадок.',
    goals: [
      'Выбирать продуктовые метрики и формулировать гипотезы.',
      'Строить воронки и сегменты для анализа поведения пользователей.',
      'Превращать данные в решения и понятные выводы для команды.'
    ]
  }
];

function cloneProgram(program: Program): Program {
  return {
    ...program,
    tags: [...program.tags],
    goals: [...program.goals],
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
        (type === 'all' || program.type === type) &&
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

  unarchive(id: string): void {
    this.state.update((programs) =>
      programs.map((program) => {
        if (program.id !== id) {
          return program;
        }

        return {
          ...cloneProgram(program),
          status: 'published',
          updatedAt: new Date().toISOString()
        };
      })
    );
  }

  remove(id: string): void {
    this.state.update((programs) => programs.filter((program) => program.id !== id));
  }
}
