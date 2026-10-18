import { Routes } from '@angular/router';
import { ChatCreateComponent } from './features/chat-create/chat-create.component';
import { ChatComponent } from './features/chat/chat.component';
import { Layout } from './shared/components/layout/layout.component';
import {
  ADMIN_NAV_ITEMS,
  USER_BOTTOM_NAV_ITEMS,
  USER_NAV_ITEMS
} from './shared/components/sidebar/sidebar.config';
import { HistoryPage } from './pages/history-page/history-page.component';
import { SettingsPage } from './pages/settings-page/settings-page.component';
import { ProfilePage } from './pages/profile-page/profile-page.component';
import { HomePage } from './pages/home-page/home-page.component';
import { HelpPage } from './pages/help-page/help-page.component';
import { adminGuard } from './core/guards/admin.guard';
import { AuthGate } from './shared/components/auth-gate/auth-gate.component';
import { UsersPage } from './pages/admin/users-page/users-page.component';
import { AdminSettingsPage } from './pages/admin/settings-page/settings-page.component';
import { SectionPlaceholder } from './shared/components/section-placeholder/section-placeholder.component';

export const routes: Routes = [
  {
    path: 'admin',
    component: Layout,
    canActivate: [adminGuard],
    data: { navItems: ADMIN_NAV_ITEMS, role: 'admin' },
    children: [
      { path: '', redirectTo: '/admin/users', pathMatch: 'full' },
      { path: 'users', component: UsersPage },
      {
        path: 'actions',
        component: SectionPlaceholder,
        data: { title: 'Журнал действий', subtitle: 'История действий пользователей' }
      },
      {
        path: 'settings',
        component: AdminSettingsPage
      },
      {
        path: 'system',
        component: SectionPlaceholder,
        data: { title: 'О системе', subtitle: 'Информация о версии и компонентах' }
      }
    ]
  },
  {
    path: '',
    component: Layout,
    data: { navItems: USER_NAV_ITEMS, bottomNavItems: USER_BOTTOM_NAV_ITEMS, role: 'user' },
    children: [
      { path: '', redirectTo: '/home', pathMatch: 'full' },
      { path: 'home', component: HomePage },
      { path: 'help', component: HelpPage },
      {
        path: '',
        component: AuthGate,
        children: [
          {
            path: 'programs',
            component: SectionPlaceholder,
            data: { title: 'Мои программы', subtitle: 'Здесь появятся ваши программы' }
          },
          {
            path: 'library',
            component: SectionPlaceholder,
            data: { title: 'Библиотека', subtitle: 'Здесь появятся материалы библиотеки' }
          },
          { path: 'create', component: ChatCreateComponent },
          { path: 'settings', component: SettingsPage },
          { path: 'chats', component: HistoryPage },
          { path: 'chats/:id', component: ChatComponent },
          { path: 'profile', component: ProfilePage }
        ]
      }
    ]
  },
  { path: '**', redirectTo: '/home' }
];
