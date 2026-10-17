import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';

import { HeaderComponent } from './header.component';
import { NotificationService } from '../../../core/services/notification.service';
import { environment } from '../../../../environments/environment';

const AVATAR_URL = `${environment.apiUrl}/users/me/avatar`;

describe('HeaderComponent', () => {
  let fixture: ComponentFixture<HeaderComponent>;
  let notifications: NotificationService;
  let http: HttpTestingController;

  const bell = () => fixture.debugElement.query(By.css('.header__icon-button'));
  const profileButton = () => fixture.debugElement.query(By.css('.header__profile-button'));

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HeaderComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()]
    }).compileComponents();

    notifications = TestBed.inject(NotificationService);
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

  it('lists the notifications in a dropdown', () => {
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
    bell().nativeElement.click();
    fixture.detectChanges();

    expect(fixture.debugElement.query(By.css('.header__empty'))).not.toBeNull();
  });

  it('opens the profile menu with a link to the profile page', () => {
    profileButton().nativeElement.click();
    fixture.detectChanges();

    const item = fixture.debugElement.query(By.css('.header__menu-item'));
    expect(item.nativeElement.textContent.trim()).toBe('Профиль');
    expect(item.nativeElement.getAttribute('href')).toBe('/profile');
  });

  it('keeps only one menu open at a time', () => {
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
});
