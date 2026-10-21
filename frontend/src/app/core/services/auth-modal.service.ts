import { Injectable, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { PendingChatDraftService } from './pending-chat-draft.service';

@Injectable({
  providedIn: 'root'
})
export class AuthModalService {
  private readonly router = inject(Router);
  private readonly pendingDraft = inject(PendingChatDraftService);

  private readonly isOpenState = signal(false);
  private readonly returnUrlState = signal('/home');

  readonly isOpen = this.isOpenState.asReadonly();
  readonly returnUrl = this.returnUrlState.asReadonly();

  open(returnUrl?: string): void {
    this.returnUrlState.set(this.sanitize(returnUrl ?? this.router.url));
    this.isOpenState.set(true);
  }

  /**
   * Dismisses the dialog without signing in. Any guest draft belonged to this
   * abandoned attempt and must not be resumed by a later, unrelated login.
   */
  close(): void {
    this.pendingDraft.clear();
    this.isOpenState.set(false);
  }

  /** Closes the dialog after a successful login, keeping the pending draft. */
  complete(): void {
    this.isOpenState.set(false);
  }

  private sanitize(value: string): string {
    if (!value.startsWith('/') || value.startsWith('//')) {
      return '/home';
    }
    return value;
  }
}
