import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  HostListener,
  computed,
  inject,
  signal
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { LucideBell } from '@lucide/angular';
import { NotificationService } from '../../../core/services/notification.service';
import { ProfileComponent } from '../profile/profile.component';

@Component({
  selector: 'app-header',
  imports: [RouterLink, LucideBell, ProfileComponent],
  templateUrl: './header.component.html',
  styleUrl: './header.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class HeaderComponent {
  private readonly notificationService = inject(NotificationService);
  private readonly elementRef = inject(ElementRef<HTMLElement>);

  readonly notifications = this.notificationService.notifications;
  readonly notificationsCount = computed(() => this.notifications().length);

  readonly notificationsOpen = signal(false);
  readonly profileOpen = signal(false);

  toggleNotifications(): void {
    this.profileOpen.set(false);
    this.notificationsOpen.update((open) => !open);
  }

  toggleProfile(): void {
    this.notificationsOpen.set(false);
    this.profileOpen.update((open) => !open);
  }

  closeMenus(): void {
    this.notificationsOpen.set(false);
    this.profileOpen.set(false);
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent): void {
    if (!this.elementRef.nativeElement.contains(event.target as Node)) {
      this.closeMenus();
    }
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    this.closeMenus();
  }
}
