import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { LucideLock } from '@lucide/angular';
import { AuthModalService } from '../../../core/services/auth-modal.service';

@Component({
  selector: 'app-login-prompt',
  imports: [LucideLock],
  templateUrl: './login-prompt.component.html',
  styleUrl: './login-prompt.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class LoginPrompt {
  private readonly authModal = inject(AuthModalService);

  openAuthModal(): void {
    this.authModal.open();
  }
}
