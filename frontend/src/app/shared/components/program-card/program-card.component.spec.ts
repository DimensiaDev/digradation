import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Program } from '../../../core/models/models';
import { ProgramCardComponent } from './program-card.component';

const PROGRAM: Program = {
  id: 'it-python',
  title: 'Python для анализа данных',
  type: 'Повышение квалификации',
  direction: 'IT и аналитика',
  hours: 72,
  status: 'approved',
  theme: 'it',
  updatedAt: '2025-10-12T12:00:00Z',
  description: 'Практический курс по анализу данных.',
  goals: ['Освоить Python.', 'Научиться анализировать данные.']
};

describe('ProgramCardComponent', () => {
  let fixture: ComponentFixture<ProgramCardComponent>;
  let component: ProgramCardComponent;
  let opens: number;
  let openTriggers: (HTMLElement | null)[];

  const detect = () => {
    fixture.detectChanges();
    TestBed.tick();
  };

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [ProgramCardComponent] });
    fixture = TestBed.createComponent(ProgramCardComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('program', PROGRAM);
    opens = 0;
    openTriggers = [];
    component.open.subscribe((trigger) => {
      opens++;
      openTriggers.push(trigger);
    });
    detect();
  });

  it('renders the supported program fields', () => {
    const text = fixture.nativeElement.textContent as string;

    expect(text).toContain('Python для анализа данных');
    expect(text).toContain('Повышение квалификации');
    expect(text).toContain('72 ч');
    expect(text).not.toContain('IT и аналитика');
    expect(text).not.toContain('Практический курс по анализу данных.');
    expect(text).toContain('12.10.2025');
    expect(text).toContain('Согласована');
  });

  it('does not render unavailable catalogue actions', () => {
    expect(fixture.debugElement.query(By.css('.program-card__menu'))).toBeNull();
    expect(fixture.debugElement.query(By.css('.program-card__author'))).toBeNull();
    expect(fixture.debugElement.query(By.css('.program-card__tags'))).toBeNull();
  });

  it('hides metadata until the programme detail supplies it', () => {
    fixture.componentRef.setInput('program', {
      ...PROGRAM,
      type: '',
      hours: 0
    });
    detect();

    expect(fixture.debugElement.query(By.css('.program-card__meta'))).toBeNull();
  });

  it('opens the program from the full-card action', () => {
    const open = fixture.debugElement.query(By.css('.program-card__open'));

    expect(open.attributes['aria-label']).toBe('Открыть программу «Python для анализа данных»');
    open.nativeElement.click();
    detect();

    expect(opens).toBe(1);
    expect(openTriggers[0]).toBe(open.nativeElement);
  });

  it('renders the date as a machine-readable time element', () => {
    const time = fixture.debugElement.query(By.css('time'));

    expect(time.attributes['datetime']).toBe(PROGRAM.updatedAt);
  });

  it('renders the theme watermark and the status data attributes', () => {
    const card = fixture.debugElement.query(By.css('.program-card'));
    const watermark = fixture.debugElement.query(By.css('svg.program-card__watermark'));

    expect(card.attributes['data-theme']).toBe('it');
    expect(card.attributes['data-status']).toBe('approved');
    expect(watermark).toBeTruthy();
    expect(watermark.attributes['aria-hidden']).toBe('true');
  });
});
