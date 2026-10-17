import { Component, inject, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import {
  AbstractControl,
  FormBuilder,
  ReactiveFormsModule,
  ValidationErrors,
  Validators
} from '@angular/forms';
import { Router } from '@angular/router';
import { catchError, EMPTY, finalize, forkJoin, map, of, switchMap } from 'rxjs';
import { ChatService } from '../../core/services/chat.service';
import { AttachmentUploadService } from '../../core/services/attachment-upload.service';
import { InitialChatStateService } from '../../core/services/initial-chat-state.service';
import { NotificationService } from '../../core/services/notification.service';
import { UploadItem } from '../../core/models/models';
import { DragNDropComponent } from '../../shared/components/drag-n-drop/drag-n-drop.component';
import { Textarea } from '../../shared/components/textarea/textarea.component';
import { getFileExtension } from '../../shared/utils/upload-format';
import { createId } from '../../shared/utils/create-id';
import { extractApiErrorMessage } from '../../shared/utils/api-error';
import { LucideArrowRight } from '@lucide/angular';

function trimmedRequired(control: AbstractControl): ValidationErrors | null {
  return String(control.value ?? '').trim().length > 0 ? null : { required: true };
}

@Component({
  selector: 'app-chat-create',
  standalone: true,
  imports: [DragNDropComponent, Textarea, ReactiveFormsModule, LucideArrowRight],
  templateUrl: './chat-create.component.html',
  styleUrl: './chat-create.component.scss'
})
export class ChatCreateComponent {
  private readonly fb = inject(FormBuilder);
  private readonly router = inject(Router);
  private readonly chatService = inject(ChatService);
  private readonly attachmentUpload = inject(AttachmentUploadService);
  private readonly initialChatState = inject(InitialChatStateService);
  private readonly notifications = inject(NotificationService);

  readonly isSubmitting = signal<boolean>(false);
  readonly selectedFiles = signal<File[]>([]);
  readonly MAX_SYMBOLS = 5000;
  readonly COUNTER_VISIBLE_FROM = 1000;

  readonly messageForm = this.fb.nonNullable.group({
    description: ['', [trimmedRequired, Validators.maxLength(this.MAX_SYMBOLS)]]
  });

  onFilesChange(files: File[]) {
    this.selectedFiles.set(files);
  }

  onSubmit() {
    if (this.messageForm.invalid) {
      this.messageForm.markAllAsTouched();
      this.notifications.error('Введите сообщение');
      return;
    }
    if (this.isSubmitting()) {
      return;
    }

    this.isSubmitting.set(true);
    const { description } = this.messageForm.getRawValue();
    const files = this.selectedFiles();

    this.chatService
      .create()
      .pipe(
        switchMap(({ session_id }) =>
          (files.length
            ? forkJoin(files.map((file) => this.uploadFile(session_id, file)))
            : of([])
          ).pipe(map(() => session_id))
        ),
        switchMap((session_id) =>
          this.chatService.send(session_id, description.trim()).pipe(map(() => session_id))
        ),
        finalize(() => this.isSubmitting.set(false)),
        catchError((err: HttpErrorResponse | Error) => {
          const msg =
            err instanceof HttpErrorResponse
              ? extractApiErrorMessage(err)
              : (err.message ?? 'Ошибка сервера');
          this.notifications.error('Не удалось создать чат: ' + msg, {
            description: 'Проверьте соединение и попробуйте ещё раз.'
          });
          return EMPTY;
        })
      )
      .subscribe({
        next: (session_id) => {
          this.notifications.success('Новый чат создан', {
            description: 'Агент уже получил ваше сообщение.'
          });
          this.initialChatState.set({
            text: description.trim(),
            files
          });
          this.messageForm.reset();
          this.router.navigate(['/chats', session_id]);
        }
      });
  }

  private uploadFile(sessionId: string, file: File) {
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
