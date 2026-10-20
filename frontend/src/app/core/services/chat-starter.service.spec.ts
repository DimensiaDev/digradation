import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Observable, of, throwError } from 'rxjs';

import { ChatStarterService } from './chat-starter.service';
import { AttachmentUploadService } from './attachment-upload.service';
import { UploadItem } from '../models/models';
import { environment } from '../../../environments/environment';

const CHATS_URL = `${environment.apiUrl}/chats/`;

class FakeUploader {
  readonly items: UploadItem[] = [];
  readonly sessionIds: string[] = [];
  failure: Error | null = null;

  upload(item: UploadItem, sessionId: string): Observable<number> {
    this.items.push(item);
    this.sessionIds.push(sessionId);
    if (this.failure) {
      return throwError(() => this.failure);
    }
    return of(item.size);
  }
}

function makeFile(name: string, size: number): File {
  return new File(['x'.repeat(size)], name, { type: 'application/octet-stream' });
}

describe('ChatStarterService', () => {
  let service: ChatStarterService;
  let http: HttpTestingController;
  let uploader: FakeUploader;

  beforeEach(() => {
    uploader = new FakeUploader();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: AttachmentUploadService, useValue: uploader }
      ]
    });

    service = TestBed.inject(ChatStarterService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('creates a session, uploads the files, sends the message and emits the session id', () => {
    const sessionIds: string[] = [];

    service
      .start('Нужна программа', [makeFile('план.pdf', 1024), makeFile('схема.png', 512)])
      .subscribe((sessionId) => sessionIds.push(sessionId));

    http.expectOne(CHATS_URL).flush({ session_id: 'sess-1' });

    expect(uploader.sessionIds).toEqual(['sess-1', 'sess-1']);
    expect(uploader.items.map((item) => item.extension)).toEqual(['.pdf', '.png']);

    const send = http.expectOne(`${CHATS_URL}sess-1`);
    expect(send.request.method).toBe('POST');
    expect(send.request.body).toEqual({ content: 'Нужна программа' });
    send.flush({ message: 'ok' });

    expect(sessionIds).toEqual(['sess-1']);
  });

  it('skips uploads when no files are attached', () => {
    const sessionIds: string[] = [];

    service.start('Нужна программа', []).subscribe((sessionId) => sessionIds.push(sessionId));

    http.expectOne(CHATS_URL).flush({ session_id: 'sess-2' });
    expect(uploader.items).toHaveLength(0);

    http.expectOne(`${CHATS_URL}sess-2`).flush({ message: 'ok' });

    expect(sessionIds).toEqual(['sess-2']);
  });

  it('does not send the message when an upload fails', () => {
    uploader.failure = new Error('Не удалось обработать файл');
    let received: unknown;

    service.start('Нужна программа', [makeFile('план.pdf', 1024)]).subscribe({
      error: (err) => (received = err)
    });

    http.expectOne(CHATS_URL).flush({ session_id: 'sess-3' });

    expect(received).toBeInstanceOf(Error);
    http.expectNone(`${CHATS_URL}sess-3`);
  });
});
