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
import { catchError, EMPTY, finalize } from 'rxjs';
import { MAX_MESSAGE_LENGTH } from '../../core/models/models';
import { ChatStarterService } from '../../core/services/chat-starter.service';
import { InitialChatStateService } from '../../core/services/initial-chat-state.service';
import { NotificationService } from '../../core/services/notification.service';
import { DragNDropComponent } from '../../shared/components/drag-n-drop/drag-n-drop.component';
import { InputComponent } from '../../shared/components/input/input.component';
import { LucideArrowRight } from '@lucide/angular';

function trimmedRequired(control: AbstractControl): ValidationErrors | null {
  return String(control.value ?? '').trim().length > 0 ? null : { required: true };
}

@Component({
  selector: 'app-chat-create',
  standalone: true,
  imports: [DragNDropComponent, InputComponent, ReactiveFormsModule, LucideArrowRight],
  templateUrl: './chat-create.component.html',
  styleUrl: './chat-create.component.scss'
})
export class ChatCreateComponent {
  private readonly fb = inject(FormBuilder);
  private readonly router = inject(Router);
  private readonly chatStarter = inject(ChatStarterService);
  private readonly initialChatState = inject(InitialChatStateService);
  private readonly notifications = inject(NotificationService);

  readonly isSubmitting = signal<boolean>(false);
  readonly selectedFiles = signal<File[]>([]);
  readonly MAX_SYMBOLS = MAX_MESSAGE_LENGTH;
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

    this.chatStarter
      .start(description.trim(), files)
      .pipe(
        finalize(() => this.isSubmitting.set(false)),
        catchError((err: HttpErrorResponse | Error) => {
          if (!(err instanceof HttpErrorResponse)) {
            this.notifications.error(
              'Не удалось создать чат: ' + (err.message || 'Ошибка сервера'),
              {
                description: 'Проверьте соединение и попробуйте ещё раз.'
              }
            );
          }
          return EMPTY;
        })
      )
      .subscribe({
        next: (session_id) => {
          this.notifications.success('Новый чат создан', {
            description: 'Агент уже получил ваше сообщение.'
          });
          this.initialChatState.set(session_id, {
            text: description.trim(),
            files
          });
          this.messageForm.reset();
          this.router.navigate(['/chats', session_id]);
        }
      });
  }
}
