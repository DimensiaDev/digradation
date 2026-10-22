import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ProgramStateApi } from '../../core/models/models';
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

describe('ProgramPreviewComponent', () => {
  let fixture: ComponentFixture<ProgramPreviewComponent>;
  let component: ProgramPreviewComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ProgramPreviewComponent]
    }).compileComponents();
    fixture = TestBed.createComponent(ProgramPreviewComponent);
    component = fixture.componentInstance;
  });

  it('shows the empty state and the draft metadata placeholders', () => {
    fixture.componentRef.setInput('state', state());
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Каркас программы');
    expect(text).toContain('Ожидает данных');
    expect(text).toContain('Тип аттестации');
    expect(text).toContain('уточняется');
    expect(text).toContain('Каркас появится здесь');
  });

  it('renders real blocks, hours and the draft badge', () => {
    fixture.componentRef.setInput('state', FILLED);
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Аналитика данных');
    expect(text).toContain('Повышение квалификации');
    expect(text).toContain('Черновик обновлён');
    expect(text).toContain('18 часов');
    expect(text).toContain('Очно-заочная');
    expect(text).toContain('Подготовить аналитиков данных');
    expect(text).toContain('Анализировать требования');
    expect(text).toContain('Введение');
    expect(text).toContain('1 тема');
    expect(text).toContain('Итого: 18 часов');
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
