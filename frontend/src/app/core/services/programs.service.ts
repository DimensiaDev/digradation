import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Subscription, catchError, of, timer } from 'rxjs';
import { environment } from '../../../environments/environment';
import {
  PaginatedResponse,
  Program,
  ProgramApiItem,
  ProgramCurriculumApi,
  ProgramKind,
  ProgramModule,
  ProgramPageApi,
  ProgramStateApi,
  ProgramStatus,
  ProgramTheme
} from '../models/models';
import { createId } from '../../shared/utils/create-id';
import { ProfileService } from './profile.service';

export const PROGRAMS_PAGE_SIZE = 6;

/** The catalogue is fetched in as few requests as the endpoint allows. */
export const PROGRAMS_FETCH_PAGE_SIZE = 100;

/** How often a programme that is still being generated is refreshed. */
export const PROGRAM_POLL_INTERVAL_MS = 2_000;

export interface ProgramsQuery {
  page?: number;
  itemsPerPage?: number;
  type?: string;
  date?: string;
  tags?: readonly string[];
}

const PROGRAM_KIND_LABELS: Record<ProgramKind, string> = {
  professional_development: 'Повышение квалификации',
  general_development: 'Общеразвивающая программа'
};

const PROGRAM_KIND_THEMES: Record<ProgramKind, ProgramTheme> = {
  professional_development: 'it',
  general_development: 'soft-skills'
};

function kindLabel(kind: ProgramKind | null | undefined): string | null {
  return kind ? PROGRAM_KIND_LABELS[kind] : null;
}

function themeForKind(kind: ProgramKind | null | undefined): ProgramTheme {
  return kind ? PROGRAM_KIND_THEMES[kind] : 'it';
}

function nonEmpty(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

function topicHours(topic: {
  hours: { lecture?: number; practice?: number; lab?: number; self_study?: number };
}): number {
  return (
    (topic.hours.lecture ?? 0) +
    (topic.hours.practice ?? 0) +
    (topic.hours.lab ?? 0) +
    (topic.hours.self_study ?? 0)
  );
}

function curriculumHours(curriculum: ProgramCurriculumApi | null): number {
  if (!curriculum) {
    return 0;
  }
  return curriculum.modules.reduce(
    (total, module) => total + module.topics.reduce((sum, topic) => sum + topicHours(topic), 0),
    0
  );
}

function curriculumModules(curriculum: ProgramCurriculumApi | null): readonly ProgramModule[] {
  if (!curriculum) {
    return [];
  }
  return curriculum.modules.map((module) => ({
    id: module.id,
    title: module.title,
    summary: module.topics.map((topic) => topic.title).join(' · '),
    hours: module.topics.reduce((total, topic) => total + topicHours(topic), 0)
  }));
}

function cloneProgram(program: Program): Program {
  return {
    ...program,
    tags: [...program.tags],
    goals: [...program.goals],
    author: { ...program.author },
    competencyStatements: program.competencyStatements
      ? [...program.competencyStatements]
      : undefined,
    modules: program.modules ? program.modules.map((module) => ({ ...module })) : undefined
  };
}

@Injectable({
  providedIn: 'root'
})
export class ProgramsService {
  private readonly http = inject(HttpClient);
  private readonly profile = inject(ProfileService);
  private readonly baseUrl = `${environment.apiUrl}/chats`;

  private readonly state = signal<readonly Program[]>([]);
  private readonly loadingState = signal(false);
  private readonly loadedState = signal(false);
  private readonly errorState = signal<string | null>(null);

  readonly programs = this.state.asReadonly();
  readonly loading = this.loadingState.asReadonly();
  readonly loaded = this.loadedState.asReadonly();
  readonly error = this.errorState.asReadonly();

  private loadRequestId = 0;
  private detailRequestId = 0;
  private detailPoll: Subscription | null = null;

  load(): void {
    const requestId = ++this.loadRequestId;
    this.loadingState.set(true);
    this.errorState.set(null);
    this.fetchPage(0, requestId, []);
  }

  clear(): void {
    this.loadRequestId++;
    this.stopDetail();
    this.state.set([]);
    this.loadedState.set(false);
    this.loadingState.set(false);
    this.errorState.set(null);
  }

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

  loadDetail(id: string): void {
    this.stopDetail();
    const requestId = ++this.detailRequestId;
    this.fetchDetail(id, requestId);
  }

  stopDetail(): void {
    this.detailRequestId++;
    this.detailPoll?.unsubscribe();
    this.detailPoll = null;
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

  private fetchPage(page: number, requestId: number, collected: ProgramApiItem[]): void {
    const params = new HttpParams()
      .set('page', page)
      .set('items_per_page', PROGRAMS_FETCH_PAGE_SIZE);

    this.http
      .get<ProgramPageApi>(`${this.baseUrl}/programmes`, { params })
      .pipe(catchError(() => of(null)))
      .subscribe((response) => {
        if (requestId !== this.loadRequestId) {
          return;
        }
        if (!response) {
          this.loadingState.set(false);
          this.errorState.set('Не удалось загрузить программы');
          return;
        }

        const items = [...collected, ...response.items];
        if (response.items.length === 0 || items.length >= response.total) {
          this.state.set(items.map((item) => this.toProgram(item)));
          this.loadedState.set(true);
          this.loadingState.set(false);
          this.errorState.set(null);
          return;
        }

        this.fetchPage(page + 1, requestId, items);
      });
  }

  private fetchDetail(id: string, requestId: number): void {
    this.http
      .get<ProgramStateApi>(`${this.baseUrl}/${id}/program`)
      .pipe(catchError(() => of(null)))
      .subscribe((response) => {
        if (requestId !== this.detailRequestId || response === null) {
          return;
        }
        if (!this.state().some((program) => program.id === id)) {
          return;
        }

        this.applyDetail(id, response);
        if (response.busy) {
          this.detailPoll = timer(PROGRAM_POLL_INTERVAL_MS).subscribe(() =>
            this.fetchDetail(id, requestId)
          );
        }
      });
  }

  private applyDetail(id: string, response: ProgramStateApi): void {
    const draft = response.draft;
    const summary = draft.summary;
    const hours = curriculumHours(draft.curriculum);
    const modules = curriculumModules(draft.curriculum);
    const competencies = draft.competencies.map((competence) => competence.statement);
    const goals = summary?.tasks.length ? summary.tasks : summary?.goal ? [summary.goal] : [];

    this.state.update((programs) =>
      programs.map((program) => {
        if (program.id !== id) {
          return program;
        }

        return {
          ...program,
          title: nonEmpty(draft.title) ?? program.title,
          status: response.status === 'none' ? program.status : response.status,
          updatedAt: response.updated_at ?? program.updatedAt,
          type: kindLabel(draft.kind) ?? program.type,
          direction: nonEmpty(summary?.focus) ?? program.direction,
          hours: hours > 0 ? hours : program.hours,
          description:
            nonEmpty(summary?.annotation) ??
            nonEmpty(summary?.relevance) ??
            nonEmpty(summary?.goal) ??
            program.description,
          goals: goals.length > 0 ? [...goals] : program.goals,
          modules: modules.length > 0 ? modules : program.modules,
          competencyStatements:
            competencies.length > 0 ? competencies : program.competencyStatements,
          busy: response.busy,
          error: response.error
        };
      })
    );
  }

  private toProgram(item: ProgramApiItem): Program {
    return {
      id: item.session_id,
      title: item.title,
      type: item.type ?? kindLabel(item.kind) ?? 'Образовательная программа',
      direction: item.direction ?? '—',
      hours: item.hours ?? 0,
      tags: item.tags ?? [],
      status: item.status === 'none' ? 'draft' : (item.status as ProgramStatus),
      theme: item.theme ?? themeForKind(item.kind),
      author: item.author ?? this.currentAuthor(),
      updatedAt: item.updated_at ?? '',
      description: item.description ?? '',
      goals: item.goals ?? []
    };
  }

  private currentAuthor(): { name: string; avatarUrl: string | null } {
    const user = this.profile.user();
    if (!user) {
      return { name: '', avatarUrl: null };
    }
    const name = [user.first_name, user.last_name].filter(Boolean).join(' ') || user.username;
    return { name, avatarUrl: null };
  }
}
