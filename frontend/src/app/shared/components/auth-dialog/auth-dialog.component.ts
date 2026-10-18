import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  effect,
  inject,
  signal,
  viewChild
} from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { catchError, of } from 'rxjs';
import { LucideEye, LucideEyeOff, LucideLock, LucideUser, LucideX } from '@lucide/angular';

import { AuthModalService } from '../../../core/services/auth-modal.service';
import { AuthService } from '../../../core/services/auth.service';
import { NotificationService } from '../../../core/services/notification.service';
import { ProfileService } from '../../../core/services/profile.service';
import { extractApiErrorMessage } from '../../utils/api-error';
import { InputComponent } from '../input/input.component';

const USERNAME_MAX_LENGTH = 32;

const PASSWORD_MIN_LENGTH = 8;
const PASSWORD_MAX_LENGTH = 128;

@Component({
  selector: 'app-auth-dialog',
  imports: [ReactiveFormsModule, InputComponent, LucideX],
  templateUrl: './auth-dialog.component.html',
  styleUrl: './auth-dialog.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AuthDialogComponent {
  private readonly fb = inject(FormBuilder);
  private readonly auth = inject(AuthService);
  private readonly profile = inject(ProfileService);
  private readonly notifications = inject(NotificationService);
  private readonly router = inject(Router);
  private readonly modal = inject(AuthModalService);

  readonly userIcon = LucideUser;
  readonly lockIcon = LucideLock;
  readonly eyeIcon = LucideEye;
  readonly eyeOffIcon = LucideEyeOff;

  private readonly dialogRef = viewChild<ElementRef<HTMLDialogElement>>('dialogEl');

  readonly form = this.fb.group({
    username: ['', [Validators.required, Validators.maxLength(USERNAME_MAX_LENGTH)]],
    password: [
      '',
      [
        Validators.required,
        Validators.minLength(PASSWORD_MIN_LENGTH),
        Validators.maxLength(PASSWORD_MAX_LENGTH)
      ]
    ]
  });

  readonly isSubmitting = signal(false);
  readonly showPassword = signal(false);

  constructor() {
    effect(() => {
      const dialog = this.dialogRef()?.nativeElement;
      if (!dialog) {
        return;
      }
      if (this.modal.isOpen()) {
        this.openDialog(dialog);
      } else {
        this.closeDialog(dialog);
      }
    });
  }

  togglePassword(): void {
    this.showPassword.update((visible) => !visible);
  }

  close(): void {
    this.modal.close();
  }

  onCancel(event: Event): void {
    event.preventDefault();
    this.close();
  }

  onNativeClose(): void {
    if (this.modal.isOpen()) {
      this.modal.close();
    }
  }

  onBackdropMouseDown(event: MouseEvent): void {
    if (event.target === this.dialogRef()?.nativeElement) {
      this.close();
    }
  }

  submit(): void {
    if (this.isSubmitting()) {
      return;
    }

    this.clearCredentialsError();

    if (this.form.invalid) {
      this.form.markAllAsTouched();
      const { username, password } = this.form.controls;
      if (username.hasError('required') || password.hasError('required')) {
        this.notifications.error('Заполните логин и пароль');
      } else if (password.hasError('minlength')) {
        this.notifications.error('Пароль должен быть не короче 8 символов');
      } else if (username.hasError('maxlength')) {
        this.notifications.error('Логин не может быть длиннее 32 символов');
      } else {
        this.notifications.error('Пароль не может быть длиннее 128 символов');
      }
      return;
    }

    this.isSubmitting.set(true);
    const { username, password } = this.form.getRawValue();
    this.auth.login(username?.trim() ?? '', password ?? '').subscribe({
      next: () => this.completeLogin(),
      error: (err: HttpErrorResponse) => {
        this.isSubmitting.set(false);
        this.showCredentialsError();

        if (err.status === 401) {
          this.notifications.error('Перепроверьте правильность введённого логина и пароля', {
            description: 'Проверьте раскладку и регистр символов.'
          });
          return;
        }

        this.notifications.error('Не удалось войти: ' + extractApiErrorMessage(err), {
          description: 'Попробуйте войти ещё раз позже.'
        });
      }
    });
  }

  private completeLogin(): void {
    this.profile
      .fetchMe()
      .pipe(catchError(() => of(null)))
      .subscribe(() => this.finishLogin());
  }

  private finishLogin(): void {
    this.isSubmitting.set(false);
    this.form.reset();
    this.showPassword.set(false);
    const target = this.modal.returnUrl();
    this.modal.close();
    this.notifications.success('Вы вошли в систему', {
      description: 'Сейчас откроется рабочая область.'
    });
    this.router.navigateByUrl(target);
  }

  private openDialog(dialog: HTMLDialogElement): void {
    if (dialog.open) {
      return;
    }
    this.isSubmitting.set(false);
    this.clearCredentialsError();
    if (typeof dialog.showModal === 'function') {
      dialog.showModal();
    } else {
      dialog.setAttribute('open', '');
    }
  }

  private closeDialog(dialog: HTMLDialogElement): void {
    if (!dialog.hasAttribute('open')) {
      return;
    }
    if (typeof dialog.close === 'function' && dialog.open) {
      dialog.close();
    } else {
      dialog.removeAttribute('open');
    }
  }

  private clearCredentialsError(): void {
    for (const control of [this.form.controls.username, this.form.controls.password]) {
      if (!control.hasError('credentials')) {
        continue;
      }
      const errors = { ...control.errors };
      delete errors['credentials'];
      control.setErrors(Object.keys(errors).length ? errors : null);
    }
  }

  private showCredentialsError(): void {
    for (const control of [this.form.controls.username, this.form.controls.password]) {
      control.setErrors({ ...(control.errors ?? {}), credentials: true });
      control.markAsTouched();
    }
  }
}
