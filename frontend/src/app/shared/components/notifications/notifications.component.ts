import { ChangeDetectionStrategy, Component, effect, inject, signal } from '@angular/core';
import { NotificationService } from '../../../core/services/notification.service';
import { Notification } from './notification.component';

const NOTIFICATION_DURATION_MS = 5000;
const LEAVE_ANIMATION_MS = 220;

@Component({
  selector: 'app-notifications',
  imports: [Notification],
  standalone: true,
  templateUrl: './notifications.component.html',
  styleUrl: './notifications.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class NotificationsComponent {
  private readonly notificationService = inject(NotificationService);
  private readonly timers = new Map<string, ReturnType<typeof setTimeout>>();

  readonly notifications = this.notificationService.notifications;
  readonly leaving = signal<ReadonlySet<string>>(new Set());

  constructor() {
    effect(() => {
      const list = this.notifications();
      const present = new Set(list.map((n) => n.id));

      for (const [id, timer] of this.timers) {
        if (!present.has(id)) {
          clearTimeout(timer);
          this.timers.delete(id);
        }
      }

      for (const notification of list) {
        if (notification.persistent || this.timers.has(notification.id)) {
          continue;
        }
        this.timers.set(
          notification.id,
          setTimeout(() => this.close(notification.id), NOTIFICATION_DURATION_MS)
        );
      }
    });
  }

  isLeaving(id: string): boolean {
    return this.leaving().has(id);
  }

  close(id: string): void {
    if (this.leaving().has(id)) {
      return;
    }
    this.leaving.update((set) => new Set(set).add(id));

    setTimeout(() => {
      this.notificationService.remove(id);
      this.leaving.update((set) => {
        const next = new Set(set);
        next.delete(id);
        return next;
      });
    }, LEAVE_ANIMATION_MS);
  }
}
