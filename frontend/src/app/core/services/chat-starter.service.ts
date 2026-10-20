import { Injectable, inject } from '@angular/core';
import {
  EMPTY,
  Observable,
  TimeoutError,
  catchError,
  forkJoin,
  map,
  of,
  switchMap,
  throwError,
  timeout
} from 'rxjs';
import { createId } from '../../shared/utils/create-id';
import { getFileExtension } from '../../shared/utils/upload-format';
import { UploadItem } from '../models/models';
import { AttachmentUploadService } from './attachment-upload.service';
import { ChatService } from './chat.service';

/** How long the create and send calls may stay unanswered. */
export const CHAT_RESPONSE_TIMEOUT_MS = 30_000;

/** Cleanup calls are best effort; they must not stall the original error. */
export const CHAT_CLEANUP_TIMEOUT_MS = 10_000;

/**
 * Upper bound for a whole per-file upload, including the processing poll.
 * Longer than the poll window so a slow but healthy upload is not cut short,
 * but finite so a connection that never closes cannot hang the send button.
 */
export const CHAT_UPLOAD_TIMEOUT_MS = 6 * 60_000;

/** Raised when a request or an upload exceeds its deadline. */
export class ChatTimeoutError extends TimeoutError {
  constructor() {
    super();
    this.name = 'ChatTimeoutError';
    this.message = 'Превышено время ожидания загрузки файла';
  }
}

function withUploadDeadline<T>(source: Observable<T>): Observable<T> {
  return source.pipe(
    timeout({ each: CHAT_UPLOAD_TIMEOUT_MS, with: () => throwError(() => new ChatTimeoutError()) })
  );
}

@Injectable({
  providedIn: 'root'
})
export class ChatStarterService {
  private readonly chatService = inject(ChatService);
  private readonly attachmentUpload = inject(AttachmentUploadService);

  start(text: string, files: readonly File[]): Observable<string> {
    return this.chatService.create().pipe(
      timeout(CHAT_RESPONSE_TIMEOUT_MS),
      switchMap(({ session_id }) => this.fillSession(session_id, text, files))
    );
  }

  /**
   * Uploads the files and sends the message. A session that was created but
   * never filled is deleted again, together with every attachment the backend
   * has already registered, so a failed attempt leaves no orphan chat behind.
   */
  private fillSession(sessionId: string, text: string, files: readonly File[]): Observable<string> {
    const attachmentIds = new Set<number>();

    return (
      files.length
        ? forkJoin(
            files.map((file) =>
              withUploadDeadline(this.upload(sessionId, file, (id) => attachmentIds.add(id)))
            )
          )
        : of([])
    ).pipe(
      map(() => sessionId),
      switchMap((id) =>
        this.chatService.send(id, text).pipe(
          timeout(CHAT_RESPONSE_TIMEOUT_MS),
          map(() => id)
        )
      ),
      catchError((err: unknown) =>
        this.discardSession(sessionId, attachmentIds).pipe(switchMap(() => throwError(() => err)))
      )
    );
  }

  private discardSession(sessionId: string, attachmentIds: ReadonlySet<number>): Observable<void> {
    const removals = [...attachmentIds].map((attachmentId) =>
      this.attachmentUpload.remove(sessionId, attachmentId).pipe(
        timeout(CHAT_CLEANUP_TIMEOUT_MS),
        catchError(() => EMPTY)
      )
    );

    return forkJoin(removals.length > 0 ? removals : [of(null)]).pipe(
      switchMap(() =>
        this.chatService.remove(sessionId).pipe(
          timeout(CHAT_CLEANUP_TIMEOUT_MS),
          catchError(() => EMPTY)
        )
      ),
      map(() => undefined),
      catchError(() => EMPTY)
    );
  }

  private upload(
    sessionId: string,
    file: File,
    onAttachmentId: (attachmentId: number) => void
  ): Observable<number> {
    const item: UploadItem = {
      id: createId(),
      name: file.name,
      size: file.size,
      extension: getFileExtension(file.name),
      file,
      status: 'queued',
      uploaded: 0
    };
    return this.attachmentUpload.upload(item, sessionId, { onAttachmentId });
  }
}
