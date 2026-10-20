import { Injectable, inject } from '@angular/core';
import { Observable, forkJoin, map, of, switchMap } from 'rxjs';
import { createId } from '../../shared/utils/create-id';
import { getFileExtension } from '../../shared/utils/upload-format';
import { UploadItem } from '../models/models';
import { AttachmentUploadService } from './attachment-upload.service';
import { ChatService } from './chat.service';

@Injectable({
  providedIn: 'root'
})
export class ChatStarterService {
  private readonly chatService = inject(ChatService);
  private readonly attachmentUpload = inject(AttachmentUploadService);

  start(text: string, files: readonly File[]): Observable<string> {
    return this.chatService.create().pipe(
      switchMap(({ session_id }) =>
        (files.length ? forkJoin(files.map((file) => this.upload(session_id, file))) : of([])).pipe(
          map(() => session_id)
        )
      ),
      switchMap((session_id) => this.chatService.send(session_id, text).pipe(map(() => session_id)))
    );
  }

  private upload(sessionId: string, file: File): Observable<number> {
    const item: UploadItem = {
      id: createId(),
      name: file.name,
      size: file.size,
      extension: getFileExtension(file.name),
      file,
      status: 'queued',
      uploaded: 0
    };
    return this.attachmentUpload.upload(item, sessionId);
  }
}
