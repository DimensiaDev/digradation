import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Program } from '../../../core/models/models';
import { ProgramActionEvent, ProgramCardComponent } from './program-card.component';

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

describe('ProgramCardComponent', () => {
  let fixture: ComponentFixture<ProgramCardComponent>;
  let component: ProgramCardComponent;
  let actions: ProgramActionEvent[];
  let opens: number;
  let openTriggers: (HTMLElement | null)[];

  const menuButton = () => fixture.debugElement.query(By.css('.program-card__menu-button'));
  const menu = () => fixture.debugElement.query(By.css('.program-card__dropdown'));
  const menuItems = () => fixture.debugElement.queryAll(By.css('.program-card__menu-item'));
  const menuItem = (label: string) =>
    menuItems().find((item) => item.nativeElement.textContent.trim() === label);

  const detect = () => {
    fixture.detectChanges();
    TestBed.tick();
  };

  const openMenu = () => {
    menuButton().nativeElement.click();
    detect();
  };

  const choose = (label: string) => {
    openMenu();
    const item = menuItem(label);
    if (!item) {
      throw new Error(`Menu item "${label}" not found`);
    }
    item.nativeElement.click();
    detect();
  };

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [ProgramCardComponent] });
    fixture = TestBed.createComponent(ProgramCardComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('program', PROGRAM);
    actions = [];
    opens = 0;
    openTriggers = [];
    component.action.subscribe((event) => actions.push(event));
    component.open.subscribe((trigger) => {
      opens++;
      openTriggers.push(trigger);
    });
    detect();
  });

  it('renders the program fields', () => {
    const text = fixture.nativeElement.textContent as string;

    expect(text).toContain('Python для анализа данных');
    expect(text).toContain('Повышение квалификации');
    expect(text).not.toContain('IT и аналитика');
    expect(text).toContain('72 ч');
    expect(text).toContain('IT');
    expect(text).toContain('Python');
    expect(text).toContain('Аналитика');
    expect(text).toContain('Иванова А. В.');
    expect(text).toContain('12.10.2025');
    expect(text).toContain('Опубликовано');
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
    expect(card.attributes['data-status']).toBe('published');
    expect(watermark).toBeTruthy();
    expect(watermark.attributes['aria-hidden']).toBe('true');
  });

  it('renders author initials when there is no avatar', () => {
    const initials = fixture.debugElement.query(By.css('.program-card__avatar--initials'));

    expect(initials.nativeElement.textContent.trim()).toBe('ИА');
  });

  it('renders the avatar image when the author has one', () => {
    fixture.componentRef.setInput('program', {
      ...PROGRAM,
      author: { ...PROGRAM.author, avatarUrl: '/avatar.png' }
    });
    detect();

    const avatar = fixture.debugElement.query(By.css('img.program-card__avatar'));
    expect(avatar.attributes['src']).toBe('/avatar.png');
    expect(fixture.debugElement.query(By.css('.program-card__avatar--initials'))).toBeNull();
  });

  it('keeps the context menu closed until the trigger is clicked', () => {
    expect(menu()).toBeNull();
    expect(menuButton().attributes['aria-expanded']).toBe('false');

    openMenu();

    expect(menu()).toBeTruthy();
    expect(menuButton().attributes['aria-expanded']).toBe('true');
    expect(menuButton().attributes['aria-controls']).toBe(menu().nativeElement.id);
  });

  it('closes the menu on a click outside the card', () => {
    openMenu();

    document.body.click();
    detect();

    expect(menu()).toBeNull();
  });

  it('closes the menu on Escape and restores focus to the trigger', () => {
    openMenu();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    detect();

    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(menuButton().nativeElement);
  });

  it('moves through the menu with the arrow keys, Home and End', () => {
    openMenu();
    const items = menuItems().map((item) => item.nativeElement as HTMLButtonElement);

    expect(document.activeElement).toBe(items[0]);
    expect(items[0].getAttribute('tabindex')).toBe('0');
    expect(items[1].getAttribute('tabindex')).toBe('-1');

    const press = (key: string) => {
      (document.activeElement as HTMLElement).dispatchEvent(
        new KeyboardEvent('keydown', { key, bubbles: true })
      );
      detect();
    };

    press('ArrowDown');
    expect(document.activeElement).toBe(items[1]);
    expect(items[1].getAttribute('tabindex')).toBe('0');
    expect(items[0].getAttribute('tabindex')).toBe('-1');

    press('End');
    expect(document.activeElement).toBe(items[2]);

    press('ArrowDown');
    expect(document.activeElement).toBe(items[0]);

    press('ArrowUp');
    expect(document.activeElement).toBe(items[2]);

    press('Home');
    expect(document.activeElement).toBe(items[0]);
  });

  it('emits duplicate and closes the menu', () => {
    choose('Дублировать');

    expect(actions.map((event) => event.action)).toEqual(['duplicate']);
    expect(actions[0].trigger).toBe(menuButton().nativeElement);
    expect(menu()).toBeNull();
  });

  it('emits archive and closes the menu', () => {
    choose('Архивировать');

    expect(actions.map((event) => event.action)).toEqual(['archive']);
    expect(actions[0].trigger).toBe(menuButton().nativeElement);
    expect(menu()).toBeNull();
  });

  it('emits delete and closes the menu', () => {
    choose('Удалить');

    expect(actions.map((event) => event.action)).toEqual(['delete']);
    expect(actions[0].trigger).toBe(menuButton().nativeElement);
    expect(menu()).toBeNull();
  });

  it('does not offer archiving for an archived program', () => {
    fixture.componentRef.setInput('program', { ...PROGRAM, status: 'archived' });
    detect();
    openMenu();

    expect(menuItem('Архивировать')).toBeUndefined();
    expect(menuItems().length).toBe(2);
  });
});
