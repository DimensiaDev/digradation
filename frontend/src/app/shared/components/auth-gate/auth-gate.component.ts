import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  effect,
  inject,
  viewChild
} from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { ProfileService } from '../../../core/services/profile.service';
import { LoginPrompt } from '../login-prompt/login-prompt.component';

@Component({
  selector: 'app-auth-gate',
  imports: [RouterOutlet, LoginPrompt],
  templateUrl: './auth-gate.component.html',
  styleUrl: './auth-gate.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AuthGate {
  private readonly profile = inject(ProfileService);
  private readonly dialogRef = viewChild<ElementRef<HTMLDialogElement>>('gateDialog');

  readonly user = this.profile.user;

  constructor() {
    effect(() => {
      const dialog = this.dialogRef()?.nativeElement;
      if (!dialog || this.user()) {
        return;
      }
      this.openDialog(dialog);
    });
  }

  onBackdropMouseDown(event: MouseEvent): void {
    const dialog = this.dialogRef()?.nativeElement;
    if (dialog && event.target === dialog) {
      this.closeDialog(dialog);
    }
  }

  private openDialog(dialog: HTMLDialogElement): void {
    if (dialog.open) {
      return;
    }
    if (typeof dialog.showModal === 'function') {
      dialog.showModal();
    } else {
      dialog.setAttribute('open', '');
    }
  }

  private closeDialog(dialog: HTMLDialogElement): void {
    if (!dialog.open) {
      return;
    }
    if (typeof dialog.close === 'function') {
      dialog.close();
    } else {
      dialog.removeAttribute('open');
    }
  }
}
