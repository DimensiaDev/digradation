import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter, Router } from '@angular/router';

import { HeaderComponent } from './header.component';
import { AuthModalService } from '../../../core/services/auth-modal.service';
import { NotificationService } from '../../../core/services/notification.service';
import { ProfileService } from '../../../core/services/profile.service';
import { environment } from '../../../../environments/environment';
import { errorInterceptor } from '../../../core/interceptors/error.interceptor';
import { User } from '../../../core/models/models';

const AVATAR_URL = `${environment.apiUrl}/users/me/avatar`;
const ME_URL = `${environment.apiUrl}/users/me`;

const testUser: User = {
  id: 1,
  username: 'ivan',
  first_name: 'Иван',
  last_name: 'Петров',
  role: 'user'
};

describe('HeaderComponent', () => {
  let fixture: ComponentFixture<HeaderComponent>;
  let notifications: NotificationService;
  let profile: ProfileService;
  let http: HttpTestingController;

  const bell = () => fixture.debugElement.query(By.css('.header__icon-button'));
  const profileButton = () => fixture.debugElement.query(By.css('.header__profile-button'));

  const seedUser = (): void => {
    profile.fetchMe().subscribe();
    http.expectOne(ME_URL).flush(testUser);
    fixture.detectChanges();
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HeaderComponent],
      providers: [
        provideRouter([]),
        provideHttpClient(withInterceptors([errorInterceptor])),
        provideHttpClientTesting()
      ]
    }).compileComponents();

    notifications = TestBed.inject(NotificationService);
    profile = TestBed.inject(ProfileService);
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(HeaderComponent);
    fixture.detectChanges();
  });

  afterEach(() => {
    http.match(AVATAR_URL).forEach((request) => request.flush({ avatar_url: null }));
    http.verify();
  });

  it('renders the profile component inside the profile button', () => {
    expect(profileButton().query(By.css('app-profile'))).not.toBeNull();
  });

  it('shows the guest placeholder in the profile button', () => {
    expect(
      fixture.debugElement.query(By.css('.profile__name')).nativeElement.textContent.trim()
    ).toBe('Гость');
  });

  it('hides the badge while there are no notifications', () => {
    expect(fixture.debugElement.query(By.css('.header__badge'))).toBeNull();
  });

  it('shows the number of notifications on the bell', () => {
    notifications.info('Ответ готов');
    notifications.error('Ошибка сервера');
    fixture.detectChanges();

    expect(
      fixture.debugElement.query(By.css('.header__badge')).nativeElement.textContent.trim()
    ).toBe('2');
  });

  it('asks a guest to sign in instead of listing notifications', () => {
    bell().nativeElement.click();
    fixture.detectChanges();

    expect(
      fixture.debugElement.query(By.css('.header__empty')).nativeElement.textContent.trim()
    ).toBe('Войдите для получения списка уведомлений');
  });

  it('lists the notifications in a dropdown', () => {
    seedUser();
    notifications.info('Ответ готов', { description: 'Можно открывать чат' });
    fixture.detectChanges();

    bell().nativeElement.click();
    fixture.detectChanges();

    expect(
      fixture.debugElement.query(By.css('.header__notice-title')).nativeElement.textContent
    ).toBe('Ответ готов');
    expect(
      fixture.debugElement.query(By.css('.header__notice-description')).nativeElement.textContent
    ).toBe('Можно открывать чат');
  });

  it('shows an empty state when the dropdown has nothing to list', () => {
    seedUser();
    bell().nativeElement.click();
    fixture.detectChanges();

    expect(
      fixture.debugElement.query(By.css('.header__empty')).nativeElement.textContent.trim()
    ).toBe('Нет уведомлений');
  });

  it('opens the profile menu with a link to the profile page', () => {
    seedUser();
    profileButton().nativeElement.click();
    fixture.detectChanges();

    const item = fixture.debugElement.query(By.css('.header__menu-item'));
    expect(item.nativeElement.textContent.trim()).toBe('Профиль');
    expect(item.nativeElement.getAttribute('href')).toBe('/profile');
  });

  it('opens the sign-in dialog straight from the guest profile button', () => {
    const modal = TestBed.inject(AuthModalService);

    profileButton().nativeElement.click();
    fixture.detectChanges();

    expect(profileButton().nativeElement.getAttribute('aria-label')).toBe('Войти в аккаунт');
    expect(modal.isOpen()).toBe(true);
    expect(modal.returnUrl()).toBe('/');
    expect(fixture.debugElement.query(By.css('.header__dropdown--profile'))).toBeNull();
  });

  it('keeps only one menu open at a time', () => {
    seedUser();
    bell().nativeElement.click();
    fixture.detectChanges();
    expect(fixture.debugElement.query(By.css('.header__dropdown--notifications'))).not.toBeNull();

    profileButton().nativeElement.click();
    fixture.detectChanges();

    expect(fixture.debugElement.query(By.css('.header__dropdown--notifications'))).toBeNull();
    expect(fixture.debugElement.query(By.css('.header__dropdown--profile'))).not.toBeNull();
  });

  it('closes the menus on Escape', () => {
    bell().nativeElement.click();
    fixture.detectChanges();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    fixture.detectChanges();

    expect(fixture.debugElement.query(By.css('.header__dropdown'))).toBeNull();
  });

  it('logs out from the profile menu and returns to the home page', () => {
    seedUser();
    const router = TestBed.inject(Router);
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);
    const clear = vi.spyOn(TestBed.inject(ProfileService), 'clear');

    profileButton().nativeElement.click();
    fixture.detectChanges();

    const logoutButton = fixture.debugElement.query(By.css('.header__menu-item--danger'));
    expect(logoutButton.nativeElement.textContent.trim()).toBe('Выход');

    logoutButton.nativeElement.click();
    fixture.detectChanges();

    http.expectOne(`${environment.apiUrl}/auth/logout`).flush({ message: 'ok' });
    fixture.detectChanges();

    expect(clear).toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith(['/home']);
  });

  it('reports a failed logout without navigating away', () => {
    seedUser();
    const router = TestBed.inject(Router);
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);

    profileButton().nativeElement.click();
    fixture.detectChanges();

    fixture.debugElement.query(By.css('.header__menu-item--danger')).nativeElement.click();
    fixture.detectChanges();

    http
      .expectOne(`${environment.apiUrl}/auth/logout`)
      .flush({ detail: 'сломалось' }, { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(navigate).not.toHaveBeenCalled();
    expect(notifications.notifications().at(-1)?.message).toBe('Ошибка сервера');
  });
});
