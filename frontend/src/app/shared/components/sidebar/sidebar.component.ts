import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  input,
  signal
} from '@angular/core';
import { Router, RouterLink, RouterLinkActive } from '@angular/router';
import {
  LucideArrowLeft,
  LucideDynamicIcon,
  LucideLogOut,
  LucideMoon,
  LucidePanelLeftClose,
  LucidePanelLeftOpen,
  LucideShieldCheck,
  LucideSun
} from '@lucide/angular';
import { AuthService } from '../../../core/services/auth.service';
import { NotificationService } from '../../../core/services/notification.service';
import { ProfileService } from '../../../core/services/profile.service';
import { ThemeService } from '../../../core/services/theme.service';
import { isAdminRole } from '../../../core/models/models';
import type { NavItem, Role } from './sidebar.config';

@Component({
  selector: 'app-sidebar',
  imports: [
    LucideDynamicIcon,
    RouterLink,
    RouterLinkActive,
    LucideArrowLeft,
    LucidePanelLeftClose,
    LucidePanelLeftOpen,
    LucideLogOut,
    LucideMoon,
    LucideSun
  ],
  templateUrl: './sidebar.component.html',
  styleUrl: './sidebar.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '[class.sidebar--collapsed]': 'collapsed()'
  }
})
export class Sidebar implements OnInit {
  private static readonly ADMIN_PANEL_ITEM: NavItem = {
    label: 'Перейти в админ-панель',
    icon: LucideShieldCheck,
    route: '/admin/users'
  };

  private static readonly USER_MENU_ITEM: NavItem = {
    label: 'Вернуться в пользовательское меню',
    icon: LucideArrowLeft,
    route: '/chats'
  };

  readonly navItems = input<NavItem[]>([]);
  readonly bottomNavItems = input<NavItem[]>([]);
  readonly role = input<Role>();
  private readonly auth = inject(AuthService);
  private readonly notifications = inject(NotificationService);
  private readonly profile = inject(ProfileService);
  private readonly router = inject(Router);
  private readonly themeService = inject(ThemeService);

  readonly theme = this.themeService.theme;

  toggleTheme(): void {
    const theme = this.themeService.toggle();
    const message = theme === 'dark' ? 'Включена тёмная тема' : 'Включена светлая тема';
    this.notifications.info(message);
  }

  readonly user = this.profile.user;
  readonly visibleNavItems = computed<NavItem[]>(() =>
    this.navItems().filter((item) => item !== Sidebar.ADMIN_PANEL_ITEM)
  );
  readonly adminItem = computed<NavItem | null>(() => {
    if (this.role() !== 'user' || !isAdminRole(this.user()?.role)) {
      return null;
    }
    return Sidebar.ADMIN_PANEL_ITEM;
  });
  readonly userMenuItem = computed<NavItem | null>(() =>
    this.role() === 'admin' ? Sidebar.USER_MENU_ITEM : null
  );
  readonly displayName = computed(() => {
    const user = this.user();
    if (!user) {
      return '';
    }
    const name = `${user.first_name ?? ''} ${user.last_name ?? ''}`.trim();
    return name || user.username;
  });
  readonly isLoggingOut = signal(false);

  private static readonly EMPTY_AVATAR = '/assets/imgs/empty-avatar.png';

  readonly avatarLoading = this.profile.avatarLoading;
  readonly avatarSrc = computed(() => this.profile.avatarUrl() ?? Sidebar.EMPTY_AVATAR);

  onAvatarError(): void {
    this.profile.avatarUrl.set(null);
  }

  ngOnInit(): void {
    this.profile.loadAvatar();
  }

  logout(): void {
    this.isLoggingOut.set(true);
    this.auth.logout().subscribe({
      next: () => {
        this.isLoggingOut.set(false);
        this.profile.clear();
        this.notifications.info('Вы вышли из аккаунта');
        this.router.navigate(['/login']);
      },
      error: (err: { error?: { detail?: string; message?: string }; message?: string }) => {
        this.isLoggingOut.set(false);
        const reason = err.error?.detail ?? err.error?.message ?? err.message ?? 'Ошибка сервера';
        this.notifications.error('Не удалось выйти: ' + reason);
      }
    });
  }

  private static readonly STORAGE_KEY = 'sidebar-collapsed';

  readonly collapsed = signal(this.readCollapsed());

  private readCollapsed(): boolean {
    if (!Sidebar.isStorageAvailable()) {
      return false;
    }
    return localStorage.getItem(Sidebar.STORAGE_KEY) === 'true';
  }

  private writeCollapsed(value: boolean): void {
    if (!Sidebar.isStorageAvailable()) {
      return;
    }
    localStorage.setItem(Sidebar.STORAGE_KEY, String(value));
  }

  private static isStorageAvailable(): boolean {
    try {
      return typeof localStorage !== 'undefined' && localStorage !== null;
    } catch {
      return false;
    }
  }

  toggleCollapsed(): void {
    this.collapsed.update((collapsed) => {
      const next = !collapsed;
      this.writeCollapsed(next);
      return next;
    });
  }
}
