import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Program } from '../../../core/models/models';
import { ProgramAction, ProgramCardComponent } from './program-card.component';

const PROGRAM: Program = {
  id: 'it-python',
  title: 'Python для анализа данных',
  direction: 'Повышение квалификации',
  hours: 72,
  tags: ['IT', 'Python', 'Аналитика'],
  status: 'published',
  theme: 'it',
  author: { name: 'Иванова А. В.', avatarUrl: null },
  updatedAt: '2025-10-12T12:00:00Z'
};

describe('ProgramCardComponent', () => {
  let fixture: ComponentFixture<ProgramCardComponent>;
  let component: ProgramCardComponent;
  let actions: ProgramAction[];

  const menuButton = () => fixture.debugElement.query(By.css('.program-card__menu-button'));
  const menu = () => fixture.debugElement.query(By.css('.program-card__dropdown'));
  const menuItems = () => fixture.debugElement.queryAll(By.css('.program-card__menu-item'));
  const menuItem = (label: string) =>
    menuItems().find((item) => item.nativeElement.textContent.trim() === label);

  const detect = () => fixture.detectChanges();

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
    component.action.subscribe((action) => actions.push(action));
    detect();
  });

  it('renders the program fields', () => {
    const text = fixture.nativeElement.textContent as string;

    expect(text).toContain('Python для анализа данных');
    expect(text).toContain('Повышение квалификации');
    expect(text).toContain('72 ч');
    expect(text).toContain('IT');
    expect(text).toContain('Python');
    expect(text).toContain('Аналитика');
    expect(text).toContain('Иванова А. В.');
    expect(text).toContain('12.10.2025');
    expect(text).toContain('Опубликовано');
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

  it('emits edit and closes the menu', () => {
    choose('Редактировать');

    expect(actions).toEqual(['edit']);
    expect(menu()).toBeNull();
  });

  it('emits duplicate and closes the menu', () => {
    choose('Дублировать');

    expect(actions).toEqual(['duplicate']);
    expect(menu()).toBeNull();
  });

  it('emits archive and closes the menu', () => {
    choose('Архивировать');

    expect(actions).toEqual(['archive']);
    expect(menu()).toBeNull();
  });

  it('emits delete and closes the menu', () => {
    choose('Удалить');

    expect(actions).toEqual(['delete']);
    expect(menu()).toBeNull();
  });

  it('does not offer archiving for an archived program', () => {
    fixture.componentRef.setInput('program', { ...PROGRAM, status: 'archived' });
    detect();
    openMenu();

    expect(menuItem('Архивировать')).toBeUndefined();
    expect(menuItems().length).toBe(3);
  });
});
