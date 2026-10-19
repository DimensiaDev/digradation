import { TestBed } from '@angular/core/testing';
import {
  ActivatedRouteSnapshot,
  provideRouter,
  RouterStateSnapshot,
  UrlTree
} from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom, Observable } from 'rxjs';

import { adminGuard } from './admin.guard';
import { ProfileService } from '../services/profile.service';
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

const runGuard = () =>
  TestBed.runInInjectionContext(() =>
    adminGuard({} as ActivatedRouteSnapshot, {} as RouterStateSnapshot)
  );

describe('adminGuard', () => {
  let http: HttpTestingController;
  let profile: ProfileService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        provideHttpClient(withInterceptors([])),
        provideHttpClientTesting()
      ]
    });
    http = TestBed.inject(HttpTestingController);
    profile = TestBed.inject(ProfileService);
  });

  afterEach(() => {
    http.verify();
  });

  it('fetches the profile and redirects a guest to /home when the session is unresolved', async () => {
    const tree$ = firstValueFrom(runGuard() as Observable<UrlTree>);

    http
      .expectOne(ME_URL)
      .flush({ detail: 'Unauthorized' }, { status: 401, statusText: 'Unauthorized' });

    expect((await tree$).toString()).toBe('/home');
    expect(profile.user()).toBeNull();
  });

  it('does not fetch when the session is already resolved', () => {
    profile.clear();

    expect((runGuard() as UrlTree).toString()).toBe('/home');
    http.expectNone(ME_URL);
  });

  it('redirects non-superusers to /home', () => {
    profile.fetchMe().subscribe();
    http.expectOne(ME_URL).flush({ ...testUser, role: 'user' });

    expect((runGuard() as UrlTree).toString()).toBe('/home');
  });

  it('allows superusers', () => {
    profile.fetchMe().subscribe();
    http.expectOne(ME_URL).flush(testUser);

    expect(runGuard()).toBe(true);
  });
});

describe('ProfileService fetchMe dedupe', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        provideHttpClient(withInterceptors([])),
        provideHttpClientTesting()
      ]
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('shares a single in-flight request between concurrent callers', () => {
    const profile = TestBed.inject(ProfileService);

    const first = firstValueFrom(profile.fetchMe());
    const second = firstValueFrom(profile.fetchMe());

    const requests = http.match((r) => r.url === ME_URL);
    expect(requests.length).toBe(1);

    requests[0].flush(testUser);

    return Promise.all([first, second]).then(([a, b]) => {
      expect(a.username).toBe('admin');
      expect(b.username).toBe('admin');
    });
  });
});
