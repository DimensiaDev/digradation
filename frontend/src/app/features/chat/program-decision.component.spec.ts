import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ProgramRevisionRequest } from '../../core/models/models';
import { ProgramDecisionBlock } from './program-view';
import { ProgramDecisionComponent } from './program-decision.component';

const BLOCKS: readonly ProgramDecisionBlock[] = [
  { id: 'competences', label: 'Компетенции' },
  { id: 'hours', label: 'Учебный план и часы' }
];

describe('ProgramDecisionComponent', () => {
  let fixture: ComponentFixture<ProgramDecisionComponent>;
  let component: ProgramDecisionComponent;

  const button = (selector: string): HTMLButtonElement =>
    fixture.nativeElement.querySelector(selector) as HTMLButtonElement;
  const reviseAction = (): HTMLButtonElement => button('.decision__action.button--outline');
  const approveAction = (): HTMLButtonElement => button('.decision__action.button--main');

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ProgramDecisionComponent]
    }).compileComponents();
    fixture = TestBed.createComponent(ProgramDecisionComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('blocks', BLOCKS);
    fixture.detectChanges();
  });

  it('offers an approval and a revision', () => {
    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Каркас программы готов');
    expect(text).toContain('Утвердить каркас');
    expect(text).toContain('Запросить доработку');

    // The block picker belongs to the revision, so it is not shown yet.
    expect(fixture.nativeElement.querySelector('.decision__blocks')).toBeNull();
  });

  it('emits an approval', () => {
    const approved = vi.fn();
    component.approved.subscribe(approved);

    approveAction().click();

    expect(approved).toHaveBeenCalledTimes(1);
  });

  it('shows one card at a time', () => {
    const cards = () => fixture.nativeElement.querySelectorAll('.decision__card');
    expect(cards()).toHaveLength(2);

    reviseAction().click();
    fixture.detectChanges();

    expect(component.mode()).toBe('revise');
    expect(cards()).toHaveLength(1);
    expect(fixture.nativeElement.textContent).not.toContain('Утвердить каркас');

    button('.decision__cancel').click();
    fixture.detectChanges();

    expect(component.mode()).toBe('choose');
    expect(cards()).toHaveLength(2);
    expect(fixture.nativeElement.textContent).toContain('Утвердить каркас');
  });

  it('hides the revision while the approval is being sent', () => {
    approveAction().click();
    fixture.componentRef.setInput('submitting', true);
    fixture.detectChanges();

    expect(component.mode()).toBe('approve');
    expect(fixture.nativeElement.querySelectorAll('.decision__card')).toHaveLength(1);
    expect(fixture.nativeElement.textContent).toContain('Утверждаем…');
  });

  it('brings the revision back when a refused approval settles', () => {
    approveAction().click();
    fixture.componentRef.setInput('submitting', true);
    fixture.detectChanges();
    expect(component.mode()).toBe('approve');

    fixture.componentRef.setInput('submitting', false);
    fixture.detectChanges();

    expect(component.mode()).toBe('choose');
    expect(fixture.nativeElement.querySelectorAll('.decision__card')).toHaveLength(2);
  });

  it('expands the revision form and emits the chosen block with the notes', () => {
    const revised: ProgramRevisionRequest[] = [];
    component.revised.subscribe((request) => revised.push(request));

    reviseAction().click();
    fixture.detectChanges();

    const chips = fixture.nativeElement.querySelectorAll('.decision__block');
    expect(chips).toHaveLength(2);
    expect(component.selectedStage()).toBe('competences');

    (chips[1] as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(component.selectedStage()).toBe('hours');

    component.notes.set('Добавить кейс в тему 2.3');
    fixture.detectChanges();

    button('.decision__submit').click();

    expect(revised).toEqual([{ stage: 'hours', notes: 'Добавить кейс в тему 2.3' }]);
  });

  it('refuses to submit a revision without notes', () => {
    reviseAction().click();
    fixture.detectChanges();

    expect(button('.decision__submit').disabled).toBe(true);

    component.notes.set('   ');
    fixture.detectChanges();
    expect(button('.decision__submit').disabled).toBe(true);

    component.notes.set('Переписать цель');
    fixture.detectChanges();
    expect(button('.decision__submit').disabled).toBe(false);
  });

  it('submits a revision on Enter and keeps Shift+Enter for a line break', () => {
    const revised: ProgramRevisionRequest[] = [];
    component.revised.subscribe((request) => revised.push(request));
    component.startRevision();
    component.notes.set('Переписать цель');
    fixture.detectChanges();

    component.onNotesKeydown(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true }));
    expect(revised).toEqual([]);

    const event = new KeyboardEvent('keydown', { key: 'Enter', cancelable: true });
    component.onNotesKeydown(event);

    expect(event.defaultPrevented).toBe(true);
    expect(revised).toEqual([{ stage: 'competences', notes: 'Переписать цель' }]);
  });

  it('cancels a revision and returns to the choice', () => {
    component.startRevision();
    component.notes.set('Переписать цель');
    fixture.detectChanges();

    button('.decision__cancel').click();
    fixture.detectChanges();

    expect(component.mode()).toBe('choose');
    expect(component.notes()).toBe('');
    expect(fixture.nativeElement.querySelector('.decision__blocks')).toBeNull();
  });

  it('shows the last revision that was carried out', () => {
    fixture.componentRef.setInput('lastRevision', {
      stageLabel: 'Компетенции',
      notes: 'Добавить работу с данными'
    });
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('Прошлая правка');
    expect(fixture.nativeElement.textContent).toContain('Компетенции');
    expect(fixture.nativeElement.textContent).toContain('Добавить работу с данными');
  });

  it('keeps the approval disabled while a decision is being sent', () => {
    fixture.componentRef.setInput('submitting', true);
    fixture.detectChanges();

    expect(approveAction().disabled).toBe(true);
    expect(reviseAction().disabled).toBe(true);
  });

  it('resets the revision form when the block set changes', () => {
    component.startRevision();
    component.notes.set('Переписать цель');
    fixture.detectChanges();

    fixture.componentRef.setInput('blocks', [{ id: 'summary', label: 'Паспорт программы' }]);
    fixture.detectChanges();

    expect(component.mode()).toBe('choose');
    expect(component.selectedStage()).toBe('summary');
    expect(component.notes()).toBe('');
  });
});
