import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ProgramAnswerApi, ProgramQuestionApi } from '../../core/models/models';
import { ChatQuestionFormComponent } from './chat-question-form.component';

const QUESTIONS: readonly ProgramQuestionApi[] = [
  { id: 'q-1', text: 'Для кого программа?' },
  { id: 'q-2', text: 'Сколько часов нужно?' }
];

describe('ChatQuestionFormComponent', () => {
  let fixture: ComponentFixture<ChatQuestionFormComponent>;
  let component: ChatQuestionFormComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChatQuestionFormComponent]
    }).compileComponents();
    fixture = TestBed.createComponent(ChatQuestionFormComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('questions', QUESTIONS);
    fixture.detectChanges();
  });

  it('shows the current question and the progress', () => {
    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Вопрос 1 из 2');
    expect(text).toContain('Для кого программа?');
    expect(text).not.toContain('Сколько часов нужно?');
  });

  it('moves forward only after an answer and keeps answers when going back', () => {
    const next = () =>
      (fixture.nativeElement.querySelector('.question-form__next') as HTMLButtonElement).click();

    expect(
      (fixture.nativeElement.querySelector('.question-form__next') as HTMLButtonElement).disabled
    ).toBe(true);

    component.setAnswer('Для руководителей');
    fixture.detectChanges();
    next();
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('Сколько часов нужно?');
    expect(fixture.nativeElement.textContent).toContain('Вопрос 2 из 2');

    (fixture.nativeElement.querySelector('.question-form__back') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Для кого программа?');
    expect(component.answerFor(QUESTIONS[0])).toBe('Для руководителей');
  });

  it('emits every answer at once on the last step', () => {
    const submitted: (readonly ProgramAnswerApi[])[] = [];
    component.answersSubmitted.subscribe((answers) => submitted.push(answers));

    component.setAnswer('Для руководителей');
    component.next();
    component.setAnswer('72 часа');
    fixture.detectChanges();

    const submit = fixture.nativeElement.querySelector('.question-form__next') as HTMLButtonElement;
    expect(submit.textContent).toContain('Отправить ответы');
    submit.click();

    expect(submitted).toEqual([
      [
        { question_id: 'q-1', text: 'Для руководителей' },
        { question_id: 'q-2', text: '72 часа' }
      ]
    ]);
  });

  it('does not submit while an earlier answer is empty', () => {
    const submitted: unknown[] = [];
    component.answersSubmitted.subscribe((answers) => submitted.push(answers));

    component.next();
    component.setAnswer('72 часа');
    component.submit();

    expect(submitted).toEqual([]);
  });

  it('resets the progress when the question set changes', () => {
    component.setAnswer('Для руководителей');
    component.next();
    fixture.detectChanges();

    fixture.componentRef.setInput('questions', [{ id: 'q-3', text: 'Новый вопрос' }]);
    fixture.detectChanges();

    expect(component.currentIndex()).toBe(0);
    expect(component.answers()).toEqual({});
    expect(fixture.nativeElement.textContent).toContain('Новый вопрос');
  });

  it('advances and submits on Enter', () => {
    const submitted: unknown[] = [];
    component.answersSubmitted.subscribe((answers) => submitted.push(answers));

    component.setAnswer('Для руководителей');
    component.onKeydown(new KeyboardEvent('keydown', { key: 'Enter' }));
    fixture.detectChanges();
    expect(component.currentIndex()).toBe(1);

    component.setAnswer('72 часа');
    component.onKeydown(new KeyboardEvent('keydown', { key: 'Enter' }));

    expect(submitted).toHaveLength(1);
  });

  it('keeps Shift+Enter for a line break', () => {
    component.setAnswer('Для руководителей');
    const event = new KeyboardEvent('keydown', {
      key: 'Enter',
      shiftKey: true,
      cancelable: true
    });

    component.onKeydown(event);

    expect(event.defaultPrevented).toBe(false);
    expect(component.currentIndex()).toBe(0);
  });

  it('disables the controls while the answers are being sent', () => {
    fixture.componentRef.setInput('submitting', true);
    fixture.detectChanges();

    const back = fixture.nativeElement.querySelector('.question-form__back') as HTMLButtonElement;
    const next = fixture.nativeElement.querySelector('.question-form__next') as HTMLButtonElement;
    expect(back.disabled).toBe(true);
    expect(next.disabled).toBe(true);
  });
});
