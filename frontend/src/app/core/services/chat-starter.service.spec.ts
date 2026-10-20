import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Observable, NEVER, TimeoutError, of, throwError } from 'rxjs';
import { vi } from 'vitest';

import {
  CHAT_RESPONSE_TIMEOUT_MS,
  CHAT_UPLOAD_TIMEOUT_MS,
  ChatStarterService
} from './chat-starter.service';
import { AttachmentUploadService, AttachmentUploadOptions } from './attachment-upload.service';
import { MAX_CONCURRENT_UPLOADS, UploadItem } from '../models/models';
import { environment } from '../../../environments/environment';

const CHATS_URL = `${environment.apiUrl}/chats/`;

class FakeUploader {
  readonly items: UploadItem[] = [];
  readonly sessionIds: string[] = [];
  readonly removed: { sessionId: string; attachmentId: number }[] = [];
  failure: Error | null = null;
  hang = false;
  manual = false;
  attachmentId: number | null = null;
  active = 0;
  peak = 0;
  readonly pending: {
    item: UploadItem;
    observer: { next(value: number): void; complete(): void };
  }[] = [];

  upload(
    item: UploadItem,
    sessionId: string,
    options: AttachmentUploadOptions = {}
  ): Observable<number> {
    this.items.push(item);
    this.sessionIds.push(sessionId);
    if (this.attachmentId !== null) {
      options.onAttachmentId?.(this.attachmentId);
    }
    if (this.manual) {
      this.active++;
      this.peak = Math.max(this.peak, this.active);
      return new Observable<number>((observer) => {
        this.pending.push({ item, observer });
        return () => {
          this.active--;
        };
      });
    }
    if (this.hang) {
      return NEVER;
    }
    if (this.failure) {
      return throwError(() => this.failure);
    }
    return of(item.size);
  }

  finishNext(): void {
    const next = this.pending.shift();
    if (!next) {
      return;
    }
    next.observer.next(next.item.size);
    next.observer.complete();
  }

  remove(sessionId: string, attachmentId: number): Observable<{ deleted: boolean }> {
    this.removed.push({ sessionId, attachmentId });
    return of({ deleted: true });
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

  it('uploads no more than the concurrency limit at once', () => {
    uploader.manual = true;
    const files = Array.from({ length: 5 }, (_, index) =>
      makeFile(`файл-${index}.pdf`, 100 + index)
    );
    const sessionIds: string[] = [];

    service.start('Нужна программа', files).subscribe((sessionId) => sessionIds.push(sessionId));
    http.expectOne(CHATS_URL).flush({ session_id: 'sess-8' });

    expect(uploader.active).toBe(MAX_CONCURRENT_UPLOADS);
    expect(uploader.peak).toBe(MAX_CONCURRENT_UPLOADS);

    while (uploader.pending.length > 0) {
      uploader.finishNext();
      expect(uploader.active).toBeLessThanOrEqual(MAX_CONCURRENT_UPLOADS);
    }

    expect(uploader.items).toHaveLength(files.length);
    expect(uploader.peak).toBe(MAX_CONCURRENT_UPLOADS);

    http.expectOne(`${CHATS_URL}sess-8`).flush({ message: 'ok' });

    expect(sessionIds).toEqual(['sess-8']);
  });

  it('skips uploads when no files are attached', () => {
    const sessionIds: string[] = [];

    service.start('Нужна программа', []).subscribe((sessionId) => sessionIds.push(sessionId));

    http.expectOne(CHATS_URL).flush({ session_id: 'sess-2' });
    expect(uploader.items).toHaveLength(0);

    http.expectOne(`${CHATS_URL}sess-2`).flush({ message: 'ok' });

    expect(sessionIds).toEqual(['sess-2']);
  });

  it('does not send the message when an upload fails and removes the session', () => {
    uploader.failure = new Error('Не удалось обработать файл');
    let received: unknown;

    service.start('Нужна программа', [makeFile('план.pdf', 1024)]).subscribe({
      error: (err) => (received = err)
    });

    http.expectOne(CHATS_URL).flush({ session_id: 'sess-3' });

    expect(received).toBeUndefined();

    const cleanup = http.expectOne(`${CHATS_URL}sess-3`);
    expect(cleanup.request.method).toBe('DELETE');
    cleanup.flush({ deleted: true });

    expect(received).toBeInstanceOf(Error);
  });

  it('removes registered attachments and the session when an upload fails', () => {
    uploader.failure = new Error('Не удалось загрузить файл в хранилище');
    uploader.attachmentId = 42;
    let received: unknown;

    service.start('Нужна программа', [makeFile('план.pdf', 1024)]).subscribe({
      error: (err) => (received = err)
    });

    http.expectOne(CHATS_URL).flush({ session_id: 'sess-6' });

    expect(uploader.removed).toEqual([{ sessionId: 'sess-6', attachmentId: 42 }]);

    http.expectOne(`${CHATS_URL}sess-6`).flush({ deleted: true });
    expect(received).toBeInstanceOf(Error);
  });

  it('removes the session when sending the message fails', () => {
    let received: unknown;

    service.start('Нужна программа', []).subscribe({ error: (err) => (received = err) });

    http.expectOne(CHATS_URL).flush({ session_id: 'sess-7' });
    http
      .expectOne(`${CHATS_URL}sess-7`)
      .flush({ detail: 'broken' }, { status: 500, statusText: 'Server Error' });

    const cleanup = http.expectOne(`${CHATS_URL}sess-7`);
    expect(cleanup.request.method).toBe('DELETE');
    cleanup.flush({ deleted: true });

    expect(received).toBeTruthy();
  });

  it('times out an upload that never finishes instead of blocking the start', () => {
    vi.useFakeTimers();
    uploader.hang = true;
    let received: unknown;

    service.start('Нужна программа', [makeFile('план.pdf', 1024)]).subscribe({
      error: (err) => (received = err)
    });

    http.expectOne(CHATS_URL).flush({ session_id: 'sess-4' });
    vi.advanceTimersByTime(CHAT_UPLOAD_TIMEOUT_MS);

    expect(received).toBeUndefined();

    http.expectOne(`${CHATS_URL}sess-4`).flush({ deleted: true });
    expect(received).toBeInstanceOf(TimeoutError);
    vi.useRealTimers();
  });

  it('times out a create request that never answers', () => {
    vi.useFakeTimers();
    let received: unknown;

    service.start('Нужна программа', []).subscribe({ error: (err) => (received = err) });

    http.expectOne(CHATS_URL);
    vi.advanceTimersByTime(CHAT_RESPONSE_TIMEOUT_MS);

    expect(received).toBeInstanceOf(TimeoutError);
    vi.useRealTimers();
  });

  it('times out a send request that never answers and removes the session', () => {
    vi.useFakeTimers();
    let received: unknown;

    service.start('Нужна программа', []).subscribe({ error: (err) => (received = err) });

    http.expectOne(CHATS_URL).flush({ session_id: 'sess-5' });
    http.expectOne(`${CHATS_URL}sess-5`);
    vi.advanceTimersByTime(CHAT_RESPONSE_TIMEOUT_MS);

    http.expectOne(`${CHATS_URL}sess-5`).flush({ deleted: true });
    expect(received).toBeInstanceOf(TimeoutError);
    vi.useRealTimers();
  });
});
