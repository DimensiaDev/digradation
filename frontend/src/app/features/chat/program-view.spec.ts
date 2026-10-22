import { ProgramStateApi } from '../../core/models/models';
import {
  programBadge,
  programChangedSections,
  programCompetencies,
  programFormLabel,
  programGoal,
  programHours,
  programHoursLabel,
  programKindLabel,
  programModules,
  programReport,
  programSources,
  programStatusLabel,
  programStep,
  programTopicCountLabel,
  programStepState
} from './program-view';

function state(overrides: Partial<ProgramStateApi> = {}): ProgramStateApi {
  return {
    status: 'none',
    current_stage: null,
    stages: {},
    running: false,
    busy: false,
    error: null,
    updated_at: null,
    version: 0,
    questions: [],
    draft: {
      kind: null,
      title: null,
      competencies: [],
      curriculum: null,
      sources: [],
      summary: null
    },
    report: null,
    exports: null,
    revision: null,
    last_revision: null,
    approved_by: null,
    approved_at: null,
    ...overrides
  };
}

const CURRICULUM = {
  modules: [
    {
      id: 'module-1',
      title: 'Введение',
      topics: [
        { id: 'topic-1', title: 'Понятия', hours: { lecture: 4, practice: 2 } },
        { id: 'topic-2', title: 'Практика', hours: { lab: 2, self_study: 4 } }
      ]
    },
    {
      id: 'module-2',
      title: 'Итоги',
      topics: [{ id: 'topic-3', title: 'Защита', hours: { practice: 6 } }]
    }
  ]
};

describe('program-view', () => {
  it('maps the run status and stage onto the wizard step', () => {
    expect(programStep(null)).toBe(1);
    expect(programStep(state())).toBe(1);
    expect(programStep(state({ status: 'awaiting_answers' }))).toBe(1);
    expect(programStep(state({ status: 'draft' }))).toBe(1);
    expect(programStep(state({ status: 'generating', current_stage: 'competences' }))).toBe(2);
    expect(programStep(state({ status: 'generating', current_stage: 'hours' }))).toBe(2);
    expect(programStep(state({ status: 'generating', current_stage: 'sources' }))).toBe(2);
    expect(programStep(state({ status: 'generating', current_stage: 'summary' }))).toBe(2);
    expect(programStep(state({ status: 'generating', current_stage: 'assessment' }))).toBe(4);
    expect(programStep(state({ status: 'generating', current_stage: 'materials' }))).toBe(4);
    expect(programStep(state({ status: 'pending_approval' }))).toBe(3);
    expect(programStep(state({ status: 'approved' }))).toBe(5);
    expect(programStep(state({ status: 'verified' }))).toBe(5);
    expect(programStep(state({ status: 'failed', current_stage: 'summary' }))).toBe(2);
    expect(programStep(state({ status: 'failed', current_stage: 'materials' }))).toBe(4);
    expect(programStep(state({ status: 'failed' }))).toBe(1);
  });

  it('marks completed, active and failed steps', () => {
    const running = state({ status: 'generating', current_stage: 'hours' });

    expect(programStepState(running, 1)).toBe('completed');
    expect(programStepState(running, 2)).toBe('active');
    expect(programStepState(running, 3)).toBe('pending');
    expect(programStepState(state({ status: 'failed', current_stage: 'hours' }), 2)).toBe('failed');
    expect(programStepState(null, 1)).toBe('active');
  });

  it('labels the draft badge for every meaningful state', () => {
    expect(programBadge(null)).toEqual({ label: 'Ожидает данных', tone: 'neutral' });
    expect(programBadge(state({ status: 'awaiting_answers' }))).toEqual({
      label: 'Нужны ответы',
      tone: 'progress'
    });
    expect(programBadge(state({ status: 'generating' }))).toEqual({
      label: 'Черновик собирается',
      tone: 'progress'
    });
    expect(
      programBadge(
        state({
          status: 'generating',
          draft: {
            kind: null,
            title: 'Программа',
            competencies: [],
            curriculum: null,
            sources: [],
            summary: null
          }
        })
      )
    ).toEqual({ label: 'Черновик обновлён', tone: 'progress' });
    expect(programBadge(state({ status: 'pending_approval' }))).toEqual({
      label: 'Черновик обновлён',
      tone: 'success'
    });
    expect(programBadge(state({ status: 'approved' }))).toEqual({
      label: 'Черновик обновлён',
      tone: 'success'
    });
    expect(programBadge(state({ status: 'failed' }))).toEqual({
      label: 'Ошибка сборки',
      tone: 'danger'
    });
    expect(programBadge(state({ status: 'draft' }))).toEqual({
      label: 'Черновик',
      tone: 'neutral'
    });
  });

  it('labels the run status', () => {
    expect(programStatusLabel(null)).toBe('Нет программы');
    expect(programStatusLabel(state({ status: 'pending_approval' }))).toBe('На согласовании');
  });

  it('names only the blocks that changed between two states', () => {
    const before = state({
      draft: {
        ...state().draft,
        title: 'Аналитика данных',
        curriculum: CURRICULUM
      }
    });
    const afterModules = state({
      version: 1,
      draft: {
        ...before.draft,
        curriculum: { ...CURRICULUM, attestation_hours: 2 }
      }
    });

    expect(programChangedSections(before, afterModules)).toEqual(['meta', 'modules']);
    expect(programChangedSections(before, before)).toEqual([]);

    const afterSources = state({
      version: 1,
      draft: {
        ...before.draft,
        sources: [
          {
            id: 'src-01',
            kind: 'normative_act',
            title: 'ФГОС',
            confirmed: true,
            trust: null,
            origin: null
          }
        ]
      }
    });
    expect(programChangedSections(before, afterSources)).toEqual(['sources']);
  });

  it('separates the goal from the metadata and the exports', () => {
    const filled = state({
      draft: {
        ...state().draft,
        title: 'Аналитика данных',
        curriculum: CURRICULUM,
        summary: {
          goal: 'Подготовить аналитиков',
          tasks: [],
          target_audience: '',
          form: 'Очная'
        }
      }
    });

    const afterForm = state({
      version: 1,
      draft: {
        ...filled.draft,
        summary: { ...filled.draft.summary!, form: 'Заочная' }
      }
    });
    const afterGoal = state({
      version: 1,
      draft: {
        ...filled.draft,
        summary: { ...filled.draft.summary!, goal: 'Другая цель' }
      }
    });
    const afterExports = state({
      ...filled,
      version: 1,
      exports: { document_attachment_id: 7, slides_attachment_id: null }
    });

    expect(programChangedSections(filled, afterForm)).toEqual(['meta']);
    expect(programChangedSections(filled, afterGoal)).toEqual(['summary']);
    expect(programChangedSections(filled, afterExports)).toEqual(['results']);
  });

  it('reports nothing to highlight without two states to compare', () => {
    expect(programChangedSections(null, state())).toEqual([]);
    expect(programChangedSections(state(), null)).toEqual([]);
  });

  it('sums curriculum hours and exposes modules', () => {
    expect(programHours(undefined)).toBe(0);
    expect(programHours(CURRICULUM)).toBe(18);
    expect(programHours({ ...CURRICULUM, attestation_hours: 2 })).toBe(20);
    expect(programHoursLabel(0)).toBe('—');
    expect(programHoursLabel(18)).toBe('18 часов');
    expect(programTopicCountLabel(1)).toBe('1 тема');
    expect(programTopicCountLabel(2)).toBe('2 темы');
    expect(programTopicCountLabel(5)).toBe('5 тем');
    expect(programTopicCountLabel(11)).toBe('11 тем');
    expect(programTopicCountLabel(21)).toBe('21 тема');
    expect(programModules(null)).toEqual([]);
    expect(programModules(CURRICULUM)).toEqual([
      {
        id: 'module-1',
        title: 'Введение',
        hours: 12,
        topicCount: 2,
        topics: ['Понятия', 'Практика']
      },
      { id: 'module-2', title: 'Итоги', hours: 6, topicCount: 1, topics: ['Защита'] }
    ]);
  });

  it('reads the summary, competencies and kind from the draft', () => {
    const filled = state({
      draft: {
        kind: 'professional_development',
        title: 'Аналитика',
        competencies: [{ id: 'competence-1', statement: 'Анализировать данные' }],
        curriculum: CURRICULUM,
        sources: [],
        summary: {
          goal: 'Подготовить аналитиков',
          tasks: [],
          target_audience: 'Специалисты',
          form: 'Очно-заочная'
        }
      }
    });

    expect(programKindLabel(filled)).toBe('Повышение квалификации');
    expect(programKindLabel(state())).toBeNull();
    expect(programFormLabel(filled)).toBe('Очно-заочная');
    expect(programFormLabel(state())).toBe('—');
    expect(programGoal(filled)).toBe('Подготовить аналитиков');
    expect(programGoal(state())).toBeNull();
    expect(programCompetencies(filled)).toEqual([
      { id: 'competence-1', label: 'Анализировать данные' }
    ]);
    expect(programCompetencies(null)).toEqual([]);
  });

  it('maps sources and the self-check report for display', () => {
    const reviewed = state({
      draft: {
        kind: 'professional_development',
        title: 'Аналитика',
        competencies: [],
        curriculum: null,
        sources: [
          {
            id: 'src-01',
            kind: 'normative_act',
            title: 'ФГОС по аналитике',
            confirmed: true,
            trust: ' Действующий документ ',
            origin: 'Минпросвещения'
          },
          {
            id: 'web-1',
            kind: 'internet_resource',
            title: 'Статья о данных',
            confirmed: false,
            trust: null,
            origin: 'Открытая энциклопедия'
          }
        ],
        summary: null
      },
      report: {
        ok: false,
        checks: [
          {
            name: 'часы',
            ok: false,
            problems: ['объём не совпадает с заявленным'],
            details: { заявлено: 72, всего: 70, 'итоговая аттестация': 2 }
          },
          {
            name: 'покрытие',
            ok: true,
            problems: [],
            details: { 'результатов обучения': 3, 'раскрыто в темах': 3 }
          },
          {
            name: 'разделы',
            ok: true,
            problems: [],
            details: { компетенции: true, материалы: false }
          }
        ]
      }
    });

    expect(programSources(null)).toEqual([]);
    expect(programSources(reviewed)).toEqual([
      {
        id: 'src-01',
        title: 'ФГОС по аналитике',
        kindLabel: 'Нормативный акт',
        confirmed: true,
        trust: 'Действующий документ',
        origin: 'Минпросвещения'
      },
      {
        id: 'web-1',
        title: 'Статья о данных',
        kindLabel: 'Интернет-ресурс',
        confirmed: false,
        trust: null,
        origin: 'Открытая энциклопедия'
      }
    ]);

    expect(programReport(null)).toBeNull();
    const report = programReport(reviewed);
    expect(report?.ok).toBe(false);
    expect(report?.passed).toBe(2);
    expect(report?.checks.map((check) => check.label)).toEqual([
      'Часы',
      'Покрытие результатов',
      'Разделы программы'
    ]);
    expect(report?.checks[0].problems).toEqual(['объём не совпадает с заявленным']);
    expect(report?.checks[0].details).toEqual([
      { label: 'Заявлено', value: '72 часа' },
      { label: 'Всего', value: '70 часов' },
      { label: 'Итоговая аттестация', value: '2 часа' }
    ]);
    expect(report?.checks[2].details).toEqual([
      { label: 'Компетенции', value: 'есть' },
      { label: 'Материалы', value: 'нет' }
    ]);
  });
});
