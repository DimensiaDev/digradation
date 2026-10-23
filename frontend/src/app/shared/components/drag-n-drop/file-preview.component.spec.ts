import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Component } from '@angular/core';
import { By } from '@angular/platform-browser';

import { ACCEPTED_EXTENSIONS, previewKindFor, mimeTypeFor } from '../../utils/file-types';
import { FilePreviewComponent } from './file-preview.component';
import { DragNDropComponent } from './drag-n-drop.component';
import { AttachmentUploadService } from '../../../core/services/attachment-upload.service';
import { Observable } from 'rxjs';

function makeFile(name: string, contents: BlobPart = 'x', type = 'application/octet-stream'): File {
  return new File([contents], name, { type });
}

async function waitForPhase(
  fixture: ComponentFixture<FilePreviewComponent>,
  phase: 'ready' | 'error'
): Promise<void> {
  await vi.waitFor(
    () => {
      fixture.detectChanges();
      expect(fixture.componentInstance.phase()).toBe(phase);
    },
    { timeout: 10000, interval: 5 }
  );
}

function blobUrlStub(): { urls: string[]; restore: () => void } {
  const urls: string[] = [];
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = vi.fn(() => {
    urls.push('blob:mock-' + urls.length);
    return urls[urls.length - 1];
  }) as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
  return {
    urls,
    restore: () => {
      URL.createObjectURL = originalCreate;
      URL.revokeObjectURL = originalRevoke;
    }
  };
}

const CRC32_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    table[index] = value >>> 0;
  }
  return table;
})();

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc = CRC32_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function concatBytes(parts: readonly Uint8Array[]): Uint8Array<ArrayBuffer> {
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const result = new Uint8Array(total);
  let offset = 0;
  for (const part of parts) {
    result.set(part, offset);
    offset += part.length;
  }
  return result;
}

function uint16(value: number): Uint8Array {
  return new Uint8Array([value & 0xff, (value >>> 8) & 0xff]);
}

function uint32(value: number): Uint8Array {
  return new Uint8Array([
    value & 0xff,
    (value >>> 8) & 0xff,
    (value >>> 16) & 0xff,
    (value >>> 24) & 0xff
  ]);
}

/** A ZIP with stored (uncompressed) entries — an archive shape every reader accepts. */
function storedZip(entries: readonly { name: string; text: string }[]): Uint8Array<ArrayBuffer> {
  const encoder = new TextEncoder();
  const local: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;

  for (const entry of entries) {
    const name = encoder.encode(entry.name);
    const data = encoder.encode(entry.text);
    const crc = crc32(data);
    const header = concatBytes([
      uint32(0x04034b50),
      uint16(20),
      uint16(0),
      uint16(0),
      uint16(0),
      uint16(0),
      uint32(crc),
      uint32(data.length),
      uint32(data.length),
      uint16(name.length),
      uint16(0),
      name,
      data
    ]);
    local.push(header);
    central.push(
      concatBytes([
        uint32(0x02014b50),
        uint16(20),
        uint16(20),
        uint16(0),
        uint16(0),
        uint16(0),
        uint16(0),
        uint32(crc),
        uint32(data.length),
        uint32(data.length),
        uint16(name.length),
        uint16(0),
        uint16(0),
        uint16(0),
        uint16(0),
        uint32(0),
        uint32(offset),
        name
      ])
    );
    offset += header.length;
  }

  const directory = concatBytes(central);
  return concatBytes([
    ...local,
    directory,
    concatBytes([
      uint32(0x06054b50),
      uint16(0),
      uint16(0),
      uint16(entries.length),
      uint16(entries.length),
      uint32(directory.length),
      uint32(offset),
      uint16(0)
    ])
  ]);
}

const W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';
const R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
const REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships';

function makeDocxFile(name: string, files: { name: string; text: string }[]): File {
  return new File([storedZip(files)], name, {
    type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
  });
}

/**
 * A real `.docx` carrying two links whose targets the *document* picks, plus one named style.
 *
 * The hostile target is the shape the security review demonstrated: the renderer copies a
 * relationship's `Target` straight into `href`, so the file — which arrived from outside the
 * organisation — decides what a click in the preview does.
 */
function docxWithLinks(hostileTarget: string): File {
  const xml = (body: string): string =>
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n${body}`;

  return makeDocxFile('бриф.docx', [
    {
      name: '[Content_Types].xml',
      text: xml(
        `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">` +
          `<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>` +
          `<Default Extension="xml" ContentType="application/xml"/>` +
          `<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>` +
          `<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>` +
          `</Types>`
      )
    },
    {
      name: '_rels/.rels',
      text: xml(
        `<Relationships xmlns="${REL_NS}">` +
          `<Relationship Id="rId1" Type="${R_NS}/officeDocument" Target="word/document.xml"/>` +
          `</Relationships>`
      )
    },
    {
      name: 'word/document.xml',
      text: xml(
        `<w:document xmlns:w="${W_NS}" xmlns:r="${R_NS}"><w:body>` +
          `<w:p><w:r><w:t>Пояснительная записка</w:t></w:r></w:p>` +
          `<w:p><w:hyperlink r:id="rIdHostile"><w:r><w:t>злая ссылка</w:t></w:r></w:hyperlink></w:p>` +
          `<w:p><w:hyperlink r:id="rIdSafe"><w:r><w:t>добрая ссылка</w:t></w:r></w:hyperlink></w:p>` +
          `</w:body></w:document>`
      )
    },
    {
      name: 'word/_rels/document.xml.rels',
      text: xml(
        `<Relationships xmlns="${REL_NS}">` +
          `<Relationship Id="rIdHostile" Type="${R_NS}/hyperlink" Target="${hostileTarget}" TargetMode="External"/>` +
          `<Relationship Id="rIdSafe" Type="${R_NS}/hyperlink" Target="https://minobrnauki.gov.ru/" TargetMode="External"/>` +
          `<Relationship Id="rIdStyles" Type="${R_NS}/styles" Target="styles.xml"/>` +
          `</Relationships>`
      )
    },
    {
      name: 'word/styles.xml',
      text: xml(
        `<w:styles xmlns:w="${W_NS}"><w:docDefaults><w:rPrDefault><w:rPr>` +
          `<w:rFonts w:ascii="Arial"/></w:rPr></w:rPrDefault></w:docDefaults>` +
          `<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>` +
          `</w:styles>`
      )
    }
  ]);
}

/** The container the preview rendered into, and the shadow tree it confined the render to. */
function docxRender(fixture: ComponentFixture<FilePreviewComponent>): {
  container: HTMLElement;
  root: ShadowRoot;
} {
  const container = fixture.nativeElement.querySelector(
    '.file-preview__docx'
  ) as HTMLElement | null;
  const root = container?.shadowRoot ?? null;
  if (!container || !root) {
    throw new Error('the docx preview did not render into a shadow root');
  }
  return { container, root };
}

function docxLinks(fixture: ComponentFixture<FilePreviewComponent>): HTMLAnchorElement[] {
  return Array.from(docxRender(fixture).root.querySelectorAll('a')) as HTMLAnchorElement[];
}

describe('previewKindFor', () => {
  it('maps browser-viewable formats to their renderers', () => {
    expect(previewKindFor('.pdf')).toBe('pdf');
    expect(previewKindFor('.docx')).toBe('docx');
    expect(previewKindFor('.xls')).toBe('spreadsheet');
    expect(previewKindFor('.xlsx')).toBe('spreadsheet');
    expect(previewKindFor('.png')).toBe('image');
    expect(previewKindFor('.jpg')).toBe('image');
    expect(previewKindFor('.jpeg')).toBe('image');
  });

  it('returns null for formats without browser rendering', () => {
    expect(previewKindFor('.dwg')).toBeNull();
    expect(previewKindFor('.doc')).toBeNull();
    expect(previewKindFor('.pptx')).toBeNull();
    expect(previewKindFor('.exe')).toBeNull();
    expect(previewKindFor('')).toBeNull();
  });

  it('maps extensions to correct MIME types for blob URLs', () => {
    expect(mimeTypeFor('.pdf')).toBe('application/pdf');
    expect(mimeTypeFor('.png')).toBe('image/png');
    expect(mimeTypeFor('.docx')).toContain('wordprocessingml');
    expect(mimeTypeFor('.pptx')).toContain('presentationml');
    expect(mimeTypeFor('.xyz')).toBe('application/octet-stream');
  });
});

describe('FilePreviewComponent', () => {
  const compile = (file: File) => {
    const fixture = TestBed.createComponent(FilePreviewComponent);
    fixture.componentRef.setInput('file', file);
    return fixture;
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [FilePreviewComponent] }).compileComponents();
  });

  it('shows the error placeholder for an unparseable docx', async () => {
    const fixture = compile(makeFile('битый.docx', 'not a zip'));
    await waitForPhase(fixture, 'error');

    expect(fixture.nativeElement.querySelector('.file-preview')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('.file-preview__warning')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('[role="alert"]')).not.toBeNull();
    expect(fixture.nativeElement.textContent).toContain('Не удалось открыть предпросмотр');
  });

  it.each([
    ['javascript:', 'javascript:window.__docxPwned=true'],
    ['mixed-case javascript:', 'JaVaScRiPt:window.__docxPwned=true'],
    // A character reference, because a *literal* newline in an attribute is normalised to a
    // space by the XML parser and stops being a scheme at all. This one survives to the DOM,
    // where the URL parser strips it again and the scheme is `javascript:` after all.
    ['javascript: split by a newline', 'java&#10;script:window.__docxPwned=true'],
    ['vbscript:', 'vbscript:msgbox(1)'],
    ['data:', 'data:text/plain,__docxPwned']
  ])('drops the href of a link a document points at %s', async (_label, target) => {
    const fixture = compile(docxWithLinks(target));
    await waitForPhase(fixture, 'ready');

    const links = docxLinks(fixture);
    // Non-vacuous: the document really did render both of its links.
    expect(links.map((link) => link.textContent)).toEqual(['злая ссылка', 'добрая ссылка']);

    const hostile = links[0];
    expect(hostile.hasAttribute('href')).toBe(false);
    expect(hostile.outerHTML).not.toContain('__docxPwned');
  });

  it('keeps a real link, hardened, and confines the document stylesheet to its own tree', async () => {
    const fixture = compile(docxWithLinks('javascript:window.__docxPwned=true'));
    await waitForPhase(fixture, 'ready');

    const { container, root } = docxRender(fixture);
    const safe = docxLinks(fixture)[1];
    expect(safe.getAttribute('href')).toBe('https://minobrnauki.gov.ru/');
    expect(safe.getAttribute('rel')).toBe('noopener noreferrer');
    expect(safe.getAttribute('target')).toBe('_blank');

    // The renderer assembles a stylesheet from the document's own style and font names,
    // unescaped. It belongs in the shadow tree, where no rule can reach the app around it.
    expect(root.querySelector('style')).not.toBeNull();
    expect(container.querySelector('style')).toBeNull();

    // A relative target resolves against the app's own origin and is allowed through.
    const relative = docxWithLinks('#anchor');
    const relativeFixture = compile(relative);
    await waitForPhase(relativeFixture, 'ready');
    expect(docxLinks(relativeFixture)[0].getAttribute('href')).toBe('#anchor');
  });

  it('renders a spreadsheet with sheet tabs and a single table', async () => {
    const fixture = compile(makeFile('sheet.xlsx', 'any bytes'));
    await waitForPhase(fixture, 'ready');

    expect(fixture.componentInstance.phase()).toBe('ready');
    expect(fixture.nativeElement.querySelector('.file-preview__sheet-tabs')).not.toBeNull();
    const activeTab = fixture.nativeElement.querySelector('.file-preview__sheet-tab--active');
    expect(activeTab).not.toBeNull();
    expect(activeTab.getAttribute('aria-selected')).toBe('true');
    expect(fixture.nativeElement.querySelector('.file-preview__sheet-table table')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('.file-preview__warning')).toBeNull();
  }, 15000);

  it('renders a spreadsheet whose cells are formulas', async () => {
    // A plan priced by formulas, and a merged header whose text sits in a cell
    // the merge swallows: both are shapes a real workbook has, and both render
    // blank or as raw formula text without the two normalisers this covers.
    const XLSX = await import('xlsx');
    const input = XLSX.utils.aoa_to_sheet([[2], [3]]);
    const total: import('xlsx').WorkSheet = {
      '!ref': 'A1',
      A1: { t: 'n', v: 0, f: 'СУММ(Ввод!A1:A2)' }
    };
    const merged: import('xlsx').WorkSheet = {
      '!ref': 'A1:B1',
      B1: { t: 's', v: 'итого' }
    };
    merged['!merges'] = [{ s: { r: 0, c: 0 }, e: { r: 0, c: 1 } }];

    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, input, 'Ввод');
    XLSX.utils.book_append_sheet(workbook, total, 'Итог');
    XLSX.utils.book_append_sheet(workbook, merged, 'Свод');
    const bytes = XLSX.write(workbook, { type: 'array', bookType: 'xlsx' });

    const fixture = compile(makeFile('plan.xlsx', bytes));
    await waitForPhase(fixture, 'ready');

    const tabs = () =>
      Array.from(
        fixture.nativeElement.querySelectorAll('.file-preview__sheet-tab')
      ) as HTMLButtonElement[];
    expect(tabs().map((tab) => tab.textContent)).toEqual(['Ввод', 'Итог', 'Свод']);
    expect(tabs()[0].classList).toContain('file-preview__sheet-tab--active');

    const tableText = () =>
      (fixture.nativeElement.querySelector('.file-preview__sheet-table table') as HTMLElement)
        .textContent ?? '';

    tabs()[1].click();
    fixture.detectChanges();

    // The cross-sheet total is computed, and the Russian function name was
    // translated rather than left for the engine to reject.
    expect(tableText()).toContain('5');
    expect(tableText()).not.toContain('СУММ');
    expect(tableText()).not.toContain('SUM(');
    expect(tabs()[1].classList).toContain('file-preview__sheet-tab--active');
    expect(tabs()[0].classList).not.toContain('file-preview__sheet-tab--active');
    expect(tabs()[0].getAttribute('aria-selected')).toBe('false');

    tabs()[2].click();
    fixture.detectChanges();

    expect(tableText()).toContain('итого');
  }, 15000);

  it('warns instead of rendering a format with no renderer', async () => {
    const fixture = compile(makeFile('чертёж.dwg', 'bytes'));
    await waitForPhase(fixture, 'error');

    expect(fixture.nativeElement.querySelector('.file-preview__warning')).not.toBeNull();
  });

  it('creates a blob URL for images and revokes it on destroy', async () => {
    const stub = blobUrlStub();
    try {
      const fixture = compile(makeFile('pic.png'));
      await waitForPhase(fixture, 'ready');

      const image = fixture.nativeElement.querySelector('.file-preview__image');
      expect(image).not.toBeNull();
      expect(image.getAttribute('src')).toBe(stub.urls[0]);
      expect(image.alt).toBe('pic.png');

      fixture.destroy();
      expect(URL.revokeObjectURL).toHaveBeenCalledWith(stub.urls[0]);
    } finally {
      stub.restore();
    }
  });

  it('creates a blob URL for pdf and emits closed on close', async () => {
    const stub = blobUrlStub();
    try {
      const fixture = compile(makeFile('doc.pdf'));
      await waitForPhase(fixture, 'ready');

      const frame = fixture.nativeElement.querySelector('.file-preview__pdf');
      expect(frame).not.toBeNull();
      expect(frame.getAttribute('src')).toBe(stub.urls[0]);
      expect(frame.title).toBe('doc.pdf');
      expect(fixture.nativeElement.querySelector('.file-preview__warning')).toBeNull();

      let closedEmitted = false;
      fixture.componentInstance.closed.subscribe(() => (closedEmitted = true));
      fixture.nativeElement.querySelector('.file-preview__close').click();

      expect(closedEmitted).toBe(true);
    } finally {
      stub.restore();
    }
  });

  it('shows the file name and uppercase extension chip', async () => {
    const fixture = compile(makeFile('отчёт.PDF'));
    await fixture.whenStable();

    expect(fixture.nativeElement.querySelector('.file-preview__title')?.textContent?.trim()).toBe(
      'отчёт.PDF'
    );
    expect(fixture.nativeElement.querySelector('.file-preview__chip')?.textContent?.trim()).toBe(
      'PDF'
    );
  });

  it('labels the dialog by the file title and exposes placeholder roles', async () => {
    const fixture = compile(makeFile('doc.pdf'));
    await waitForPhase(fixture, 'ready');

    const dialog = fixture.nativeElement.querySelector('dialog.file-preview');
    expect(dialog).not.toBeNull();
    expect(dialog.getAttribute('aria-labelledby')).toBe('file-preview-title');
    const titleEl = fixture.nativeElement.querySelector('#file-preview-title');
    expect(titleEl?.textContent?.trim()).toBe('doc.pdf');
  });

  it('closes cleanly without touching focus', async () => {
    const fixture = compile(makeFile('doc.pdf'));
    await waitForPhase(fixture, 'ready');

    try {
      let closedEmitted = false;
      fixture.componentInstance.closed.subscribe(() => (closedEmitted = true));

      fixture.componentInstance.close();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(closedEmitted).toBe(true);
    } finally {
      fixture.destroy();
    }
  });
});

@Component({
  selector: 'app-dnd-preview-test-host',
  imports: [DragNDropComponent],
  template: `<app-drag-n-drop [sessionId]="'test-session'" />`
})
class PreviewTestHost {}

describe('DragNDropComponent preview integration', () => {
  let fixture: ComponentFixture<PreviewTestHost>;
  let component: DragNDropComponent;
  let uploadSpy: ReturnType<typeof vi.spyOn>;
  let completers: Map<string, () => void>;

  const getDropzone = () => fixture.debugElement.query(By.css('.dropzone__area'));

  function dropFiles(files: File[]): void {
    const event = new Event('drop', { bubbles: true }) as unknown as DragEvent;
    Object.defineProperty(event, 'dataTransfer', { value: { files } });
    getDropzone().nativeElement.dispatchEvent(event);
    fixture.detectChanges();
  }

  function finishAllUploads(): void {
    for (const item of component.items()) {
      completers.get(item.id)?.();
    }
    fixture.detectChanges();
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [PreviewTestHost],
      providers: [
        {
          provide: AttachmentUploadService,
          useValue: { upload: () => new Observable<number>(() => () => undefined) }
        }
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(PreviewTestHost);
    component = fixture.debugElement.query(By.directive(DragNDropComponent))
      .componentInstance as DragNDropComponent;

    completers = new Map();
    uploadSpy = vi.spyOn(TestBed.inject(AttachmentUploadService), 'upload').mockImplementation(
      (item) =>
        new Observable<number>((observer) => {
          completers.set(item.id, () => {
            observer.next(0);
            observer.complete();
          });
          return () => completers.delete(item.id);
        })
    );

    fixture.detectChanges();
  });

  afterEach(() => {
    uploadSpy.mockRestore();
  });

  it('opens the preview dialog with the dropped file and closes on output', () => {
    const pdf = makeFile('документ.pdf');
    dropFiles([pdf]);
    finishAllUploads();

    expect(component.state()).toBe('completed');

    fixture.debugElement.query(By.css('button[aria-label="Предпросмотр"]')).nativeElement.click();
    fixture.detectChanges();

    const previewHost = fixture.debugElement.query(By.css('app-file-preview'));
    expect(previewHost).not.toBeNull();
    expect(component.previewItem()?.file).toBe(pdf);
    expect(previewHost.nativeElement.querySelector('.file-preview')).not.toBeNull();

    previewHost.componentInstance.close();
    fixture.detectChanges();

    expect(component.previewItem()).toBeNull();
    expect(fixture.debugElement.query(By.css('app-file-preview'))).toBeNull();
  });

  it('accepts exactly the formats the backend takes, including docx', () => {
    expect(ACCEPTED_EXTENSIONS).toEqual(['.pdf', '.png', '.jpeg', '.jpg', '.docx']);
    expect(ACCEPTED_EXTENSIONS).not.toContain('.pptx');
  });

  it('offers a preview for every accepted upload type', () => {
    // The eye button is rendered conditionally on `previewKindFor`. While every
    // accepted extension has a preview kind that branch is unreachable, so there
    // is nothing integration-level to assert about a hidden button; what matters
    // is that the two lists agree. This fails on the day someone accepts a type
    // they cannot render, which is exactly when the guard starts to matter.
    for (const extension of ACCEPTED_EXTENSIONS) {
      expect(previewKindFor(extension)).not.toBeNull();
    }
  });

  it('still previews a PDF among the accepted types', () => {
    dropFiles([makeFile('doc.pdf')]);
    finishAllUploads();

    const rows = fixture.debugElement.queryAll(By.css('.upload-item'));
    const pdfRow = rows.find((row) => row.nativeElement.textContent.includes('doc.pdf'));

    expect(pdfRow).toBeDefined();
    expect(pdfRow?.query(By.css('button[aria-label="Предпросмотр"]'))).not.toBeNull();
    expect(pdfRow?.query(By.css('button[aria-label="Удалить"]'))).not.toBeNull();
  });

  it('clears the open preview when its item is removed', () => {
    dropFiles([makeFile('doc.pdf')]);
    finishAllUploads();

    fixture.debugElement.query(By.css('button[aria-label="Предпросмотр"]')).nativeElement.click();
    fixture.detectChanges();
    expect(component.previewItem()).not.toBeNull();

    fixture.debugElement.query(By.css('button[aria-label="Удалить"]')).nativeElement.click();
    fixture.detectChanges();

    expect(component.previewItem()).toBeNull();
    expect(component.state()).toBe('idle');
  });
});
