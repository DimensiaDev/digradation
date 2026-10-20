import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';

import { FileTileComponent } from './file-tile.component';

describe('FileTileComponent', () => {
  let fixture: ComponentFixture<FileTileComponent>;
  let createObjectUrl: ReturnType<typeof vi.fn>;
  let revokeObjectUrl: ReturnType<typeof vi.fn>;

  const makeFile = (name: string): File =>
    new File(['x'], name, { type: 'application/octet-stream' });

  const create = (name: string): ComponentFixture<FileTileComponent> => {
    const created = TestBed.createComponent(FileTileComponent);
    created.componentRef.setInput('file', makeFile(name));
    created.detectChanges();
    created.detectChanges();
    return created;
  };

  beforeEach(async () => {
    createObjectUrl = vi.fn(() => 'blob:thumbnail');
    revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: createObjectUrl
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: revokeObjectUrl
    });

    await TestBed.configureTestingModule({ imports: [FileTileComponent] }).compileComponents();
    fixture = create('план.pdf');
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('shows the name and a file icon for a document', () => {
    expect(fixture.nativeElement.textContent).toContain('план.pdf');
    expect(fixture.debugElement.query(By.css('.file-tile__icon'))).not.toBeNull();
    expect(fixture.debugElement.query(By.css('.file-tile__image'))).toBeNull();
  });

  it('shows a thumbnail for an image and revokes it on destroy', () => {
    const imageFixture = create('схема.png');
    const image = imageFixture.debugElement.query(By.css('.file-tile__image'))
      .nativeElement as HTMLImageElement;

    expect(image.src).toContain('blob:thumbnail');
    expect(createObjectUrl).toHaveBeenCalledOnce();

    imageFixture.destroy();
    expect(revokeObjectUrl).toHaveBeenCalledWith('blob:thumbnail');
  });

  it('offers a preview only for previewable files', () => {
    expect(fixture.debugElement.query(By.css('.file-tile__preview'))).not.toBeNull();

    const textFixture = create('заметки.txt');
    expect(textFixture.debugElement.query(By.css('.file-tile__preview'))).toBeNull();
    textFixture.destroy();
  });

  it('emits preview and remove from its buttons', () => {
    const preview = vi.fn();
    const remove = vi.fn();
    fixture.componentInstance.preview.subscribe(preview);
    fixture.componentInstance.remove.subscribe(remove);

    (
      fixture.debugElement.query(By.css('.file-tile__preview')).nativeElement as HTMLButtonElement
    ).click();
    (
      fixture.debugElement.query(By.css('.file-tile__remove')).nativeElement as HTMLButtonElement
    ).click();

    expect(preview).toHaveBeenCalledOnce();
    expect(remove).toHaveBeenCalledOnce();
  });
});
