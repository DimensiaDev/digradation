import {
  PROGRAM_STATUS_LABELS,
  ProgramBlocksApi,
  ProgramCurriculumApi,
  ProgramStateApi
} from '../../core/models/models';
import { formatProgramHours } from '../../shared/utils/program-detail-content';

export type ProgramWizardStep = 1 | 2 | 3 | 4 | 5;
export type ProgramStepState = 'completed' | 'active' | 'pending' | 'failed';
export type ProgramPreviewSection = 'summary' | 'competencies' | 'modules';
export type ProgramBadgeTone = 'neutral' | 'progress' | 'success' | 'danger';

export interface ProgramWizardStepView {
  readonly id: ProgramWizardStep;
  readonly label: string;
}

export interface ProgramModuleView {
  readonly id: string;
  readonly title: string;
  readonly hours: number;
  readonly topicCount: number;
  readonly topics: readonly string[];
}

export interface ProgramBadge {
  readonly label: string;
  readonly tone: ProgramBadgeTone;
}

export const PROGRAM_WIZARD_STEPS: readonly ProgramWizardStepView[] = [
  { id: 1, label: 'Запрос' },
  { id: 2, label: 'Структура и каркас' },
  { id: 3, label: 'Содержание и материалы' },
  { id: 4, label: 'Проверка' },
  { id: 5, label: 'Готово' }
];

export const PROGRAM_STAGE_LABELS: Readonly<Record<string, string>> = {
  competences: 'компетенции',
  hours: 'учебный план',
  sources: 'источники',
  summary: 'паспорт программы',
  assessment: 'аттестация',
  materials: 'материалы'
};

const CONTENT_STAGES = new Set(['sources', 'summary', 'assessment', 'materials']);

function hasDraftData(blocks: ProgramBlocksApi | null | undefined): boolean {
  return Boolean(
    blocks?.title ||
    blocks?.competencies.length ||
    blocks?.curriculum ||
    blocks?.summary ||
    blocks?.sources.length
  );
}

export function programStep(state: ProgramStateApi | null): ProgramWizardStep {
  if (!state || state.status === 'none' || state.status === 'awaiting_answers') {
    return 1;
  }

  if (state.status === 'generating' || state.status === 'failed') {
    if (state.current_stage && CONTENT_STAGES.has(state.current_stage)) {
      return 3;
    }
    if (state.current_stage === 'hours') {
      return 2;
    }
    return state.current_stage ? 2 : 1;
  }

  if (state.status === 'pending_approval') {
    return 4;
  }

  if (state.status === 'approved' || state.status === 'verified') {
    return 5;
  }

  return 1;
}

export function programStepState(
  state: ProgramStateApi | null,
  step: ProgramWizardStep
): ProgramStepState {
  const active = programStep(state);
  if (step < active) {
    return 'completed';
  }
  if (step > active) {
    return 'pending';
  }
  return state?.status === 'failed' ? 'failed' : 'active';
}

export function programBadge(state: ProgramStateApi | null): ProgramBadge {
  if (!state || state.status === 'none') {
    return { label: 'Ожидает данных', tone: 'neutral' };
  }

  switch (state.status) {
    case 'awaiting_answers':
      return { label: 'Нужны ответы', tone: 'progress' };
    case 'generating':
      return hasDraftData(state.draft)
        ? { label: 'Черновик обновлён', tone: 'progress' }
        : { label: 'Черновик собирается', tone: 'progress' };
    case 'pending_approval':
    case 'approved':
    case 'verified':
      return { label: 'Черновик обновлён', tone: 'success' };
    case 'failed':
      return { label: 'Ошибка сборки', tone: 'danger' };
    default:
      return { label: 'Черновик', tone: 'neutral' };
  }
}

export function programStatusLabel(state: ProgramStateApi | null): string {
  return state ? PROGRAM_STATUS_LABELS[state.status] : PROGRAM_STATUS_LABELS.none;
}

export function programStatusHint(state: ProgramStateApi | null): string {
  if (!state || state.status === 'none') {
    return 'Опишите программу в чате, чтобы запустить сборку каркаса.';
  }

  switch (state.status) {
    case 'awaiting_answers':
      return 'Ответьте на вопросы агента, чтобы продолжить.';
    case 'generating': {
      const stage = state.current_stage ? PROGRAM_STAGE_LABELS[state.current_stage] : null;
      return stage ? `Сейчас собираем ${stage}.` : 'Программа собирается по этапам.';
    }
    case 'pending_approval':
      return 'Проверьте каркас и подтвердите программу в чате.';
    case 'approved':
    case 'verified':
      return 'Программа утверждена.';
    case 'failed':
      return state.error?.trim() || 'Не удалось собрать программу.';
    default:
      return 'Черновик готов к продолжению.';
  }
}

export function programKindLabel(state: ProgramStateApi | null): string | null {
  switch (state?.draft.kind) {
    case 'professional_development':
      return 'Повышение квалификации';
    case 'general_development':
      return 'Общеразвивающая программа';
    default:
      return null;
  }
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

export function programHours(curriculum: ProgramCurriculumApi | null | undefined): number {
  if (!curriculum) {
    return 0;
  }
  return curriculum.modules.reduce(
    (total, module) => total + module.topics.reduce((sum, topic) => sum + topicHours(topic), 0),
    0
  );
}

export function programHoursLabel(hours: number): string {
  return hours > 0 ? formatProgramHours(hours) : '—';
}

export function programTopicCountLabel(count: number): string {
  const mod100 = count % 100;
  const mod10 = count % 10;

  if (mod100 >= 11 && mod100 <= 14) {
    return `${count} тем`;
  }
  if (mod10 === 1) {
    return `${count} тема`;
  }
  if (mod10 >= 2 && mod10 <= 4) {
    return `${count} темы`;
  }
  return `${count} тем`;
}

export function programModules(
  curriculum: ProgramCurriculumApi | null | undefined
): readonly ProgramModuleView[] {
  if (!curriculum) {
    return [];
  }

  return curriculum.modules.map((module) => ({
    id: module.id,
    title: module.title,
    hours: module.topics.reduce((total, topic) => total + topicHours(topic), 0),
    topicCount: module.topics.length,
    topics: module.topics.map((topic) => topic.title)
  }));
}

export function programFormLabel(state: ProgramStateApi | null): string {
  return state?.draft.summary?.form?.trim() || '—';
}

export function programGoal(state: ProgramStateApi | null): string | null {
  return state?.draft.summary?.goal?.trim() || null;
}

export function programCompetencies(
  state: ProgramStateApi | null
): readonly { readonly id: string; readonly label: string }[] {
  return (
    state?.draft.competencies.map((competence) => ({
      id: competence.id,
      label: competence.statement
    })) ?? []
  );
}
