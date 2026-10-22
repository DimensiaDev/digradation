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
    expect(fixture.nativeElement.textContent).toContain('Проверка');
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
      'completed',
      'failed',
      'pending',
      'pending'
    ]);
    expect(steps()[2].querySelector('svg')).not.toBeNull();
    expect(steps()[2].getAttribute('aria-current')).toBe('step');
  });

  it('keeps the verification step current once the draft is approved', () => {
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
