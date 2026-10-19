import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';

import { ProfileService } from './profile.service';
import { NotificationService } from './notification.service';
import { environment } from '../../../environments/environment';
import { User } from '../models/models';

const ME_URL = `${environment.apiUrl}/users/me`;

const testUser: User = {
  id: 1,
  username: 'admin',
  first_name: null,
  last_name: null,
  role: 'superuser'
};

describe('ProfileService', () => {
  let service: ProfileService;
  let notifications: NotificationService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()]
    });
    service = TestBed.inject(ProfileService);
    notifications = TestBed.inject(NotificationService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('starts with an unresolved session', () => {
    expect(service.user()).toBeNull();
    expect(service.sessionChecked()).toBe(false);
  });

  it('resolves the session with the user and marks it checked', async () => {
    const result$ = firstValueFrom(service.ensureSession());

    http.expectOne(ME_URL).flush(testUser);

    expect(await result$).toEqual(testUser);
    expect(service.user()).toEqual(testUser);
    expect(service.sessionChecked()).toBe(true);
  });

  it('resolves a guest session to null and marks it checked', async () => {
    const result$ = firstValueFrom(service.ensureSession());

    http
      .expectOne(ME_URL)
      .flush({ detail: 'Not authenticated' }, { status: 401, statusText: 'Unauthorized' });

    expect(await result$).toBeNull();
    expect(service.user()).toBeNull();
    expect(service.sessionChecked()).toBe(true);
    expect(notifications.notifications()).toHaveLength(0);
  });

  it('reports a server failure instead of passing it off as a guest session', async () => {
    const result$ = firstValueFrom(service.ensureSession());

    http.expectOne(ME_URL).flush(null, { status: 500, statusText: 'Server Error' });

    expect(await result$).toBeNull();
    expect(service.sessionChecked()).toBe(true);
    expect(notifications.notifications()[0].message).toBe('Не удалось проверить сессию');
    expect(notifications.notifications()[0].description).toContain('Ошибка сервера');
  });

  it('reports an unreachable server instead of passing it off as a guest session', async () => {
    const result$ = firstValueFrom(service.ensureSession());

    http.expectOne(ME_URL).error(new ProgressEvent('error'));

    expect(await result$).toBeNull();
    expect(notifications.notifications()[0].description).toContain(
      'Не удалось связаться с сервером'
    );
  });

  it('drops the user but keeps the session checked after clear', async () => {
    const result$ = firstValueFrom(service.ensureSession());
    http.expectOne(ME_URL).flush(testUser);
    await result$;

    service.clear();

    expect(service.user()).toBeNull();
    expect(service.sessionChecked()).toBe(true);
  });
});
