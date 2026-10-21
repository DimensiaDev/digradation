import type { Program } from '../../core/models/models';
import {
  buildProgramCompetencies,
  buildProgramMaterials,
  buildProgramModules,
  formatProgramHours
} from './program-detail-content';

const PROGRAM: Program = {
  id: 'it-python',
  title: 'Python для анализа данных',
  type: 'Повышение квалификации',
  direction: 'IT и аналитика',
  hours: 72,
  tags: ['IT', 'Python'],
  status: 'published',
  theme: 'it',
  author: { name: 'Иванова А. В.', avatarUrl: null },
  updatedAt: '2025-10-12T12:00:00Z',
  description: 'Практический курс по анализу данных.',
  goals: ['Освоить Python.']
};

describe('program detail content', () => {
  it.each([
    [1, '1 час'],
    [2, '2 часа'],
    [4, '4 часа'],
    [5, '5 часов'],
    [11, '11 часов'],
    [21, '21 час'],
    [72, '72 часа'],
    [0, '0 часов']
  ])('formats %i hours as %s', (hours, expected) => {
    expect(formatProgramHours(hours)).toBe(expected);
  });

  it('builds four modules that add up to the program duration', () => {
    const modules = buildProgramModules(72);

    expect(modules.length).toBe(4);
    expect(modules.reduce((total, module) => total + module.hours, 0)).toBe(72);
    expect(modules[0].id).toBe('module-1');
    expect(modules[0].title).toBe('Введение и ключевые понятия');
    expect(modules[0].summary).toContain('Цели обучения');
  });

  it('keeps the duration exact for short programs and zero', () => {
    expect(buildProgramModules(16).reduce((total, module) => total + module.hours, 0)).toBe(16);
    expect(buildProgramModules(0).reduce((total, module) => total + module.hours, 0)).toBe(0);
  });

  it('derives competencies from the program tags and direction', () => {
    const competencies = buildProgramCompetencies(PROGRAM);

    expect(competencies.length).toBe(3);
    expect(competencies[0]).toContain('IT, Python');
    expect(competencies[1]).toContain('IT и аналитика');
  });

  it('falls back when the program has no tags', () => {
    const competencies = buildProgramCompetencies({ ...PROGRAM, tags: [] });

    expect(competencies[0]).toBe('Применять освоенные методы в профессиональной деятельности.');
  });

  it('builds materials with shared file type styles', () => {
    const materials = buildProgramMaterials();

    expect(materials.length).toBe(4);
    expect(materials.map((material) => material.name)).toEqual([
      'Программа курса.pdf',
      'Рабочая тетрадь.xlsx',
      'Методические указания.docx',
      'Шаблон итогового проекта.docx'
    ]);
    expect(materials[0].meta).toBe('PDF · 2,4 МБ');
    expect(materials[0].style.color).toBe('var(--color-danger)');
    expect(materials[1].style.color).toBe('var(--color-success)');
    expect(materials[2].style.icon).toBeTruthy();
  });
});
