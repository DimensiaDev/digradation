import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { ProgramsService } from '../../core/services/programs.service';
import { ProgramsPage } from './programs-page.component';

describe('ProgramsPage', () => {
  let fixture: ComponentFixture<ProgramsPage>;
  let service: ProgramsService;
  let router: Router;

  const cards = () => fixture.debugElement.queryAll(By.css('.program-card'));
  const cardTitles = () =>
    cards().map((card) =>
      card.query(By.css('.program-card__title')).nativeElement.textContent.trim()
    );
  const statuses = () => cards().map((card) => card.attributes['data-status']);
  const chips = () => fixture.debugElement.queryAll(By.css('.filter-chip'));
  const activeChip = () => fixture.debugElement.query(By.css('.filter-chip--active'));
  const emptyState = () => fixture.debugElement.query(By.css('.programs-page__empty'));

  const chip = (label: string) => {
    const found = chips().find((item) => item.nativeElement.textContent.trim() === label);
    if (!found) {
      throw new Error(`Filter chip "${label}" not found`);
    }
    return found;
  };

  const selectFilter = (label: string) => {
    chip(label).nativeElement.click();
    fixture.detectChanges();
  };

  const openFirstMenu = () => {
    const button = fixture.debugElement.queryAll(By.css('.program-card__menu-button'))[0];
    button.nativeElement.click();
    fixture.detectChanges();
  };

  const choose = (label: string) => {
    openFirstMenu();
    const item = fixture.debugElement
      .queryAll(By.css('.program-card__menu-item'))
      .find((entry) => entry.nativeElement.textContent.trim() === label);
    if (!item) {
      throw new Error(`Menu item "${label}" not found`);
    }
    item.nativeElement.click();
    fixture.detectChanges();
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [ProgramsPage],
      providers: [provideRouter([])]
    });

    fixture = TestBed.createComponent(ProgramsPage);
    fixture.detectChanges();
    service = TestBed.inject(ProgramsService);
    router = TestBed.inject(Router);
  });

  it('renders the section heading and the mock catalog', () => {
    const heading = fixture.debugElement.query(By.css('h1'));

    expect(heading.nativeElement.textContent.trim()).toBe('Мои программы');
    expect(cardTitles().length).toBe(8);
    expect(cardTitles()[0]).toBe('Python для анализа данных');
  });

  it('shows every status filter with "Все" active by default', () => {
    expect(chips().map((item) => item.nativeElement.textContent.trim())).toEqual([
      'Все',
      'В работе',
      'На проверке',
      'Опубликовано',
      'Архив'
    ]);
    expect(activeChip().nativeElement.textContent.trim()).toBe('Все');
    expect(activeChip().attributes['aria-pressed']).toBe('true');
  });

  it('filters the cards by status', () => {
    selectFilter('Опубликовано');
    expect(cardTitles().length).toBe(3);
    expect([...new Set(statuses())]).toEqual(['published']);

    selectFilter('В работе');
    expect(cardTitles().length).toBe(2);
    expect([...new Set(statuses())]).toEqual(['in_progress']);

    selectFilter('На проверке');
    expect(cardTitles().length).toBe(1);
    expect([...new Set(statuses())]).toEqual(['review']);

    selectFilter('Архив');
    expect(cardTitles().length).toBe(1);
    expect([...new Set(statuses())]).toEqual(['archived']);
  });

  it('shows drafts only through the "Все" filter', () => {
    expect(cardTitles()).toContain('Управление проектами');

    selectFilter('В работе');

    expect(cardTitles()).not.toContain('Управление проектами');
    expect(statuses()).not.toContain('draft');
  });

  it('links the primary action to the new-program route', () => {
    const link = fixture.debugElement.query(By.css('.programs-page__create'));

    expect(link.nativeElement.getAttribute('href')).toBe('/programs/new');
    expect(link.nativeElement.textContent).toContain('Создать программу');
  });

  it('duplicates a program as a draft and reveals it through "Все"', () => {
    selectFilter('Опубликовано');

    choose('Дублировать');

    expect(service.programs().length).toBe(9);
    expect(activeChip().nativeElement.textContent.trim()).toBe('Все');
    expect(cardTitles()[0]).toBe('Python для анализа данных (копия)');
    expect(statuses()[0]).toBe('draft');
  });

  it('archives a program and shows it under the archive filter', () => {
    choose('Архивировать');

    expect(statuses().filter((status) => status === 'archived').length).toBe(2);

    selectFilter('Архив');
    expect(cardTitles().length).toBe(2);
  });

  it('deletes a program', () => {
    choose('Удалить');

    expect(cardTitles().length).toBe(7);
    expect(service.programs().length).toBe(7);
  });

  it('navigates to the edit placeholder', () => {
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);

    choose('Редактировать');

    expect(navigate).toHaveBeenCalledWith(['/programs/new']);
  });

  it('offers a reset when the selected status is empty', () => {
    selectFilter('На проверке');
    choose('Архивировать');

    expect(cardTitles().length).toBe(0);
    expect(emptyState().nativeElement.textContent).toContain('В этом статусе пока нет программ');

    fixture.debugElement.query(By.css('.programs-page__empty-action')).nativeElement.click();
    fixture.detectChanges();

    expect(activeChip().nativeElement.textContent.trim()).toBe('Все');
    expect(cardTitles().length).toBe(8);
  });

  it('shows the empty catalog state after every program is deleted', () => {
    for (let index = 0; index < 8; index++) {
      choose('Удалить');
    }

    expect(cardTitles().length).toBe(0);
    expect(emptyState().nativeElement.textContent).toContain('Пока нет программ');

    const createLink = fixture.debugElement.query(By.css('.programs-page__empty-action'));
    expect(createLink.nativeElement.getAttribute('href')).toBe('/programs/new');
  });
});
