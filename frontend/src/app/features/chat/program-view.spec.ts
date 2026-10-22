import { ProgramStateApi } from '../../core/models/models';
import {
  programBadge,
  programCompetencies,
  programFormLabel,
  programGoal,
  programHours,
  programHoursLabel,
  programKindLabel,
  programMilestones,
  programModules,
  programStatusHint,
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
    expect(programStep(state({ status: 'generating', current_stage: 'sources' }))).toBe(3);
    expect(programStep(state({ status: 'generating', current_stage: 'summary' }))).toBe(3);
    expect(programStep(state({ status: 'generating', current_stage: 'assessment' }))).toBe(3);
    expect(programStep(state({ status: 'generating', current_stage: 'materials' }))).toBe(3);
    expect(programStep(state({ status: 'pending_approval' }))).toBe(4);
    expect(programStep(state({ status: 'approved' }))).toBe(5);
    expect(programStep(state({ status: 'verified' }))).toBe(5);
    expect(programStep(state({ status: 'failed', current_stage: 'summary' }))).toBe(3);
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

  it('describes the run status and its next action', () => {
    expect(programStatusLabel(null)).toBe('Нет программы');
    expect(programStatusLabel(state({ status: 'pending_approval' }))).toBe('На согласовании');
    expect(programStatusHint(null)).toContain('Опишите программу');
    expect(programStatusHint(state({ status: 'awaiting_answers' }))).toContain('Ответьте');
    expect(programStatusHint(state({ status: 'generating', current_stage: 'hours' }))).toContain(
      'учебный план'
    );
    expect(programStatusHint(state({ status: 'generating' }))).toContain('по этапам');
    expect(programStatusHint(state({ status: 'pending_approval' }))).toContain('Проверьте');
    expect(programStatusHint(state({ status: 'approved' }))).toContain('утверждена');
    expect(programStatusHint(state({ status: 'failed', error: 'Нет данных' }))).toBe('Нет данных');
    expect(programStatusHint(state({ status: 'failed' }))).toContain('Не удалось');
    expect(programStatusHint(state({ status: 'draft' }))).toContain('Черновик');
  });

  it('sums curriculum hours and exposes modules', () => {
    expect(programHours(undefined)).toBe(0);
    expect(programHours(CURRICULUM)).toBe(18);
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

  it('builds chat milestone cards from the saved blocks', () => {
    expect(programMilestones(null)).toEqual([]);
    expect(programMilestones(state())).toEqual([]);

    const structure = state({
      draft: {
        kind: null,
        title: null,
        competencies: [{ id: 'c-1', statement: 'Умеет' }],
        curriculum: null,
        sources: [],
        summary: null
      }
    });
    expect(programMilestones(structure)).toEqual([
      {
        id: 'structure',
        title: 'Структура программы готова',
        description: 'Компетенции и результаты освоения сохранены',
        section: 'competencies'
      }
    ]);

    const modules = state({
      draft: {
        kind: null,
        title: null,
        competencies: [],
        curriculum: CURRICULUM,
        sources: [],
        summary: null
      }
    });
    expect(programMilestones(modules)[0]).toMatchObject({
      section: 'modules',
      description: '18 часов · модулей: 2'
    });

    const complete = state({
      status: 'pending_approval',
      draft: {
        kind: 'general_development',
        title: 'Программа',
        competencies: [{ id: 'c-1', statement: 'Умеет' }],
        curriculum: CURRICULUM,
        sources: [],
        summary: {
          goal: 'Цель',
          tasks: [],
          target_audience: 'Все',
          form: 'Очная'
        }
      }
    });
    expect(programMilestones(complete).map((milestone) => milestone.id)).toEqual([
      'structure',
      'scaffold',
      'review'
    ]);
  });
});
