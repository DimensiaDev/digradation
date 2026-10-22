import {
  PROGRAM_STATUS_LABELS,
  ProgramBlocksApi,
  ProgramCurriculumApi,
  ProgramRevisionApi,
  ProgramSourceKind,
  ProgramStage,
  ProgramStateApi
} from '../../core/models/models';
import { formatProgramHours } from '../../shared/utils/program-detail-content';

export type ProgramWizardStep = 1 | 2 | 3 | 4 | 5;
export type ProgramStepState = 'completed' | 'active' | 'pending' | 'failed';
export type ProgramPreviewSection =
  'summary' | 'competencies' | 'modules' | 'sources' | 'report' | 'files' | 'results';
/**
 * Every region of the aside that can flash when the run writes to it: the
 * accordion sections, and the blocks that are not accordions — the metadata row
 * above them and the pinned results footer.
 */
export type ProgramHighlightSection = ProgramPreviewSection | 'meta';
export type ProgramDiffableSection = Exclude<ProgramHighlightSection, 'files'>;
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

export interface ProgramSourceView {
  readonly id: string;
  readonly title: string;
  readonly kindLabel: string;
  readonly confirmed: boolean;
  readonly trust: string | null;
  readonly origin: string | null;
}

export interface ProgramCheckDetailView {
  readonly label: string;
  readonly value: string;
}

export interface ProgramCheckView {
  readonly name: string;
  readonly label: string;
  readonly ok: boolean;
  readonly problems: readonly string[];
  readonly details: readonly ProgramCheckDetailView[];
}

export interface ProgramReportView {
  readonly ok: boolean;
  readonly passed: number;
  readonly checks: readonly ProgramCheckView[];
}

export const PROGRAM_SOURCE_KIND_LABELS: Record<ProgramSourceKind, string> = {
  normative_act: 'Нормативный акт',
  professional_standard: 'Профессиональный стандарт',
  literature: 'Литература',
  internet_resource: 'Интернет-ресурс',
  reference: 'Справочное издание'
};

const PROGRAM_CHECK_LABELS: Record<string, string> = {
  часы: 'Часы',
  покрытие: 'Покрытие результатов',
  источники: 'Источники',
  разделы: 'Разделы программы'
};

const PROGRAM_DETAIL_LABELS: Record<string, string> = {
  заявлено: 'Заявлено',
  всего: 'Всего',
  темы: 'Темы',
  'итоговая аттестация': 'Итоговая аттестация',
  аудиторных: 'Аудиторных',
  'самостоятельной работы': 'Самостоятельная работа',
  'результатов обучения': 'Результатов обучения',
  'раскрыто в темах': 'Раскрыто в темах',
  'проверяется двумя средствами': 'Проверяется двумя средствами',
  'всего источников': 'Всего источников',
  подтверждено: 'Подтверждено',
  'не подтверждено': 'Не подтверждено',
  компетенции: 'Компетенции',
  'учебный план': 'Учебный план',
  паспорт: 'Паспорт',
  источники: 'Источники',
  'оценочные средства': 'Оценочные средства',
  материалы: 'Материалы'
};

export const PROGRAM_WIZARD_STEPS: readonly ProgramWizardStepView[] = [
  { id: 1, label: 'Запрос' },
  { id: 2, label: 'Каркас программы' },
  { id: 3, label: 'Согласование каркаса' },
  { id: 4, label: 'Содержание и материалы' },
  { id: 5, label: 'Готово' }
];

const SKELETON_STAGES = new Set(['competences', 'hours', 'sources', 'summary']);
const CONTENT_STAGES = new Set(['assessment', 'materials']);

/** The skeleton the methodist approves, in the order the pipeline writes it. */
export const PROGRAM_SKELETON_STAGE_ORDER: readonly ProgramStage[] = [
  'competences',
  'hours',
  'sources',
  'summary'
];

/** The content written from an approved skeleton. */
export const PROGRAM_CONTENT_STAGE_ORDER: readonly ProgramStage[] = ['assessment', 'materials'];

export const PROGRAM_STAGE_ORDER: readonly ProgramStage[] = [
  ...PROGRAM_SKELETON_STAGE_ORDER,
  ...PROGRAM_CONTENT_STAGE_ORDER
];

export const PROGRAM_STAGE_LABELS: Record<ProgramStage, string> = {
  competences: 'Компетенции',
  hours: 'Учебный план и часы',
  assessment: 'Оценочные средства',
  materials: 'Материалы',
  sources: 'Источники',
  summary: 'Паспорт программы'
};

export interface ProgramDecisionBlock {
  readonly id: ProgramStage;
  readonly label: string;
}

export interface ProgramRevisionView {
  readonly stageLabel: string;
  readonly notes: string;
}

/** The decision the methodist owes the programme, when one is due. */
export interface ProgramDecisionView {
  /** The blocks that already exist and may be asked to be redone. */
  readonly blocks: readonly ProgramDecisionBlock[];
  /** The last change that was carried out, if there was one. */
  readonly lastRevision: ProgramRevisionView | null;
}

export function programStageLabel(stage: ProgramStage): string {
  return PROGRAM_STAGE_LABELS[stage] ?? stage;
}

export function programRevisionView(
  revision: ProgramRevisionApi | null | undefined
): ProgramRevisionView | null {
  if (!revision) {
    return null;
  }
  return { stageLabel: programStageLabel(revision.stage), notes: revision.notes };
}

/**
 * The decision a programme is waiting for, or null when none is due.
 *
 * Only a programme whose skeleton is rendered and which is not being worked on
 * asks the methodist anything: `busy` is false once the run stops to wait, and
 * the content cannot be released until the draft has been approved.
 */
export function programDecision(state: ProgramStateApi | null): ProgramDecisionView | null {
  if (!state || state.status !== 'pending_approval' || state.busy) {
    return null;
  }

  const blocks = PROGRAM_STAGE_ORDER.filter((stage) => state.stages[stage] === 'completed').map(
    (stage) => ({ id: stage, label: programStageLabel(stage) })
  );

  return { blocks, lastRevision: programRevisionView(state.last_revision) };
}

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
      return 4;
    }
    if (state.current_stage && SKELETON_STAGES.has(state.current_stage)) {
      return 2;
    }
    return state.current_stage ? 2 : 1;
  }

  if (state.status === 'pending_approval') {
    return 3;
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

/**
 * The content that decides whether a block of the aside changed. A stage
 * rewrites its own block and bumps the version, so the version says that
 * something moved but not which block, and only a comparison of the blocks
 * themselves can tell the methodist where to look.
 */
const PROGRAM_BLOCK_SIGNATURES: Record<
  ProgramDiffableSection,
  (state: ProgramStateApi | null) => string
> = {
  meta: (state) =>
    JSON.stringify([programHours(state?.draft.curriculum), state?.draft.summary?.form ?? null]),
  summary: (state) =>
    JSON.stringify([
      state?.draft.summary?.goal ?? null,
      state?.draft.summary?.tasks ?? [],
      state?.draft.summary?.target_audience ?? null
    ]),
  competencies: (state) => JSON.stringify(state?.draft.competencies ?? []),
  modules: (state) => JSON.stringify(state?.draft.curriculum ?? null),
  sources: (state) => JSON.stringify(state?.draft.sources ?? []),
  report: (state) => JSON.stringify(state?.report ?? null),
  results: (state) => JSON.stringify(state?.exports ?? null)
};

export const PROGRAM_DIFFABLE_SECTIONS = Object.keys(
  PROGRAM_BLOCK_SIGNATURES
) as readonly ProgramDiffableSection[];

export function programChangedSections(
  previous: ProgramStateApi | null,
  next: ProgramStateApi | null
): readonly ProgramDiffableSection[] {
  if (previous === null || next === null) {
    return [];
  }

  return PROGRAM_DIFFABLE_SECTIONS.filter(
    (section) =>
      PROGRAM_BLOCK_SIGNATURES[section](previous) !== PROGRAM_BLOCK_SIGNATURES[section](next)
  );
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
  return (
    curriculum.modules.reduce(
      (total, module) => total + module.topics.reduce((sum, topic) => sum + topicHours(topic), 0),
      0
    ) + (curriculum.attestation_hours ?? 0)
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

export function programSources(state: ProgramStateApi | null): readonly ProgramSourceView[] {
  return (
    state?.draft.sources.map((source) => ({
      id: source.id,
      title: source.title.trim(),
      kindLabel: PROGRAM_SOURCE_KIND_LABELS[source.kind] ?? source.kind,
      confirmed: source.confirmed,
      trust: source.trust?.trim() || null,
      origin: source.origin?.trim() || null
    })) ?? []
  );
}

export function programReport(state: ProgramStateApi | null): ProgramReportView | null {
  const report = state?.report;
  if (!report) {
    return null;
  }
  const checks = report.checks.map((check) => ({
    name: check.name,
    label: PROGRAM_CHECK_LABELS[check.name] ?? capitalize(check.name),
    ok: check.ok,
    problems: [...check.problems],
    details: Object.entries(check.details)
      .map(([key, value]) => {
        const formatted = formatReportDetail(check.name, value);
        return formatted === null
          ? null
          : { label: PROGRAM_DETAIL_LABELS[key] ?? capitalize(key), value: formatted };
      })
      .filter((detail): detail is ProgramCheckDetailView => detail !== null)
  }));
  return {
    ok: report.ok,
    passed: checks.filter((check) => check.ok).length,
    checks
  };
}

function formatReportDetail(checkName: string, value: unknown): string | null {
  if (typeof value === 'boolean') {
    return value ? 'есть' : 'нет';
  }
  if (typeof value === 'number') {
    return checkName === 'часы'
      ? formatProgramHours(value)
      : value.toLocaleString('ru-RU', { maximumFractionDigits: 2 });
  }
  if (typeof value === 'string') {
    return value.trim() || null;
  }
  return null;
}

function capitalize(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
}
