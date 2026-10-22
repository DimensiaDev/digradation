import { ChangeDetectionStrategy, Component, computed, inject, input, signal } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';
import {
  LucideArrowLeft,
  LucideDynamicIcon,
  LucideMoon,
  LucidePanelLeftClose,
  LucidePanelLeftOpen,
  LucideShieldCheck,
  LucideSun
} from '@lucide/angular';
import { NotificationService } from '../../../core/services/notification.service';
import { ProfileService } from '../../../core/services/profile.service';
import { ThemeService } from '../../../core/services/theme.service';
import { isAdminRole } from '../../../core/models/models';
import type { NavItem, Role } from '../../navigation/navigation.config';

@Component({
  selector: 'app-sidebar',
  imports: [
    LucideDynamicIcon,
    RouterLink,
    RouterLinkActive,
    LucidePanelLeftClose,
    LucidePanelLeftOpen,
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
export class Sidebar {
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

  // The router's componentInputBinding sets every declared input from the
  // route data, and a missing key arrives as undefined — which would wipe the
  // default. The transforms keep the lists arrays no matter who binds them.
  readonly navItems = input<NavItem[], NavItem[] | undefined>([], {
    transform: (items) => items ?? []
  });
  readonly bottomNavItems = input<NavItem[], NavItem[] | undefined>([], {
    transform: (items) => items ?? []
  });
  readonly role = input<Role>();
  private readonly notifications = inject(NotificationService);
  private readonly profile = inject(ProfileService);
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
