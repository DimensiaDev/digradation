import { Injectable, signal } from '@angular/core';

export interface Notification {
  id: string;
  type: 'success' | 'error' | 'info' | 'warning';
  message: string;
  description?: string;
  persistent?: boolean;
}

export interface NotificationOptions {
  description?: string;
  persistent?: boolean;
}

export interface ErrorOnceOptions extends NotificationOptions {
  windowMs?: number;
}

@Injectable({
  providedIn: 'root'
})
export class NotificationService {
  private lastError = { message: '', time: 0 };

  readonly notifications = signal<Notification[]>([]);

  success(message: string, options: NotificationOptions = {}): void {
    this.add('success', message, options);
  }

  error(message: string, options: NotificationOptions = {}): void {
    this.add('error', message, options);
  }

  errorOnce(message: string, options: ErrorOnceOptions = {}): void {
    const windowMs = options.windowMs ?? 3000;
    const now = Date.now();
    if (this.lastError.message === message && now - this.lastError.time < windowMs) {
      return;
    }
    this.lastError = { message, time: now };
    this.error(message, options);
  }

  info(message: string, options: NotificationOptions = {}): void {
    this.add('info', message, options);
  }

  warning(message: string, options: NotificationOptions = {}): void {
    this.add('warning', message, options);
  }

  remove(id: string): void {
    this.notifications.update((list) => list.filter((n) => n.id !== id));
  }

  private add(
    type: Notification['type'],
    message: string,
    options: NotificationOptions = {}
  ): void {
    const id = crypto.randomUUID();
    this.notifications.update((list) => [
      ...list,
      { id, type, message, description: options.description, persistent: options.persistent }
    ]);
  }
}
