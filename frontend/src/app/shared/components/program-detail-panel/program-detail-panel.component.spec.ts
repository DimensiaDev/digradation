import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Program } from '../../../core/models/models';
import { ProgramDetailPanelComponent } from './program-detail-panel.component';

const PROGRAM: Program = {
  id: 'it-python',
  title: 'Python для анализа данных',
  type: 'Повышение квалификации',
  direction: 'IT и аналитика',
  hours: 72,
  tags: ['IT', 'Python', 'Аналитика'],
  status: 'published',
  theme: 'it',
  author: { name: 'Иванова А. В.', avatarUrl: null },
  updatedAt: '2025-10-12T12:00:00Z',
  description: 'Практический курс по анализу данных.',
  goals: ['Освоить Python.', 'Научиться анализировать данные.']
};

describe('ProgramDetailPanelComponent', () => {
  let fixture: ComponentFixture<ProgramDetailPanelComponent>;
  let component: ProgramDetailPanelComponent;
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
  const menu = () => fixture.debugElement.query(By.css('.program-panel__dropdown'));
  const scroll = () => fixture.debugElement.query(By.css('.program-panel__scroll'));
  const menuButton = () =>
    fixture.debugElement.query(By.css('.program-panel__menu--header .program-panel__menu-button'));
  const menuItems = () => fixture.debugElement.queryAll(By.css('.program-panel__menu-item'));
  const menuItem = (label: string) =>
    menuItems().find((item) => item.nativeElement.textContent.trim() === label);
  const action = (label: string) =>
    fixture.debugElement
      .queryAll(By.css('.program-panel__action'))
      .find((item) => item.nativeElement.textContent.trim() === label);

  const openMenu = () => {
    menuButton().nativeElement.click();
    detect();
  };

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
    closed = 0;
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
    expect(text).toContain('Опубликовано');
    expect(text).toContain('72 часа');
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

  it('switches between every demo tab', () => {
    expect(tab('Описание')?.attributes['aria-selected']).toBe('true');
    expect(tab('Описание')?.attributes['aria-controls']).toBe('program-panel-content');
    expect(content().attributes['id']).toBe('program-panel-content');
    expect(content().nativeElement.textContent).toContain('Описание программы');

    selectTab('Структура');
    expect(tab('Структура')?.attributes['aria-selected']).toBe('true');
    expect(tab('Структура')?.attributes['aria-controls']).toBe('program-panel-content');
    expect(tab('Описание')?.attributes['aria-controls']).toBeUndefined();
    expect(content().nativeElement.textContent).toContain('Структура программы');
    expect(fixture.debugElement.queryAll(By.css('.program-panel__module')).length).toBe(4);
    expect(content().nativeElement.textContent).toContain('Итого: 72 часа');

    selectTab('Компетенции');
    expect(content().nativeElement.textContent).toContain('Компетенции');
    expect(fixture.debugElement.queryAll(By.css('.program-panel__competency')).length).toBe(3);
    expect(content().nativeElement.textContent).toContain('IT, Python, Аналитика');

    selectTab('Материалы');
    expect(content().nativeElement.textContent).toContain('Материалы');
    expect(fixture.debugElement.queryAll(By.css('.program-panel__material')).length).toBe(4);
    expect(content().nativeElement.textContent).toContain('Программа курса.pdf');
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

    expect(tab('Материалы')?.attributes['aria-selected']).toBe('true');
    expect(document.activeElement).toBe(tab('Материалы')?.nativeElement);

    press('ArrowRight');

    expect(tab('Описание')?.attributes['aria-selected']).toBe('true');

    press('ArrowLeft');

    expect(tab('Материалы')?.attributes['aria-selected']).toBe('true');

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

  it('opens the header menu and closes it on an outside click', () => {
    openMenu();

    expect(menu()).toBeTruthy();
    expect(menuButton().attributes['aria-expanded']).toBe('true');

    document.body.click();
    detect();

    expect(menu()).toBeNull();
    expect(menuButton().attributes['aria-expanded']).toBe('false');
  });

  it('closes the menu on Escape before closing the panel', () => {
    openMenu();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    detect();

    expect(menu()).toBeNull();
    expect(closed).toBe(0);
    expect(document.activeElement).toBe(menuButton().nativeElement);
  });

  it('keeps the demo menu actions visual-only', () => {
    openMenu();

    menuItem('Дублировать')?.nativeElement.click();
    detect();

    expect(menu()).toBeNull();
    expect(component.program()).toBe(PROGRAM);
    expect(closed).toBe(0);
  });

  it('hides the archive action for an archived program', () => {
    setProgram({ ...PROGRAM, status: 'archived' });
    openMenu();

    expect(fixture.nativeElement.textContent).toContain('Архив');
    expect(menuItem('Архивировать')).toBeUndefined();
    expect(menuItems().length).toBe(2);
  });

  it('keeps the footer action buttons visually only', () => {
    action('Открыть программу')?.nativeElement.click();
    action('Редактировать')?.nativeElement.click();
    detect();

    expect(panel()).toBeTruthy();
    expect(closed).toBe(0);
  });
});
