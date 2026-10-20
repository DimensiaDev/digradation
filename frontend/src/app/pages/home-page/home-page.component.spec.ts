import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { signal } from '@angular/core';
import { Router, provideRouter } from '@angular/router';
import { Subject, TimeoutError, of, throwError } from 'rxjs';

import { HomePage } from './home-page.component';
import { BrandLogoAnimatedComponent } from '../../shared/components/brand-logo-animated/brand-logo-animated.component';
import { AuthModalService } from '../../core/services/auth-modal.service';
import { ChatStarterService } from '../../core/services/chat-starter.service';
import { InitialChatStateService } from '../../core/services/initial-chat-state.service';
import { NotificationService } from '../../core/services/notification.service';
import { PendingChatDraftService } from '../../core/services/pending-chat-draft.service';
import { ProfileService } from '../../core/services/profile.service';
import { SpeechRecognitionService } from '../../core/services/speech-recognition.service';
import { User } from '../../core/models/models';

interface FakeSpeechOptions {
  readonly lang?: string;
  readonly onError?: (code: string) => void;
}

class FakeSpeechRecognitionService {
  isSupported = true;

  private readonly listening = signal(false);
  private readonly transcriptState = signal({ final: '', interim: '' });

  readonly isListening = this.listening.asReadonly();
  readonly transcript = this.transcriptState.asReadonly();

  lastOptions: FakeSpeechOptions | null = null;

  readonly start = vi.fn((options: FakeSpeechOptions = {}) => {
    this.lastOptions = options;
    this.listening.set(true);
  });

  readonly stop = vi.fn(() => {
    this.listening.set(false);
  });

  emit(final: string, interim: string): void {
    this.transcriptState.set({ final, interim });
  }
}

function makeFile(name: string, size: number): File {
  return new File(['x'.repeat(size)], name, { type: 'application/octet-stream' });
}

function dragEvent(type: string, files: File[] = []): DragEvent {
  const event = new Event(type, { bubbles: true, cancelable: true }) as unknown as DragEvent;
  Object.defineProperty(event, 'dataTransfer', {
    value: { types: files.length > 0 ? ['Files'] : [], files, dropEffect: '' },
    configurable: true
  });
  return event;
}

describe('HomePage', () => {
  let fixture: ComponentFixture<HomePage>;
  let userSignal: ReturnType<typeof signal<User | null>>;
  let authModal: { open: ReturnType<typeof vi.fn> };
  let chatStarter: { start: ReturnType<typeof vi.fn> };
  let speech: FakeSpeechRecognitionService;
  let pendingDraft: PendingChatDraftService;
  let initialChatState: InitialChatStateService;
  let router: Router;

  const page = () => fixture.componentInstance;
  const textarea = () =>
    fixture.debugElement.query(By.css('.composer__textarea')).nativeElement as HTMLTextAreaElement;
  const sendButton = () =>
    fixture.debugElement.query(By.css('.composer__send')).nativeElement as HTMLButtonElement;
  const micButton = () =>
    fixture.debugElement.query(By.css('.composer__mic')).nativeElement as HTMLButtonElement;
  const fileInput = () =>
    fixture.debugElement.query(By.css('input[type="file"]')).nativeElement as HTMLInputElement;
  const tiles = () => fixture.debugElement.queryAll(By.css('.file-tile'));
  const examples = () => fixture.debugElement.queryAll(By.css('.example-card'));

  const refresh = (): void => fixture.detectChanges();

  const typeMessage = (value: string): void => {
    const field = textarea();
    field.value = value;
    field.dispatchEvent(new Event('input'));
    refresh();
  };

  const submit = (): void => {
    fixture.debugElement
      .query(By.css('form'))
      .nativeElement.dispatchEvent(new Event('submit', { cancelable: true }));
    refresh();
  };

  const signIn = (): void => {
    userSignal.set({} as User);
    TestBed.tick();
    refresh();
  };

  const selectFiles = (files: File[]): void => {
    const input = fileInput();
    Object.defineProperty(input, 'files', { value: files, configurable: true });
    input.dispatchEvent(new Event('change'));
    refresh();
  };

  beforeEach(async () => {
    userSignal = signal<User | null>(null);
    authModal = { open: vi.fn() };
    chatStarter = { start: vi.fn(() => of('sess-1')) };
    speech = new FakeSpeechRecognitionService();

    await TestBed.configureTestingModule({
      imports: [HomePage],
      providers: [
        provideRouter([]),
        { provide: ProfileService, useValue: { user: userSignal.asReadonly() } },
        { provide: AuthModalService, useValue: authModal },
        { provide: ChatStarterService, useValue: chatStarter },
        { provide: SpeechRecognitionService, useValue: speech }
      ]
    }).compileComponents();

    pendingDraft = TestBed.inject(PendingChatDraftService);
    initialChatState = TestBed.inject(InitialChatStateService);
    router = TestBed.inject(Router);

    fixture = TestBed.createComponent(HomePage);
    fixture.detectChanges();
  });

  it('greets the user and shows the animated brand mark', () => {
    expect(fixture.debugElement.query(By.css('h1')).nativeElement.textContent).toContain(
      'Готовы к созданию новой программы?'
    );
    expect(fixture.debugElement.query(By.directive(BrandLogoAnimatedComponent))).not.toBeNull();
  });

  it('renders the prompt examples', () => {
    expect(examples()).toHaveLength(4);
  });

  it('fills the field when a prompt example is clicked', () => {
    (examples()[0].nativeElement as HTMLButtonElement).click();
    refresh();

    expect(textarea().value).toBe(page().examples[0]);
  });

  it('keeps the send button disabled until there is text', () => {
    expect(sendButton().disabled).toBe(true);

    typeMessage('   ');
    expect(sendButton().disabled).toBe(true);

    typeMessage('Нужна программа по анализу данных');
    expect(sendButton().disabled).toBe(false);
  });

  it('keeps the composer collapsed until a character is typed', () => {
    const field = () =>
      fixture.debugElement.query(By.css('.composer__field')).nativeElement as HTMLElement;

    expect(page().isExpanded()).toBe(false);
    expect(field().classList).not.toContain('composer__field--expanded');

    typeMessage('П');

    expect(page().isExpanded()).toBe(true);
    expect(field().classList).toContain('composer__field--expanded');

    typeMessage('');

    expect(page().isExpanded()).toBe(false);
  });

  it('marks the composer active while the field is focused', () => {
    expect(page().isComposerActive()).toBe(false);

    textarea().dispatchEvent(new Event('focus'));
    refresh();
    expect(page().isComposerActive()).toBe(true);

    textarea().dispatchEvent(new Event('blur'));
    refresh();
    expect(page().isComposerActive()).toBe(false);
  });

  it('hides the examples once the user starts composing', () => {
    const examples = () =>
      fixture.debugElement.query(By.css('.home__examples')).nativeElement as HTMLElement;

    expect(examples().classList).not.toContain('home__examples--hidden');
    expect(examples().hasAttribute('inert')).toBe(false);

    typeMessage('П');

    expect(examples().classList).toContain('home__examples--hidden');
    expect(examples().hasAttribute('inert')).toBe(true);

    typeMessage('');

    expect(examples().classList).not.toContain('home__examples--hidden');
    expect(examples().hasAttribute('inert')).toBe(false);
  });

  it('focuses the textarea when the composer wrapper is clicked', () => {
    const focusSpy = vi.spyOn(textarea(), 'focus');

    fixture.debugElement
      .query(By.css('.composer__field'))
      .nativeElement.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(focusSpy).toHaveBeenCalled();
  });

  it('opens the file picker from the attach button', () => {
    const clickSpy = vi.spyOn(fileInput(), 'click');

    fixture.debugElement
      .query(By.css('.composer__attach'))
      .nativeElement.dispatchEvent(new Event('click'));

    expect(clickSpy).toHaveBeenCalled();
  });

  it('highlights the field while a file is dragged over it', () => {
    const field = fixture.debugElement.query(By.css('.composer__field'));

    field.nativeElement.dispatchEvent(dragEvent('dragenter', [makeFile('план.pdf', 1024)]));
    refresh();

    expect(page().isDragging()).toBe(true);
    const dropzone = fixture.debugElement.query(By.css('.composer__dropzone'));
    expect(dropzone.nativeElement.textContent).toContain('Перетащите файлы сюда');
    expect(dropzone.query(By.css('svg'))).not.toBeNull();

    field.nativeElement.dispatchEvent(dragEvent('dragleave', [makeFile('план.pdf', 1024)]));
    refresh();

    expect(page().isDragging()).toBe(false);
    expect(fixture.debugElement.query(By.css('.composer__dropzone'))).toBeNull();
  });

  it('ignores a drag that carries no files', () => {
    fixture.debugElement
      .query(By.css('.composer__field'))
      .nativeElement.dispatchEvent(dragEvent('dragenter'));

    refresh();

    expect(page().isDragging()).toBe(false);
  });

  it('accepts the file drag over the field', () => {
    const event = dragEvent('dragover', [makeFile('план.pdf', 1024)]);

    fixture.debugElement.query(By.css('.composer__field')).nativeElement.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
  });

  it('adds the dropped files', () => {
    const field = fixture.debugElement.query(By.css('.composer__field'));

    field.nativeElement.dispatchEvent(dragEvent('dragenter', [makeFile('план.pdf', 2048)]));
    field.nativeElement.dispatchEvent(dragEvent('drop', [makeFile('план.pdf', 2048)]));
    refresh();

    expect(page().isDragging()).toBe(false);
    expect(tiles()).toHaveLength(1);
    expect(fixture.nativeElement.textContent).toContain('план.pdf');
  });

  it('lists the selected files and lets the user remove one', () => {
    selectFiles([makeFile('план.pdf', 2048), makeFile('схема.png', 512)]);

    expect(tiles()).toHaveLength(2);
    expect(fixture.nativeElement.textContent).toContain('план.pdf');

    (tiles()[0].query(By.css('.file-tile__remove')).nativeElement as HTMLButtonElement).click();
    refresh();

    expect(tiles()).toHaveLength(1);
    expect(fixture.nativeElement.textContent).not.toContain('план.pdf');
  });

  it('shows the attached files inside the composer, above the textarea', () => {
    selectFiles([makeFile('план.pdf', 2048)]);

    const field = fixture.debugElement.query(By.css('.composer__field'))
      .nativeElement as HTMLElement;
    const list = field.querySelector('.composer__files');
    const children = Array.from(field.children);

    expect(list).not.toBeNull();
    expect(children.indexOf(list as Element)).toBeLessThan(children.indexOf(textarea()));
  });

  it('opens a preview from a previewable tile', () => {
    selectFiles([makeFile('план.pdf', 2048)]);

    const preview = fixture.debugElement.query(By.css('.file-tile__media'))
      .nativeElement as HTMLButtonElement;
    preview.click();

    expect(page().previewedFile()?.name).toBe('план.pdf');
  });

  it('skips the same file selected twice', () => {
    const file = makeFile('план.pdf', 2048);
    selectFiles([file]);
    selectFiles([file]);

    expect(tiles()).toHaveLength(1);
  });

  it('attaches different files that share a name and a size', () => {
    selectFiles([makeFile('план.pdf', 2048), makeFile('план.pdf', 2048)]);

    expect(tiles()).toHaveLength(2);
  });

  it('rejects an unsupported extension instead of attaching it', () => {
    selectFiles([makeFile('заметки.txt', 2048)]);

    expect(tiles()).toHaveLength(0);
    const notice = TestBed.inject(NotificationService).notifications().at(-1);
    expect(notice?.type).toBe('error');
    expect(notice?.message).toContain('заметки.txt');
  });

  it('rejects a file above the size limit instead of attaching it', () => {
    selectFiles([makeFile('план.pdf', 30 * 1024 * 1024 + 1)]);

    expect(tiles()).toHaveLength(0);
    const notice = TestBed.inject(NotificationService).notifications().at(-1);
    expect(notice?.type).toBe('error');
    expect(notice?.message).toContain('план.pdf');
  });

  it('keeps the valid files from a mixed selection', () => {
    selectFiles([makeFile('план.pdf', 2048), makeFile('заметки.txt', 2048)]);

    expect(tiles()).toHaveLength(1);
    expect(fixture.nativeElement.textContent).toContain('план.pdf');
  });

  it('does not submit the form', () => {
    const form = fixture.debugElement.query(By.css('form'));
    const event = new Event('submit', { cancelable: true });

    form.nativeElement.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
  });

  it('places the microphone to the left of the send button', () => {
    expect(micButton().nextElementSibling).toBe(sendButton());
    expect(micButton().disabled).toBe(false);
    expect(micButton().getAttribute('aria-label')).toBe('Начать голосовой ввод');
    expect(micButton().getAttribute('aria-pressed')).toBe('false');
  });

  it('dictates into the field and stops on a second click', () => {
    micButton().click();
    refresh();

    expect(speech.start).toHaveBeenCalledWith(expect.objectContaining({ lang: 'ru-RU' }));
    expect(micButton().classList).toContain('composer__mic--recording');
    expect(micButton().getAttribute('aria-pressed')).toBe('true');
    expect(micButton().getAttribute('aria-label')).toBe('Остановить голосовой ввод');

    speech.emit('Нужна', 'программа');
    refresh();

    expect(textarea().value).toBe('Нужна программа');
    expect(sendButton().disabled).toBe(false);

    micButton().click();
    refresh();

    expect(speech.stop).toHaveBeenCalled();
    expect(micButton().classList).not.toContain('composer__mic--recording');
    expect(micButton().getAttribute('aria-pressed')).toBe('false');
    expect(textarea().value).toBe('Нужна программа');
  });

  it('appends dictation to the text already in the field', () => {
    typeMessage('Сделай программу');
    micButton().click();
    speech.emit('на Python', '');
    refresh();

    expect(textarea().value).toBe('Сделай программу на Python');
  });

  it('keeps edits made while dictation is still running', () => {
    typeMessage('Сделай программу');
    micButton().click();
    speech.emit('на Python', '');
    refresh();

    typeMessage('Сделай программу на Python и добавь примеры');
    speech.emit('на Python', 'срочно');
    refresh();

    expect(textarea().value).toBe('Сделай программу на Python срочно и добавь примеры');
  });

  it('reports a denied microphone', () => {
    micButton().click();
    speech.lastOptions?.onError?.('not-allowed');
    refresh();

    const notices = TestBed.inject(NotificationService).notifications();
    expect(notices.at(-1)?.message).toBe('Разрешите доступ к микрофону');
  });

  it('explains an unreachable browser recognition service', () => {
    micButton().click();
    speech.lastOptions?.onError?.('network');
    refresh();

    const notice = TestBed.inject(NotificationService).notifications().at(-1);
    expect(notice?.message).toBe('Сервис распознавания речи недоступен');
    expect(notice?.description).toContain('Safari');
  });

  it('disables the microphone when speech recognition is unavailable', () => {
    speech.isSupported = false;
    fixture = TestBed.createComponent(HomePage);
    fixture.detectChanges();

    expect(micButton().disabled).toBe(true);
    expect(micButton().getAttribute('title')).toContain('не поддерживается');
  });

  it('opens the auth popup and keeps the message and files for a guest', () => {
    const file = makeFile('план.pdf', 1024);
    selectFiles([file]);
    typeMessage('  Составь программу  ');

    submit();

    expect(authModal.open).toHaveBeenCalledWith('/home');
    expect(pendingDraft.peek()).toEqual({ text: 'Составь программу', files: [file] });
    expect(chatStarter.start).not.toHaveBeenCalled();
  });

  it('starts a chat right away for an authenticated user', () => {
    signIn();
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);
    const file = makeFile('план.pdf', 1024);
    selectFiles([file]);
    typeMessage('Нужна программа');

    submit();

    expect(authModal.open).not.toHaveBeenCalled();
    expect(chatStarter.start).toHaveBeenCalledWith('Нужна программа', [file]);
    expect(initialChatState.consume('sess-1')).toEqual({
      text: 'Нужна программа',
      files: [file]
    });
    expect(navigate).toHaveBeenCalledWith(['/chats', 'sess-1']);
  });

  it('locks the composer while the chat is starting so submitted text cannot change', () => {
    signIn();
    const start$ = new Subject<string>();
    chatStarter.start.mockReturnValue(start$.asObservable());
    vi.spyOn(router, 'navigate').mockResolvedValue(true);
    const attach = () =>
      fixture.debugElement.query(By.css('.composer__attach')).nativeElement as HTMLButtonElement;
    typeMessage('Нужна программа');

    submit();

    expect(textarea().readOnly).toBe(true);
    expect(attach().disabled).toBe(true);

    typeMessage('Изменённый текст');
    expect(page().message()).toBe('Нужна программа');

    start$.next('sess-1');
    start$.complete();
    refresh();

    expect(textarea().readOnly).toBe(false);
    expect(attach().disabled).toBe(false);
  });

  it('does not navigate when the page is destroyed while the chat is starting', () => {
    signIn();
    const start$ = new Subject<string>();
    chatStarter.start.mockReturnValue(start$.asObservable());
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);
    typeMessage('Нужна программа');

    submit();
    fixture.destroy();
    start$.next('sess-1');
    start$.complete();

    expect(navigate).not.toHaveBeenCalled();
    expect(initialChatState.consume('sess-1')).toBeNull();
  });

  it('reports an upload timeout and lets the user retry', () => {
    signIn();
    chatStarter.start.mockReturnValue(throwError(() => new TimeoutError()));
    typeMessage('Нужна программа');

    submit();

    const notice = TestBed.inject(NotificationService).notifications().at(-1);
    expect(notice?.type).toBe('error');
    expect(notice?.message).toContain('время ожидания');
    expect(notice?.description).toBeTruthy();
    expect(sendButton().disabled).toBe(false);
  });

  it('starts the saved chat after a guest signs in', () => {
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);
    const file = makeFile('план.pdf', 1024);
    selectFiles([file]);
    typeMessage('Нужна программа');
    submit();

    expect(chatStarter.start).not.toHaveBeenCalled();

    signIn();

    expect(chatStarter.start).toHaveBeenCalledWith('Нужна программа', [file]);
    expect(pendingDraft.peek()).toBeNull();
    expect(initialChatState.consume('sess-1')).toEqual({
      text: 'Нужна программа',
      files: [file]
    });
    expect(navigate).toHaveBeenCalledWith(['/chats', 'sess-1']);
  });

  it('restores the guest draft when resuming it after login fails', () => {
    chatStarter.start.mockReturnValue(throwError(() => new Error('Ошибка сервера')));
    const file = makeFile('план.pdf', 1024);
    selectFiles([file]);
    typeMessage('Нужна программа');
    submit();

    expect(pendingDraft.peek()).toEqual({ text: 'Нужна программа', files: [file] });

    signIn();

    expect(pendingDraft.peek()).toEqual({ text: 'Нужна программа', files: [file] });
    expect(sendButton().disabled).toBe(false);
  });
});
