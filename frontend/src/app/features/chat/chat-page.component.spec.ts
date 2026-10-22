import { Component, input, output } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { throwError } from 'rxjs';
import { vi } from 'vitest';

import { environment } from '../../../environments/environment';
import { ProgramQuestionApi, ProgramStateApi } from '../../core/models/models';
import { ChatService } from '../../core/services/chat.service';
import { ChatPage } from './chat-page.component';
import { ChatComponent } from './chat.component';

const SESSION_ID = 'd0a3f1e2-0000-4000-8000-123456789abc';
const SECOND_SESSION_ID = 'b1c2d3e4-0000-4000-8000-000000000001';
const PROGRAM_URL = (id: string) => `${environment.apiUrl}/chats/${id}/program`;

const BLOCKS = {
  kind: null,
  title: null,
  competencies: [],
  curriculum: null,
  sources: [],
  summary: null
};

const state = (overrides: Partial<ProgramStateApi> = {}): ProgramStateApi => ({
  status: 'generating',
  current_stage: 'competences',
  stages: {},
  running: true,
  busy: true,
  error: null,
  updated_at: '2026-10-21T12:00:00Z',
  version: 1,
  questions: [],
  draft: BLOCKS,
  report: null,
  exports: null,
  ...overrides
});

const question = (id: string, text = 'Сколько часов?'): ProgramQuestionApi => ({ id, text });

@Component({
  selector: 'app-chat',
  template: `
    <button class="start-turn" type="button" (click)="turnStarted.emit()">start</button>
    <button class="finish-turn" type="button" (click)="turnFinished.emit()">finish</button>
  `
})
class ChatStub {
  readonly id = input.required<string>();
  readonly questions = input<readonly ProgramQuestionApi[]>([]);
  readonly exportAttachmentIds = input<readonly number[]>([]);
  readonly turnStarted = output<void>();
  readonly turnFinished = output<void>();
  readonly refreshProgram = output<void>();
}

describe('ChatPage', () => {
  let fixture: ComponentFixture<ChatPage>;
  let component: ChatPage;
  let http: HttpTestingController;
  let chat: ChatStub;

  const create = (id = SESSION_ID, program: ProgramStateApi | null = state()): void => {
    fixture = TestBed.createComponent(ChatPage);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('id', id);
    fixture.detectChanges();

    if (program) {
      http.expectOne(PROGRAM_URL(id)).flush(program);
      fixture.detectChanges();
    }

    chat = fixture.debugElement.query(By.directive(ChatStub)).componentInstance as ChatStub;
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [ChatPage],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()]
    });
    TestBed.overrideComponent(ChatPage, {
      remove: { imports: [ChatComponent] },
      add: { imports: [ChatStub] }
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    fixture?.destroy();
    vi.useRealTimers();
    http.match(() => true);
    TestBed.resetTestingModule();
  });

  it('loads the programme, renders the shell and feeds the chat', () => {
    create(
      SESSION_ID,
      state({
        status: 'pending_approval',
        busy: false,
        running: false,
        questions: [question('q1')]
      })
    );

    expect(component.program()).not.toBeNull();
    expect(component.programLoading()).toBe(false);
    expect(fixture.nativeElement.textContent).toContain('Мои программы');
    expect(fixture.nativeElement.textContent).toContain('Создание образовательной программы');
    expect(fixture.nativeElement.textContent).toContain('Проверка');
    expect(chat.questions()).toEqual([question('q1')]);
  });

  it('passes the programme export ids to the chat', () => {
    create(
      SESSION_ID,
      state({
        busy: false,
        running: false,
        exports: { document_attachment_id: 12, slides_attachment_id: 13 }
      })
    );

    expect(chat.exportAttachmentIds()).toEqual([12, 13]);
  });

  it('polls the programme while it is busy and stops once it settles', async () => {
    vi.useFakeTimers();
    create();

    await vi.advanceTimersByTimeAsync(2000);
    http.expectOne(PROGRAM_URL(SESSION_ID)).flush(state({ busy: false, running: false }));
    fixture.detectChanges();

    await vi.advanceTimersByTimeAsync(6000);
    expect(http.match((request) => request.url === PROGRAM_URL(SESSION_ID))).toEqual([]);
  });

  it('stops polling while the methodist has unanswered questions', async () => {
    vi.useFakeTimers();
    create(SESSION_ID, state({ status: 'awaiting_answers', questions: [question('q1')] }));

    await vi.advanceTimersByTimeAsync(6000);
    expect(http.match((request) => request.url === PROGRAM_URL(SESSION_ID))).toEqual([]);
    expect(chat.questions()).toHaveLength(1);
  });

  it('hides questions during a turn and refreshes before showing them again', () => {
    create(SESSION_ID, state({ status: 'awaiting_answers', questions: [question('q1')] }));

    const start = fixture.debugElement.query(By.css('.start-turn')).nativeElement as HTMLElement;
    start.click();
    fixture.detectChanges();

    expect(component.turnInFlight()).toBe(true);
    expect(chat.questions()).toEqual([]);
    http
      .expectOne(PROGRAM_URL(SESSION_ID))
      .flush(state({ status: 'awaiting_answers', questions: [question('q1')] }));
    fixture.detectChanges();
    expect(chat.questions()).toEqual([]);

    const finish = fixture.debugElement.query(By.css('.finish-turn')).nativeElement as HTMLElement;
    finish.click();
    fixture.detectChanges();

    expect(component.turnInFlight()).toBe(false);
    http
      .expectOne(PROGRAM_URL(SESSION_ID))
      .flush(state({ status: 'awaiting_answers', questions: [question('q2', 'Срок?')] }));
    fixture.detectChanges();
    expect(chat.questions()).toEqual([question('q2', 'Срок?')]);
  });

  it('reports a load failure and retries through the preview', () => {
    fixture = TestBed.createComponent(ChatPage);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('id', SESSION_ID);
    fixture.detectChanges();

    http
      .expectOne(PROGRAM_URL(SESSION_ID))
      .flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(component.programLoading()).toBe(false);
    expect(component.programError()).toContain('Не удалось загрузить каркас');

    component.refreshProgram();
    expect(component.programLoading()).toBe(true);
    http.expectOne(PROGRAM_URL(SESSION_ID)).flush(state({ busy: false }));
    fixture.detectChanges();

    expect(component.programError()).toBeNull();
    expect(component.programLoading()).toBe(false);
  });

  it('reports a non-HTTP failure generically', () => {
    const service = TestBed.inject(ChatService);
    vi.spyOn(service, 'program').mockReturnValue(throwError(() => new Error('boom')));

    fixture = TestBed.createComponent(ChatPage);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('id', SESSION_ID);
    fixture.detectChanges();

    expect(component.programError()).toBe('Не удалось загрузить каркас программы');
  });

  it('ignores a response from the session that was left behind', () => {
    fixture = TestBed.createComponent(ChatPage);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('id', SESSION_ID);
    fixture.detectChanges();
    const first = http.expectOne(PROGRAM_URL(SESSION_ID));

    fixture.componentRef.setInput('id', SECOND_SESSION_ID);
    fixture.detectChanges();
    const second = http.expectOne(PROGRAM_URL(SECOND_SESSION_ID));
    second.flush(state({ busy: false, version: 2 }));
    fixture.detectChanges();
    first.flush(state({ busy: false, version: 7 }));
    fixture.detectChanges();

    expect(component.program()?.version).toBe(2);
    expect(component.program()?.busy).toBe(false);
  });

  it('switches between chat and preview on a narrow screen', () => {
    create(SESSION_ID, state({ busy: false }));

    component.showPreview();
    fixture.detectChanges();
    expect(component.mobileView()).toBe('preview');
    expect(
      fixture.nativeElement.querySelector('.chat-page__pane--preview').classList
    ).not.toContain('chat-page__pane--mobile-hidden');

    component.showChat();
    fixture.detectChanges();
    expect(component.mobileView()).toBe('chat');
    expect(fixture.nativeElement.querySelector('.chat-page__pane--chat').classList).not.toContain(
      'chat-page__pane--mobile-hidden'
    );
  });
});
