import type { Program } from '../../core/models/models';
import { fileTypeStyleFor, type FileTypeStyle } from './file-types';

export interface ProgramModulePreview {
  readonly id: string;
  readonly title: string;
  readonly summary: string;
  readonly hours: number;
}

export interface ProgramMaterialPreview {
  readonly id: string;
  readonly name: string;
  readonly meta: string;
  readonly style: FileTypeStyle;
}

const MODULE_BLUEPRINTS = [
  {
    title: 'Введение и ключевые понятия',
    summary: 'Цели обучения, базовые термины и контекст программы.'
  },
  {
    title: 'Практический модуль',
    summary: 'Разбор кейсов и отработка инструментов на реальных задачах.'
  },
  {
    title: 'Инструменты и методы',
    summary: 'Методики, шаблоны и подходы для ежедневной работы.'
  },
  {
    title: 'Итоговый проект',
    summary: 'Самостоятельная работа и защита результатов.'
  }
] as const;

const MATERIAL_BLUEPRINTS = [
  { id: 'material-1', name: 'Программа курса.pdf', meta: 'PDF · 2,4 МБ', extension: '.pdf' },
  { id: 'material-2', name: 'Рабочая тетрадь.xlsx', meta: 'XLSX · 860 КБ', extension: '.xlsx' },
  {
    id: 'material-3',
    name: 'Методические указания.docx',
    meta: 'DOCX · 1,1 МБ',
    extension: '.docx'
  },
  {
    id: 'material-4',
    name: 'Шаблон итогового проекта.docx',
    meta: 'DOCX · 640 КБ',
    extension: '.docx'
  }
] as const;

export function formatProgramHours(hours: number): string {
  const total = Math.max(0, Math.round(hours));
  const lastTwo = total % 100;
  const last = total % 10;
  const word =
    last === 1 && lastTwo !== 11
      ? 'час'
      : last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)
        ? 'часа'
        : 'часов';

  return `${total} ${word}`;
}

export function buildProgramModules(hours: number): readonly ProgramModulePreview[] {
  const total = Math.max(0, Math.round(hours));
  const first = Math.round(total * 0.2);
  const second = Math.round(total * 0.3);
  const third = Math.round(total * 0.3);
  const allocated = [first, second, third, total - first - second - third];

  return MODULE_BLUEPRINTS.map((module, index) => ({
    ...module,
    id: `module-${index + 1}`,
    hours: allocated[index]
  }));
}

export function buildProgramCompetencies(program: Program): readonly string[] {
  const tagLine =
    program.tags.length > 0
      ? `Применять на практике: ${program.tags.join(', ')}.`
      : 'Применять освоенные методы в профессиональной деятельности.';

  return [
    tagLine,
    `Проектировать решения в направлении «${program.direction}».`,
    'Оценивать результаты и представлять итоговый проект.'
  ];
}

export function buildProgramMaterials(): readonly ProgramMaterialPreview[] {
  return MATERIAL_BLUEPRINTS.map(({ extension, ...material }) => ({
    ...material,
    style: fileTypeStyleFor(extension)
  }));
}
