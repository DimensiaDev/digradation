import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';

import { HomePage } from './home-page.component';
import { BrandLogoAnimatedComponent } from '../../shared/components/brand-logo-animated/brand-logo-animated.component';

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

  const page = () => fixture.componentInstance;
  const textarea = () =>
    fixture.debugElement.query(By.css('.composer__textarea')).nativeElement as HTMLTextAreaElement;
  const sendButton = () =>
    fixture.debugElement.query(By.css('.composer__send')).nativeElement as HTMLButtonElement;
  const fileInput = () =>
    fixture.debugElement.query(By.css('input[type="file"]')).nativeElement as HTMLInputElement;
  const chips = () => fixture.debugElement.queryAll(By.css('.file-chip'));
  const examples = () => fixture.debugElement.queryAll(By.css('.example-card'));

  const refresh = (): void => fixture.detectChanges();

  const typeMessage = (value: string): void => {
    const field = textarea();
    field.value = value;
    field.dispatchEvent(new Event('input'));
    refresh();
  };

  const selectFiles = (files: File[]): void => {
    const input = fileInput();
    Object.defineProperty(input, 'files', { value: files, configurable: true });
    input.dispatchEvent(new Event('change'));
    refresh();
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [HomePage] }).compileComponents();

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
    expect(chips()).toHaveLength(1);
    expect(fixture.nativeElement.textContent).toContain('план.pdf');
  });

  it('lists the selected files and lets the user remove one', () => {
    selectFiles([makeFile('план.pdf', 2048), makeFile('схема.png', 512)]);

    expect(chips()).toHaveLength(2);
    expect(fixture.nativeElement.textContent).toContain('план.pdf');
    expect(fixture.nativeElement.textContent).toContain('2 КБ');

    (chips()[0].query(By.css('.file-chip__remove')).nativeElement as HTMLButtonElement).click();
    refresh();

    expect(chips()).toHaveLength(1);
    expect(fixture.nativeElement.textContent).not.toContain('план.pdf');
  });

  it('skips duplicate files', () => {
    selectFiles([makeFile('план.pdf', 2048)]);
    selectFiles([makeFile('план.pdf', 2048)]);

    expect(chips()).toHaveLength(1);
  });

  it('does not submit the form', () => {
    const form = fixture.debugElement.query(By.css('form'));
    const event = new Event('submit', { cancelable: true });

    form.nativeElement.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
  });
});
