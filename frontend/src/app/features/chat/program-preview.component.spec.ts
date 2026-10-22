import { HttpErrorResponse } from '@angular/common/http';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { ProgramStateApi } from '../../core/models/models';
import { AttachmentDownloadService } from '../../core/services/attachment-download.service';
import { NotificationService } from '../../core/services/notification.service';
import { ProgramPreviewComponent } from './program-preview.component';

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
    ...overrides
  };
}

const FILLED = state({
  status: 'generating',
  current_stage: 'summary',
  version: 4,
  draft: {
    kind: 'professional_development',
    title: 'Аналитика данных',
    competencies: [
      { id: 'c-1', statement: 'Анализировать требования' },
      { id: 'c-2', statement: 'Строить отчёты' }
    ],
    curriculum: {
      modules: [
        {
          id: 'm-1',
          title: 'Введение',
          topics: [{ id: 't-1', title: 'Понятия', hours: { lecture: 4, practice: 2 } }]
        },
        {
          id: 'm-2',
          title: 'Практика',
          topics: [{ id: 't-2', title: 'Кейсы', hours: { practice: 6, self_study: 6 } }]
        }
      ]
    },
    sources: [],
    summary: {
      goal: 'Подготовить аналитиков данных',
      tasks: [],
      target_audience: 'Специалисты',
      form: 'Очно-заочная'
    }
  }
});

const REVIEWED = state({
  ...FILLED,
  status: 'pending_approval',
  draft: {
    ...FILLED.draft,
    sources: [
      {
        id: 'src-01',
        kind: 'normative_act',
        title: 'ФГОС по аналитике данных',
        confirmed: true,
        trust: 'Действующий документ',
        origin: 'Минпросвещения России'
      },
      {
        id: 'web-1',
        kind: 'internet_resource',
        title: 'Статья об аналитике',
        confirmed: false,
        trust: null,
        origin: 'Открытая энциклопедия'
      }
    ]
  },
  report: {
    ok: false,
    checks: [
      {
        name: 'часы',
        ok: true,
        problems: [],
        details: {
          заявлено: 18,
          всего: 18,
          темы: 16,
          'итоговая аттестация': 2
        }
      },
      {
        name: 'покрытие',
        ok: false,
        problems: ['результат обучения c-2 не раскрыт ни в одной теме'],
        details: {
          'результатов обучения': 2,
          'раскрыто в темах': 1,
          'проверяется двумя средствами': 0
        }
      }
    ]
  }
});

const EXPORTED = state({
  ...REVIEWED,
  exports: {
    document_attachment_id: 41,
    slides_attachment_id: 42
  }
});

describe('ProgramPreviewComponent', () => {
  let fixture: ComponentFixture<ProgramPreviewComponent>;
  let component: ProgramPreviewComponent;
  let fetchFile: ReturnType<typeof vi.fn>;
  let save: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    fetchFile = vi.fn(() => of(new File(['x'], 'programme.docx')));
    save = vi.fn();
    await TestBed.configureTestingModule({
      imports: [ProgramPreviewComponent],
      providers: [
        {
          provide: AttachmentDownloadService,
          useValue: { fetchFile, save }
        }
      ]
    }).compileComponents();
    fixture = TestBed.createComponent(ProgramPreviewComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('sessionId', 'mock-programme-4');
  });

  function expandSection(section: string): void {
    const button = fixture.nativeElement.querySelector(
      `[data-section="${section}"] .accordion__toggle`
    ) as HTMLButtonElement;
    button.click();
    fixture.detectChanges();
  }

  it('shows the empty state and only the supported metadata', () => {
    fixture.componentRef.setInput('state', state());
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Каркас программы');
    expect(text).toContain('Ожидает данных');
    expect(text).not.toContain('Тип аттестации');
    expect(text).not.toContain('уточняется');
    expect(text).toContain('Каркас появится здесь');
    expect(fixture.nativeElement.querySelectorAll('.program-preview__meta-item').length).toBe(2);
  });

  it('points empty sections at the stage the client actually shows', () => {
    fixture.componentRef.setInput(
      'state',
      state({
        status: 'generating',
        draft: { ...state().draft, title: 'Аналитика данных' }
      })
    );
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text.match(/на этапе «Каркас программы»/g)).toHaveLength(4);
    expect(text).not.toContain('«Паспорт программы»');
    expect(text).not.toContain('«Учебный план»');
    expect(text).not.toContain('после этапа «Источники»');
  });

  it('opens only one accordion section at a time', () => {
    fixture.componentRef.setInput('state', FILLED);
    fixture.detectChanges();

    const summary = fixture.nativeElement.querySelector(
      '#program-preview-summary-panel'
    ) as HTMLElement;
    const modules = fixture.nativeElement.querySelector(
      '#program-preview-modules-panel'
    ) as HTMLElement;

    expect(summary.classList.contains('accordion__panel--collapsed')).toBe(false);
    expect(modules.classList.contains('accordion__panel--collapsed')).toBe(true);
    expect(modules.getAttribute('aria-hidden')).toBe('true');

    expandSection('modules');

    expect(summary.classList.contains('accordion__panel--collapsed')).toBe(true);
    expect(modules.classList.contains('accordion__panel--collapsed')).toBe(false);
    expect(modules.hasAttribute('aria-hidden')).toBe(false);
    expect(
      fixture.nativeElement
        .querySelector('[data-section="modules"] .accordion__toggle')
        .getAttribute('aria-expanded')
    ).toBe('true');

    expandSection('modules');

    expect(modules.classList.contains('accordion__panel--collapsed')).toBe(true);
  });

  it('renders real blocks, hours and the draft badge', () => {
    fixture.componentRef.setInput('state', FILLED);
    fixture.detectChanges();

    let text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Аналитика данных');
    expect(text).toContain('Черновик обновлён');
    expect(text).toContain('18 часов');
    expect(text).toContain('Очно-заочная');
    expect(text).toContain('Подготовить аналитиков данных');
    expect(
      (fixture.nativeElement.querySelector('#program-preview-competencies-panel') as HTMLElement)
        .classList
    ).toContain('accordion__panel--collapsed');

    expandSection('competencies');
    text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Анализировать требования');
    expect(
      (fixture.nativeElement.querySelector('#program-preview-competencies-panel') as HTMLElement)
        .classList
    ).not.toContain('accordion__panel--collapsed');

    expandSection('modules');
    text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Введение');
    expect(text).toContain('1 тема');
    expect(text).toContain('Итого: 18 часов');
  });

  it('renders sources with their kind, confirmation, trust and origin', () => {
    fixture.componentRef.setInput('state', REVIEWED);
    fixture.detectChanges();
    expandSection('sources');

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Источники');
    expect(text).toContain('ФГОС по аналитике данных');
    expect(text).toContain('Нормативный акт');
    expect(text).toContain('Подтверждён каталогом');
    expect(text).toContain('Доверие:');
    expect(text).toContain('Действующий документ');
    expect(text).toContain('Происхождение:');
    expect(text).toContain('Минпросвещения России');
    expect(text).toContain('Статья об аналитике');
    expect(text).toContain('Интернет-ресурс');
    expect(text).toContain('Не подтверждён — проверьте вручную');
    expect(text).toContain('Открытая энциклопедия');

    const unconfirmed = fixture.nativeElement.querySelector(
      '.program-preview__source--unconfirmed'
    ) as HTMLElement;
    expect(unconfirmed).not.toBeNull();
    expect(unconfirmed.textContent).toContain('Не подтверждён');
  });

  it('renders the self-check report with problems and key details', () => {
    fixture.componentRef.setInput('state', REVIEWED);
    fixture.detectChanges();
    expandSection('report');

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Проверка');
    const score = fixture.nativeElement.querySelector(
      '.program-preview__report-score'
    ) as HTMLElement;
    expect(score.textContent).toContain('1');
    expect(score.textContent).toContain('из 2 проверок');
    expect(text).toContain('Нельзя утверждать');
    expect(text).toContain('Часы');
    expect(text).toContain('Заявлено');
    expect(text).toContain('18 часов');
    expect(text).toContain('Итоговая аттестация');
    expect(text).toContain('2 часа');
    expect(text).toContain('Покрытие результатов');
    expect(text).toContain('Раскрыто в темах');
    expect(text).toContain('результат обучения c-2 не раскрыт ни в одной теме');
    expect(text).toContain('Требует внимания');
    expect(text).toContain('Пройдено');

    const verdict = fixture.nativeElement.querySelector(
      '.program-preview__report-summary .program-preview__badge'
    ) as HTMLElement;
    expect(verdict.getAttribute('data-tone')).toBe('danger');

    const progress = fixture.nativeElement.querySelector(
      '.program-preview__report-progress'
    ) as HTMLElement;
    expect(progress.getAttribute('aria-valuenow')).toBe('1');
    expect(progress.getAttribute('aria-valuemax')).toBe('2');

    const problems = fixture.nativeElement.querySelectorAll(
      '[data-group="problems"] .program-preview__check--problem'
    ) as NodeListOf<HTMLElement>;
    expect(problems.length).toBe(1);
    expect(problems[0].textContent).toContain('не раскрыт');
  });

  it('shows a passed self-check without problem highlighting', () => {
    const checks = (REVIEWED.report?.checks ?? []).map((check) => ({
      ...check,
      ok: true,
      problems: []
    }));
    fixture.componentRef.setInput('state', {
      ...REVIEWED,
      report: { ok: true, checks }
    });
    fixture.detectChanges();
    expandSection('report');

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Проверки пройдены');
    const score = fixture.nativeElement.querySelector(
      '.program-preview__report-score'
    ) as HTMLElement;
    expect(score.textContent).toContain('2');
    expect(score.textContent).toContain('из 2 проверок');
    expect(
      fixture.nativeElement
        .querySelector('.program-preview__report-summary .program-preview__badge')
        .getAttribute('data-tone')
    ).toBe('success');
    expect(fixture.nativeElement.querySelector('[data-group="problems"]')).toBeNull();
    expect(
      fixture.nativeElement.querySelectorAll('[data-group="passed"] .program-preview__check').length
    ).toBe(2);
    expect(fixture.nativeElement.querySelector('.program-preview__check--problem')).toBeNull();
  });

  it('shows the results block and downloads the available exports', () => {
    const file = new File(['docx'], 'programme.docx');
    fetchFile.mockReturnValue(of(file));
    fixture.componentRef.setInput('state', EXPORTED);
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Результаты программы');
    expect(text).toContain('Скачать DOCX');
    expect(text).toContain('Скачать PPTX');

    const buttons = fixture.nativeElement.querySelectorAll(
      '.program-preview__results button'
    ) as NodeListOf<HTMLButtonElement>;
    expect(buttons.length).toBe(2);

    buttons[0].click();
    fixture.detectChanges();

    expect(fetchFile).toHaveBeenCalledWith('mock-programme-4', 41);
    expect(save).toHaveBeenCalledWith(file, 'programme.docx');
    expect(component.downloading()).toBeNull();
  });

  it('shows only the exports that exist', () => {
    fixture.componentRef.setInput('state', {
      ...EXPORTED,
      exports: { document_attachment_id: null, slides_attachment_id: 42 }
    });
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).not.toContain('Скачать DOCX');
    expect(text).toContain('Скачать PPTX');
  });

  it('shows no results footer until an export exists', () => {
    fixture.componentRef.setInput('state', REVIEWED);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.program-preview__footer')).toBeNull();
  });

  it('reports a failed download and enables the buttons again', () => {
    fetchFile.mockReturnValue(
      throwError(() => new HttpErrorResponse({ status: 404, error: { detail: 'Нет файла' } }))
    );
    fixture.componentRef.setInput('state', EXPORTED);
    fixture.detectChanges();

    const button = fixture.nativeElement.querySelector(
      '.program-preview__results button'
    ) as HTMLButtonElement;
    button.click();
    fixture.detectChanges();

    const notifications = TestBed.inject(NotificationService).notifications();
    expect(notifications[0]?.message).toContain('Не удалось скачать файл');
    expect(component.downloading()).toBeNull();
    expect(save).not.toHaveBeenCalled();
  });

  it('pulses the badge when the programme version changes', () => {
    vi.useFakeTimers();
    fixture.componentRef.setInput('state', { ...FILLED, version: 4 });
    fixture.detectChanges();

    const dot = fixture.nativeElement.querySelector('.program-preview__badge-dot') as HTMLElement;
    expect(dot.classList.contains('program-preview__badge-dot--pulse')).toBe(false);

    fixture.componentRef.setInput('state', { ...FILLED, version: 5 });
    fixture.detectChanges();

    expect(dot.classList.contains('program-preview__badge-dot--pulse')).toBe(true);
    vi.advanceTimersByTime(1400);
    fixture.detectChanges();
    expect(dot.classList.contains('program-preview__badge-dot--pulse')).toBe(false);
    vi.useRealTimers();
  });

  it('shows a loading skeleton before the first state arrives', () => {
    fixture.componentRef.setInput('loading', true);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.program-preview__loading')).not.toBeNull();
  });

  it('shows an error with a retry action', () => {
    const reload = vi.fn();
    component.reload.subscribe(reload);
    fixture.componentRef.setInput('error', 'Не удалось загрузить каркас');
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Не удалось загрузить каркас');

    const button = fixture.nativeElement.querySelector(
      '.program-preview__error button'
    ) as HTMLButtonElement;
    button.click();

    expect(reload).toHaveBeenCalledOnce();
  });

  it('opens the files section and asks the chat to reload uploads', () => {
    const filesOpened = vi.fn();
    component.filesOpened.subscribe(filesOpened);
    fixture.componentRef.setInput('state', state());
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('Файлы');

    expandSection('files');

    expect(filesOpened).toHaveBeenCalledOnce();
    expect(fixture.nativeElement.querySelector('.session-files__section')).not.toBeNull();
  });

  it('renders session and pending files and forwards their actions', () => {
    const preview = vi.fn();
    const download = vi.fn();
    const remove = vi.fn();
    const removeSession = vi.fn();
    component.previewRequested.subscribe(preview);
    component.downloadRequested.subscribe(download);
    component.deletePendingRequested.subscribe(remove);
    component.deleteSessionRequested.subscribe(removeSession);

    fixture.componentRef.setInput('state', state());
    fixture.componentRef.setInput('sessionFiles', [{ name: 'итог.pdf', attachmentId: 3 }]);
    fixture.componentRef.setInput('pendingFiles', [
      {
        id: 7,
        filename: 'деталь.pdf',
        status: 'uploading',
        ready: false,
        chat_message_id: null,
        timestamp: '2026-09-12T12:18:35.689095Z'
      }
    ]);
    fixture.detectChanges();
    expandSection('files');

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Файлы в сообщениях');
    expect(text).toContain('итог.pdf');
    expect(text).toContain('Загруженные файлы');
    expect(text).toContain('деталь.pdf');
    expect(text).toContain('Загружается');

    const rows = fixture.nativeElement.querySelectorAll(
      '.session-files__file'
    ) as NodeListOf<HTMLElement>;
    (rows[0].querySelector('[aria-label="Предпросмотр"]') as HTMLButtonElement).click();
    (rows[0].querySelector('[aria-label="Скачать"]') as HTMLButtonElement).click();
    (rows[1].querySelector('[aria-label="Удалить файл"]') as HTMLButtonElement).click();
    (
      fixture.nativeElement.querySelector('.session-files__actions button') as HTMLButtonElement
    ).click();

    expect(preview).toHaveBeenCalledWith({ name: 'итог.pdf', attachmentId: 3 });
    expect(download).toHaveBeenCalledWith({ name: 'итог.pdf', attachmentId: 3 });
    expect(remove).toHaveBeenCalledWith(expect.objectContaining({ id: 7, filename: 'деталь.pdf' }));
    expect(removeSession).toHaveBeenCalledOnce();
  });

  it('describes a file by type', () => {
    expect(component.fileIconFor('drawing.pdf')).toBeTruthy();
    expect(component.fileColorFor('drawing.pdf')).toBeTruthy();
  });

  it('tells every upload state apart', () => {
    const labels = [
      ['uploading', 'Загружается'],
      ['processing', 'Обрабатывается'],
      ['error', 'Ошибка'],
      ['completed', 'Готово']
    ] as const;

    for (const [status, label] of labels) {
      expect(
        component.attachmentStatusLabel({
          id: 1,
          filename: 'a.pdf',
          status,
          ready: false,
          chat_message_id: null,
          timestamp: ''
        })
      ).toBe(label);
    }

    expect(
      component.attachmentStatusLabel({
        id: 2,
        filename: 'b.pdf',
        status: 'unknown',
        ready: true,
        chat_message_id: null,
        timestamp: ''
      })
    ).toBe('Готово');
  });

  it('scrolls to and highlights a section on request', () => {
    vi.useFakeTimers();
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView
    });

    fixture.componentRef.setInput('state', FILLED);
    fixture.detectChanges();

    component.focusSection('modules');
    fixture.detectChanges();

    expect(scrollIntoView).toHaveBeenCalledOnce();
    expect(component.focused()).toBe('modules');
    expect(
      fixture.nativeElement
        .querySelector('[data-section="modules"]')
        .classList.contains('program-preview__section--focused')
    ).toBe(true);

    vi.advanceTimersByTime(1400);
    fixture.detectChanges();
    expect(component.focused()).toBeNull();
    vi.useRealTimers();
  });
});
