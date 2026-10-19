import { Injectable, inject, signal } from '@angular/core';
import { Router } from '@angular/router';

@Injectable({
  providedIn: 'root'
})
export class AuthModalService {
  private readonly router = inject(Router);

  private readonly isOpenState = signal(false);
  private readonly returnUrlState = signal('/home');

  readonly isOpen = this.isOpenState.asReadonly();
  readonly returnUrl = this.returnUrlState.asReadonly();

  open(returnUrl?: string): void {
    this.returnUrlState.set(this.sanitize(returnUrl ?? this.router.url));
    this.isOpenState.set(true);
  }

  close(): void {
    this.isOpenState.set(false);
  }

  private sanitize(value: string): string {
    if (!value.startsWith('/') || value.startsWith('//')) {
      return '/home';
    }
    return value;
  }
}
