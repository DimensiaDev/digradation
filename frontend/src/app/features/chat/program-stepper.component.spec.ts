import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ProgramStateApi } from '../../core/models/models';
import { ProgramStepperComponent } from './program-stepper.component';

const BLOCKS = {
  kind: null,
  title: null,
  competencies: [],
  curriculum: null,
  sources: [],
  summary: null
};

const state = (overrides: Partial<ProgramStateApi> = {}): ProgramStateApi => ({
  status: 'none',
  current_stage: null,
  stages: {},
  running: false,
  busy: false,
  error: null,
  updated_at: null,
  version: 1,
  questions: [],
  draft: BLOCKS,
  report: null,
  exports: null,
  revision: null,
  last_revision: null,
  approved_by: null,
  approved_at: null,
  ...overrides
});

describe('ProgramStepperComponent', () => {
  let fixture: ComponentFixture<ProgramStepperComponent>;

  const create = (value: ProgramStateApi | null): void => {
    fixture = TestBed.createComponent(ProgramStepperComponent);
    fixture.componentRef.setInput('state', value);
    fixture.detectChanges();
  };

  const steps = (): HTMLElement[] =>
    Array.from(fixture.nativeElement.querySelectorAll('.program-stepper__step'));

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [ProgramStepperComponent] });
  });

  it('starts on the brief step and labels every stage', () => {
    create(null);

    expect(fixture.nativeElement.textContent).toContain('Запрос');
    expect(fixture.nativeElement.textContent).toContain('Каркас программы');
    expect(fixture.nativeElement.textContent).toContain('Согласование каркаса');
    expect(fixture.nativeElement.textContent).toContain('Содержание и материалы');
    expect(steps().map((step) => step.dataset['state'])).toEqual([
      'active',
      'pending',
      'pending',
      'pending',
      'pending'
    ]);
    expect(steps()[0].getAttribute('aria-current')).toBe('step');
  });

  it('marks the generation stage from the current stage name', () => {
    create(
      state({
        status: 'generating',
        current_stage: 'hours'
      })
    );

    expect(steps().map((step) => step.dataset['state'])).toEqual([
      'completed',
      'active',
      'pending',
      'pending',
      'pending'
    ]);
    expect(steps()[1].getAttribute('aria-current')).toBe('step');
    expect(steps()[0].querySelector('svg')).not.toBeNull();
  });

  it('shows a failed stage with an alert marker', () => {
    create(
      state({
        status: 'failed',
        current_stage: 'summary'
      })
    );

    expect(steps().map((step) => step.dataset['state'])).toEqual([
      'completed',
      'failed',
      'pending',
      'pending',
      'pending'
    ]);
    expect(steps()[1].querySelector('svg')).not.toBeNull();
    expect(steps()[1].getAttribute('aria-current')).toBe('step');
  });

  it('waits for the methodist to agree the skeleton', () => {
    create(state({ status: 'pending_approval' }));

    expect(steps().map((step) => step.dataset['state'])).toEqual([
      'completed',
      'completed',
      'active',
      'pending',
      'pending'
    ]);
    expect(steps()[2].getAttribute('aria-current')).toBe('step');
  });

  it('spins a ring around the stage a busy run is working on', () => {
    create(state({ status: 'generating', current_stage: 'hours', busy: false }));

    const markers = (): HTMLElement[] =>
      Array.from(fixture.nativeElement.querySelectorAll('.program-stepper__marker'));

    expect(markers()[1].classList.contains('program-stepper__marker--spinning')).toBe(false);
    expect(fixture.nativeElement.textContent).not.toContain('Этап выполняется');

    create(state({ status: 'generating', current_stage: 'hours', busy: true }));

    expect(markers()[1].classList.contains('program-stepper__marker--spinning')).toBe(true);
    expect(markers()[0].classList.contains('program-stepper__marker--spinning')).toBe(false);
    expect(fixture.nativeElement.textContent).toContain('Этап выполняется');
  });

  it('stops spinning once the run waits for the methodist', () => {
    // `busy` is false exactly while a run waits, so the ring is off there even
    // though the approval step is the current one.
    create(state({ status: 'pending_approval', busy: false }));

    expect(fixture.nativeElement.querySelector('.program-stepper__marker--spinning')).toBeNull();
    expect(
      (
        fixture.nativeElement.querySelectorAll('.program-stepper__marker')[2] as HTMLElement
      ).classList.contains('program-stepper__marker--spinning')
    ).toBe(false);
  });

  it('keeps the final step current once the programme is approved', () => {
    create(state({ status: 'approved' }));

    expect(steps().map((step) => step.dataset['state'])).toEqual([
      'completed',
      'completed',
      'completed',
      'completed',
      'active'
    ]);
    expect(steps()[4].getAttribute('aria-current')).toBe('step');
  });
});
