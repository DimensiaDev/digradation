import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  HostListener,
  computed,
  inject,
  signal
} from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { LucideBell } from '@lucide/angular';
import { AuthService } from '../../../core/services/auth.service';
import { NotificationService } from '../../../core/services/notification.service';
import { ProfileService } from '../../../core/services/profile.service';
import { ProfileComponent } from '../profile/profile.component';

@Component({
  selector: 'app-header',
  imports: [RouterLink, LucideBell, ProfileComponent],
  templateUrl: './header.component.html',
  styleUrl: './header.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class HeaderComponent {
  private readonly auth = inject(AuthService);
  private readonly notificationService = inject(NotificationService);
  private readonly profileService = inject(ProfileService);
  private readonly router = inject(Router);
  private readonly elementRef = inject(ElementRef<HTMLElement>);

  readonly notifications = this.notificationService.notifications;
  readonly notificationsCount = computed(() => this.notifications().length);

  readonly notificationsOpen = signal(false);
  readonly profileOpen = signal(false);
  readonly loggingOut = signal(false);

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

  logout(): void {
    if (this.loggingOut()) {
      return;
    }

    this.loggingOut.set(true);
    this.closeMenus();
    this.auth.logout().subscribe({
      next: () => {
        this.loggingOut.set(false);
        this.profileService.clear();
        this.notificationService.info('Вы вышли из аккаунта');
        this.router.navigate(['/login']);
      },
      error: (err: { error?: { detail?: string; message?: string }; message?: string }) => {
        this.loggingOut.set(false);
        const reason = err.error?.detail ?? err.error?.message ?? err.message ?? 'Ошибка сервера';
        this.notificationService.error('Не удалось выйти: ' + reason);
      }
    });
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
