import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Program } from '../../../core/models/models';
import { ProgramDetailPanelComponent } from './program-detail-panel.component';

const PROGRAM: Program = {
  id: 'it-python',
  title: 'Python для анализа данных',
  type: 'Повышение квалификации',
  direction: 'IT и аналитика',
  hours: 24,
  status: 'approved',
  theme: 'it',
  updatedAt: '2025-10-12T12:00:00Z',
  description: 'Практический курс по анализу данных.',
  goals: ['Освоить Python.', 'Научиться анализировать данные.'],
  modules: [
    {
      id: 'm-1',
      title: 'Введение',
      summary: 'Основы Python',
      hours: 22
    }
  ],
  competencyStatements: ['Анализировать данные на Python.']
};

describe('ProgramDetailPanelComponent', () => {
  let fixture: ComponentFixture<ProgramDetailPanelComponent>;
  let component: ProgramDetailPanelComponent;
  let opened: number;
  let closed: number;
  let main: HTMLElement;

  const detect = () => {
    fixture.detectChanges();
    TestBed.tick();
  };

  const setProgram = (program: Program | null) => {
    fixture.componentRef.setInput('program', program);
    detect();
  };

  const panel = () => fixture.debugElement.query(By.css('.program-panel'));
  const closeButton = () => fixture.debugElement.query(By.css('.program-panel__close'));
  const tab = (label: string) =>
    fixture.debugElement
      .queryAll(By.css('.program-panel__tab'))
      .find((item) => item.nativeElement.textContent.trim() === label);
  const content = () => fixture.debugElement.query(By.css('.program-panel__content'));
  const scroll = () => fixture.debugElement.query(By.css('.program-panel__scroll'));
  const action = (label: string) =>
    fixture.debugElement
      .queryAll(By.css('.program-panel__action'))
      .find((item) => item.nativeElement.textContent.trim() === label);

  const selectTab = (label: string) => {
    tab(label)?.nativeElement.click();
    detect();
  };

  beforeEach(() => {
    main = document.createElement('main');
    document.body.appendChild(main);

    TestBed.configureTestingModule({ imports: [ProgramDetailPanelComponent] });
    fixture = TestBed.createComponent(ProgramDetailPanelComponent);
    component = fixture.componentInstance;
    opened = 0;
    closed = 0;
    component.open.subscribe(() => opened++);
    component.closed.subscribe(() => closed++);
    fixture.componentRef.setInput('program', PROGRAM);
    detect();
  });

  afterEach(() => {
    main.remove();
    document.body.style.overflow = '';
  });

  it('renders the selected program with its metadata and goals', () => {
    const text = fixture.nativeElement.textContent as string;

    expect(panel().attributes['role']).toBeUndefined();
    expect(panel().attributes['aria-modal']).toBeUndefined();
    expect(panel().attributes['aria-labelledby']).toBe('program-panel-title');
    expect(text).toContain('Python для анализа данных');
    expect(text).toContain('Согласована');
    expect(text).toContain('24 часа');
    expect(text).toContain('Повышение квалификации');
    expect(text).toContain('IT и аналитика');
    expect(text).toContain('Практический курс по анализу данных.');
    expect(text).toContain('Освоить Python.');
    expect(
      fixture.debugElement.query(By.css('.program-panel__hero')).attributes['data-theme']
    ).toBe('it');
  });

  it('renders nothing while no program is selected', () => {
    setProgram(null);

    expect(panel()).toBeNull();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(closed).toBe(0);
  });

  it('switches between the implemented tabs', () => {
    expect(tab('Описание')?.attributes['aria-selected']).toBe('true');
    expect(tab('Описание')?.attributes['aria-controls']).toBe('program-panel-content');
    expect(content().attributes['id']).toBe('program-panel-content');
    expect(content().nativeElement.textContent).toContain('Описание программы');

    selectTab('Структура');
    expect(tab('Структура')?.attributes['aria-selected']).toBe('true');
    expect(tab('Структура')?.attributes['aria-controls']).toBe('program-panel-content');
    expect(tab('Описание')?.attributes['aria-controls']).toBeUndefined();
    expect(content().nativeElement.textContent).toContain('Структура программы');
    expect(fixture.debugElement.queryAll(By.css('.program-panel__module')).length).toBe(1);
    expect(content().nativeElement.textContent).toContain('Введение');
    expect(content().nativeElement.textContent).toContain('Итого: 24 часа');

    selectTab('Компетенции');
    expect(content().nativeElement.textContent).toContain('Компетенции');
    expect(fixture.debugElement.queryAll(By.css('.program-panel__competency')).length).toBe(1);
    expect(content().nativeElement.textContent).toContain('Анализировать данные на Python.');
  });

  it('does not render unavailable tabs, menus or actions', () => {
    expect(tab('Материалы')).toBeUndefined();
    expect(fixture.debugElement.query(By.css('.program-panel__menu'))).toBeNull();
    expect(fixture.debugElement.query(By.css('.program-panel__material'))).toBeNull();
    expect(
      fixture.debugElement
        .queryAll(By.css('.program-panel__action'))
        .map((item) => item.nativeElement.textContent.trim())
    ).toEqual(['Открыть программу']);
  });

  it('navigates tabs with the keyboard', () => {
    const activeTab = () =>
      fixture.debugElement.query(By.css('.program-panel__tab[aria-selected="true"]'));

    const press = (key: string) => {
      activeTab().triggerEventHandler('keydown', new KeyboardEvent('keydown', { key }));
      detect();
    };

    press('ArrowRight');

    expect(tab('Структура')?.attributes['aria-selected']).toBe('true');
    expect(document.activeElement).toBe(tab('Структура')?.nativeElement);

    press('End');

    expect(tab('Компетенции')?.attributes['aria-selected']).toBe('true');
    expect(document.activeElement).toBe(tab('Компетенции')?.nativeElement);

    press('ArrowRight');

    expect(tab('Описание')?.attributes['aria-selected']).toBe('true');

    press('ArrowLeft');

    expect(tab('Компетенции')?.attributes['aria-selected']).toBe('true');

    press('Home');

    expect(tab('Описание')?.attributes['aria-selected']).toBe('true');

    press('PageDown');

    expect(tab('Описание')?.attributes['aria-selected']).toBe('true');
  });

  it('returns to the description tab when another program is selected', () => {
    selectTab('Структура');

    setProgram({ ...PROGRAM, id: 'other', title: 'Другая программа' });

    expect(fixture.nativeElement.textContent).toContain('Другая программа');
    expect(tab('Описание')?.attributes['aria-selected']).toBe('true');
    expect(content().nativeElement.textContent).toContain('Практический курс по анализу данных.');
  });

  it('scrolls back to the top when another program is selected', () => {
    const container = scroll().nativeElement as HTMLElement;
    container.scrollTop = 240;

    setProgram({ ...PROGRAM, id: 'other', title: 'Другая программа' });

    expect(container.scrollTop).toBe(0);
  });

  it('shows truthful empty states before the blocks are formed', () => {
    setProgram({
      ...PROGRAM,
      type: '',
      direction: '',
      hours: 0,
      description: '',
      goals: [],
      modules: undefined,
      competencyStatements: undefined
    });

    expect(fixture.debugElement.query(By.css('.program-panel__meta'))).toBeNull();
    expect(content().nativeElement.textContent).toContain('Описание ещё не сформировано.');
    expect(content().nativeElement.textContent).toContain('Цели ещё не сформированы.');

    selectTab('Структура');
    expect(content().nativeElement.textContent).toContain('Структура ещё не сформирована.');

    selectTab('Компетенции');
    expect(content().nativeElement.textContent).toContain('Компетенции ещё не сформированы.');
  });

  it('closes from the close button and Escape', () => {
    closeButton().nativeElement.click();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(closed).toBe(2);
  });

  it('focuses the close button when the panel opens', () => {
    expect(document.activeElement).toBe(closeButton().nativeElement);
  });

  it('leaves the page scroll available while open', () => {
    expect(document.body.style.overflow).toBe('');
    expect(main.style.overflow).toBe('');
  });

  it('emits open from the footer action', () => {
    action('Открыть программу')?.nativeElement.click();
    detect();

    expect(opened).toBe(1);
    expect(closed).toBe(0);
  });
});
