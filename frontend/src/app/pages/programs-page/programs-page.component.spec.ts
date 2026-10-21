import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { vi } from 'vitest';
import { ProgramsService } from '../../core/services/programs.service';
import { Select } from '../../shared/components/select/select.component';
import { ProgramsPage } from './programs-page.component';

describe('ProgramsPage', () => {
  let fixture: ComponentFixture<ProgramsPage>;
  let service: ProgramsService;

  const cards = () => fixture.debugElement.queryAll(By.css('.program-card'));
  const cardTitles = () =>
    cards().map((card) =>
      card.query(By.css('.program-card__title')).nativeElement.textContent.trim()
    );
  const statuses = () => cards().map((card) => card.attributes['data-status']);
  const emptyState = () => fixture.debugElement.query(By.css('.programs-page__empty'));
  const detailPanel = () => fixture.debugElement.query(By.css('.program-panel'));
  const openButtons = () => fixture.debugElement.queryAll(By.css('.program-card__open'));
  const filters = () => fixture.debugElement.queryAll(By.directive(Select));
  const pagination = () => fixture.debugElement.query(By.css('.pagination'));
  const paginationNav = () => fixture.debugElement.query(By.css('.pagination__nav'));
  const pageInfo = () => fixture.debugElement.query(By.css('.pagination__info'));
  const resultsRegion = () => fixture.debugElement.query(By.css('.programs-page__results'));
  const navigationButtons = () => fixture.debugElement.queryAll(By.css('.pagination__button'));
  const previousButton = () => navigationButtons()[0];
  const nextButton = () => navigationButtons()[navigationButtons().length - 1];
  const pageButton = (page: number) =>
    navigationButtons().find((button) => button.nativeElement.textContent.trim() === String(page));
  const typeSelect = () => filters()[0].componentInstance as Select;
  const dateSelect = () => filters()[1].componentInstance as Select;
  const tagSelect = () => filters()[2].componentInstance as Select;

  const chooseFilter = (index: number, label: string) => {
    const select = filters()[index];
    if (!(select.componentInstance as Select).isOpen()) {
      select.query(By.css('button[role="combobox"]')).nativeElement.click();
      fixture.detectChanges();
    }

    const option = select
      .queryAll(By.css('li[role="option"]'))
      .find((item) => item.nativeElement.textContent.trim() === label);
    if (!option) {
      throw new Error(`Filter option "${label}" not found`);
    }
    option.nativeElement.click();
    fixture.detectChanges();
  };

  const selectType = (label: string) => chooseFilter(0, label);
  const selectDate = (label: string) => chooseFilter(1, label);
  const selectTag = (label: string) => chooseFilter(2, label);

  const openFirstMenu = () => {
    const button = fixture.debugElement.queryAll(By.css('.program-card__menu-button'))[0];
    button.nativeElement.click();
    fixture.detectChanges();
  };

  const openFirstProgram = () => {
    const button = openButtons()[0];
    button.nativeElement.focus();
    button.nativeElement.click();
    fixture.detectChanges();
    TestBed.tick();
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
      imports: [ProgramsPage]
    });

    fixture = TestBed.createComponent(ProgramsPage);
    fixture.detectChanges();
    service = TestBed.inject(ProgramsService);
  });

  it('renders the section heading and the mock catalog', () => {
    const heading = fixture.debugElement.query(By.css('h1'));

    expect(heading.nativeElement.textContent.trim()).toBe('Программы');
    expect(cardTitles().length).toBe(6);
    expect(cardTitles()[0]).toBe('Python для анализа данных');
    expect(pagination()).not.toBeNull();
  });

  it('paginates the catalog with numbered pages and a range summary', () => {
    expect(pageInfo().nativeElement.textContent).toContain('Показано 1–6 из 8 программ');
    expect(pageButton(1)?.nativeElement.getAttribute('aria-current')).toBe('page');
    expect(previousButton().nativeElement.disabled).toBe(true);

    nextButton().nativeElement.click();
    fixture.detectChanges();

    expect(cardTitles()).toEqual(['Веб-разработка: полный цикл', 'Продуктовая аналитика']);
    expect(pageInfo().nativeElement.textContent).toContain('Показано 7–8 из 8 программ');
    expect(pageButton(2)?.nativeElement.getAttribute('aria-current')).toBe('page');
    expect(previousButton().nativeElement.disabled).toBe(false);
    expect(nextButton().nativeElement.disabled).toBe(true);

    pageButton(1)?.nativeElement.click();
    fixture.detectChanges();

    expect(cardTitles().length).toBe(6);
  });

  it('announces and focuses the results after changing the page', () => {
    const results = resultsRegion().nativeElement as HTMLElement;
    results.scrollIntoView ??= () => undefined;
    const scroll = vi.spyOn(results, 'scrollIntoView');

    expect(pageInfo().nativeElement.getAttribute('aria-live')).toBe('polite');
    expect(pageInfo().nativeElement.getAttribute('aria-atomic')).toBe('true');

    nextButton().nativeElement.click();
    fixture.detectChanges();

    expect(document.activeElement).toBe(results);
    expect(scroll).toHaveBeenCalledWith({ block: 'start', behavior: 'auto' });
  });

  it('scrolls to the results on a filter change without stealing focus', () => {
    const results = resultsRegion().nativeElement as HTMLElement;
    results.scrollIntoView ??= () => undefined;
    const scroll = vi.spyOn(results, 'scrollIntoView');
    const trigger = filters()[0].query(By.css('button[role="combobox"]'))
      .nativeElement as HTMLButtonElement;
    trigger.focus();

    selectType('Повышение квалификации');

    expect(scroll).toHaveBeenCalledWith({ block: 'start', behavior: 'auto' });
    expect(document.activeElement).toBe(trigger);
  });

  it('collapses distant pages into an ellipsis', () => {
    for (let index = 0; index < 43; index++) {
      service.duplicate('it-python');
    }
    fixture.detectChanges();

    pageButton(9)?.nativeElement.click();
    fixture.detectChanges();

    expect(fixture.debugElement.queryAll(By.css('.pagination__ellipsis')).length).toBe(1);
    expect(pageButton(1)).toBeTruthy();
    expect(pageButton(7)).toBeTruthy();
    expect(pageButton(2)).toBeFalsy();
  });

  it('returns to the first page when a filter changes', () => {
    nextButton().nativeElement.click();
    fixture.detectChanges();

    selectType('Повышение квалификации');

    expect(paginationNav()).toBeNull();
    expect(pageInfo().nativeElement.textContent).toContain('Показано 1–3 из 3 программ');
    expect(cardTitles().length).toBe(3);
  });

  it('steps back when the last page disappears', () => {
    nextButton().nativeElement.click();
    fixture.detectChanges();

    choose('Удалить');
    expect(cardTitles().length).toBe(1);

    choose('Удалить');
    expect(cardTitles().length).toBe(6);
    expect(paginationNav()).toBeNull();
    expect(pageInfo().nativeElement.textContent).toContain('Показано 1–6 из 6 программ');
  });

  it('keeps a selected type visible when its last program is deleted', () => {
    selectType('Профессиональная переподготовка');
    expect(cardTitles().length).toBe(2);

    choose('Удалить');
    choose('Удалить');

    expect(typeSelect().value()).toBe('Профессиональная переподготовка');
    expect(typeSelect().selectedLabel()).toBe('Профессиональная переподготовка');
    expect(
      typeSelect()
        .options()
        .map((option) => option.value)
    ).toContain('Профессиональная переподготовка');
    expect(cardTitles().length).toBe(0);
    expect(emptyState().nativeElement.textContent).toContain('По фильтрам ничего не найдено');
  });

  it('keeps a selected month visible when its last program is deleted', () => {
    selectDate('Август 2025');
    expect(cardTitles().length).toBe(2);

    choose('Удалить');
    choose('Удалить');

    expect(dateSelect().selectedLabel()).toBe('Август 2025');
    expect(
      dateSelect()
        .options()
        .some((option) => option.label === 'Август 2025')
    ).toBe(true);
    expect(cardTitles().length).toBe(0);
    expect(emptyState().nativeElement.textContent).toContain('По фильтрам ничего не найдено');
  });

  it('keeps a selected tag visible when its last program is deleted', () => {
    selectTag('Agile');
    expect(cardTitles().length).toBe(2);

    choose('Удалить');
    choose('Удалить');

    expect(tagSelect().value()).toEqual(['Agile']);
    expect(tagSelect().selectedLabels()).toEqual(['Agile']);
    expect(
      tagSelect()
        .options()
        .some((option) => option.value === 'Agile')
    ).toBe(true);
    expect(cardTitles().length).toBe(0);
    expect(emptyState().nativeElement.textContent).toContain('По фильтрам ничего не найдено');
  });

  it('shows the catalog filters with all values selected by default', () => {
    expect(filters().length).toBe(3);
    expect(typeSelect().value()).toBe('all');
    expect(dateSelect().value()).toBe('all');
    expect(tagSelect().value()).toEqual([]);
    expect(
      typeSelect()
        .options()
        .map((option) => option.label)
    ).toEqual([
      'Все типы',
      'Повышение квалификации',
      'Корпоративное обучение',
      'Профессиональная переподготовка'
    ]);
    expect(
      dateSelect()
        .options()
        .map((option) => option.label)
    ).toEqual(['Все даты', 'Октябрь 2025', 'Сентябрь 2025', 'Август 2025']);
  });

  it('filters the cards by program type', () => {
    selectType('Повышение квалификации');
    expect(cardTitles().length).toBe(3);
    expect(cardTitles()).not.toContain('Кибербезопасность для сотрудников');

    selectType('Профессиональная переподготовка');
    expect(cardTitles().length).toBe(2);
    expect(cardTitles()).toContain('Основы машинного обучения');
    expect(cardTitles()).toContain('Веб-разработка: полный цикл');
  });

  it('filters the cards by update date', () => {
    selectDate('Октябрь 2025');
    expect(cardTitles().length).toBe(3);

    selectDate('Сентябрь 2025');
    expect(cardTitles().length).toBe(3);

    selectDate('Август 2025');
    expect(cardTitles().length).toBe(2);
  });

  it('filters the cards by technology tag', () => {
    selectTag('Python');
    expect(cardTitles()).toEqual(['Python для анализа данных', 'Основы машинного обучения']);
  });

  it('combines several technology tags', () => {
    selectTag('Python');
    selectTag('Agile');

    expect(cardTitles()).toEqual([
      'Python для анализа данных',
      'Основы машинного обучения',
      'Agile-трансформация команд',
      'Управление проектами'
    ]);
    expect(tagSelect().selectedLabels()).toEqual(['Python', 'Agile']);
  });

  it('combines the type and date filters', () => {
    selectType('Корпоративное обучение');
    selectDate('Сентябрь 2025');

    expect(cardTitles()).toEqual(['Agile-трансформация команд', 'Soft skills для руководителей']);
  });

  it('keeps drafts visible when no filters are applied', () => {
    expect(cardTitles()).toContain('Управление проектами');
    expect(statuses()).toContain('draft');
  });

  it('does not render a create-program action', () => {
    expect(fixture.debugElement.query(By.css('.programs-page__create'))).toBeNull();
    expect(fixture.debugElement.query(By.css('a[href="/programs/new"]'))).toBeNull();
  });

  it('duplicates a program as a draft and resets the filters', async () => {
    selectType('Повышение квалификации');
    selectDate('Август 2025');
    selectTag('Аналитика');

    choose('Дублировать');
    await fixture.whenStable();
    fixture.detectChanges();

    expect(service.programs().length).toBe(9);
    expect(typeSelect().value()).toBe('all');
    expect(dateSelect().value()).toBe('all');
    expect(tagSelect().value()).toEqual([]);
    expect(cardTitles()[0]).toBe('Продуктовая аналитика (копия)');
    expect(statuses()[0]).toBe('draft');
  });

  it('returns focus to the card trigger after duplicating', () => {
    const trigger = fixture.debugElement.queryAll(By.css('.program-card__menu-button'))[0]
      .nativeElement as HTMLButtonElement;

    choose('Дублировать');
    TestBed.tick();
    fixture.detectChanges();

    expect(document.activeElement).toBe(trigger);
  });

  it('archives a program', () => {
    choose('Архивировать');

    expect(statuses()[0]).toBe('archived');
    expect(service.programs().filter((program) => program.status === 'archived').length).toBe(2);
  });

  it('returns focus to the card trigger after archiving', () => {
    const trigger = fixture.debugElement.queryAll(By.css('.program-card__menu-button'))[0]
      .nativeElement as HTMLButtonElement;

    choose('Архивировать');
    TestBed.tick();
    fixture.detectChanges();

    expect(document.activeElement).toBe(trigger);
  });

  it('deletes a program', () => {
    choose('Удалить');

    expect(cardTitles().length).toBe(6);
    expect(service.programs().length).toBe(7);
  });

  it('moves focus to the next card after deleting one', () => {
    const triggers = fixture.debugElement.queryAll(By.css('.program-card__menu-button'));
    const nextTrigger = triggers[1].nativeElement as HTMLButtonElement;

    choose('Удалить');
    TestBed.tick();
    fixture.detectChanges();

    expect(document.activeElement).toBe(nextTrigger);
  });

  it('offers a reset when the selected filters are empty', async () => {
    selectType('Корпоративное обучение');
    selectTag('Python');

    expect(cardTitles().length).toBe(0);
    expect(emptyState().nativeElement.textContent).toContain('По фильтрам ничего не найдено');
    expect(emptyState().nativeElement.getAttribute('aria-live')).toBe('polite');
    expect(emptyState().nativeElement.getAttribute('aria-atomic')).toBe('true');

    fixture.debugElement.query(By.css('.programs-page__empty-action')).nativeElement.click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(typeSelect().value()).toBe('all');
    expect(dateSelect().value()).toBe('all');
    expect(tagSelect().value()).toEqual([]);
    expect(cardTitles().length).toBe(6);
    expect(document.activeElement).toBe(resultsRegion().nativeElement);
  });

  it('shows the empty catalog state after every program is deleted', () => {
    for (let index = 0; index < 8; index++) {
      choose('Удалить');
    }

    expect(cardTitles().length).toBe(0);
    expect(emptyState().nativeElement.textContent).toContain('Пока нет программ');

    expect(fixture.debugElement.query(By.css('.programs-page__empty-action'))).toBeNull();

    TestBed.tick();
    fixture.detectChanges();
    expect(document.activeElement).toBe(resultsRegion().nativeElement);
  });

  it('opens the detail panel from a program card', () => {
    openFirstProgram();

    expect(detailPanel()).toBeTruthy();
    expect(detailPanel().nativeElement.textContent).toContain('Python для анализа данных');
    expect(detailPanel().nativeElement.textContent).toContain('72 часа');
    expect(detailPanel().nativeElement.textContent).toContain('Практический курс');
  });

  it('closes the panel and restores focus to the card', () => {
    const trigger = openButtons()[0].nativeElement as HTMLButtonElement;
    openFirstProgram();

    fixture.debugElement.query(By.css('.program-panel__close')).nativeElement.click();
    fixture.detectChanges();
    TestBed.tick();
    fixture.detectChanges();

    expect(detailPanel()).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('keeps the panel menu actions visual-only', () => {
    openFirstProgram();

    fixture.debugElement
      .query(By.css('.program-panel__menu--header .program-panel__menu-button'))
      .nativeElement.click();
    fixture.detectChanges();

    const duplicate = fixture.debugElement
      .queryAll(By.css('.program-panel__menu-item'))
      .find((item) => item.nativeElement.textContent.trim() === 'Дублировать');
    duplicate?.nativeElement.click();
    fixture.detectChanges();

    expect(service.programs().length).toBe(8);
    expect(detailPanel()).toBeTruthy();
  });

  it('reflects an archived status in the open panel', () => {
    openFirstProgram();

    service.archive('it-python');
    fixture.detectChanges();
    TestBed.tick();
    fixture.detectChanges();

    expect(detailPanel().nativeElement.textContent).toContain('Архив');
  });

  it('closes the panel when the selected program disappears', () => {
    openFirstProgram();

    service.remove('it-python');
    fixture.detectChanges();
    TestBed.tick();
    fixture.detectChanges();

    expect(detailPanel()).toBeNull();
  });
});
