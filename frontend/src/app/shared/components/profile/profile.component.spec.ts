import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { ProfileComponent } from './profile.component';
import { ProfileService } from '../../../core/services/profile.service';
import { environment } from '../../../../environments/environment';

const AVATAR_URL = `${environment.apiUrl}/users/me/avatar`;
const ME_URL = `${environment.apiUrl}/users/me`;

describe('ProfileComponent', () => {
  let fixture: ComponentFixture<ProfileComponent>;
  let profile: ProfileService;
  let http: HttpTestingController;

  const text = (selector: string): string | undefined =>
    (fixture.nativeElement.querySelector(selector) as HTMLElement | null)?.textContent?.trim();

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ProfileComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()]
    }).compileComponents();

    profile = TestBed.inject(ProfileService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.match(AVATAR_URL).forEach((request) => request.flush({ avatar_url: null }));
    http.verify();
  });

  const create = (): void => {
    fixture = TestBed.createComponent(ProfileComponent);
    fixture.detectChanges();
  };

  const seedUser = (user: {
    id: number;
    username: string;
    first_name: string | null;
    last_name: string | null;
    role: 'user' | 'admin' | 'superuser';
  }): void => {
    profile.fetchMe().subscribe();
    http.expectOne(ME_URL).flush(user);
  };

  it('shows the guest placeholder without requesting the avatar', () => {
    create();
    fixture.detectChanges();

    expect(text('.profile__name')).toBe('Гость');
    expect(text('.profile__role')).toBe('');
    expect(profile.avatarLoading()).toBe(false);
    expect(fixture.nativeElement.querySelector('img')?.getAttribute('src')).toBe(
      '/assets/imgs/empty-avatar.png'
    );
    http.expectNone(AVATAR_URL);
  });

  it('shows the full name and role label', () => {
    seedUser({
      id: 1,
      username: 'ivan',
      first_name: 'Иван',
      last_name: 'Петров',
      role: 'admin'
    });

    create();

    expect(text('.profile__name')).toBe('Иван Петров');
    expect(text('.profile__role')).toBe('Администратор');
  });

  it('falls back to the username when the name is empty', () => {
    seedUser({
      id: 1,
      username: 'ivan',
      first_name: null,
      last_name: null,
      role: 'user'
    });

    create();

    expect(text('.profile__name')).toBe('ivan');
  });

  it('falls back to the empty avatar image', () => {
    seedUser({
      id: 1,
      username: 'ivan',
      first_name: null,
      last_name: null,
      role: 'user'
    });

    create();
    http.expectOne(AVATAR_URL).flush({ avatar_url: null });
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('img')?.getAttribute('src')).toBe(
      '/assets/imgs/empty-avatar.png'
    );
  });

  it('resets the avatar when the image fails to load', () => {
    seedUser({
      id: 1,
      username: 'ivan',
      first_name: null,
      last_name: null,
      role: 'user'
    });

    create();
    http.expectOne(AVATAR_URL).flush({ avatar_url: 'https://cdn/broken.png' });
    fixture.detectChanges();

    fixture.nativeElement.querySelector('img').dispatchEvent(new Event('error'));

    expect(profile.avatarUrl()).toBeNull();
  });
});
