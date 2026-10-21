import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { ActivatedRoute, provideRouter } from '@angular/router';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { AuthGate } from './auth-gate.component';
import { ProfileService } from '../../../core/services/profile.service';
import { environment } from '../../../../environments/environment';
import { User } from '../../../core/models/models';

const ME_URL = `${environment.apiUrl}/users/me`;

const testUser: User = {
  id: 1,
  username: 'ivan',
  first_name: null,
  last_name: null,
  role: 'user'
};

describe('AuthGate', () => {
  let fixture: ComponentFixture<AuthGate>;
  let profile: ProfileService;
  let http: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AuthGate],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()]
    }).compileComponents();

    profile = TestBed.inject(ProfileService);
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(AuthGate);
    fixture.detectChanges();
    fixture.detectChanges();
  });

  afterEach(() => {
    http.verify();
  });

  it('shows the inline prompt for a guest without a dialog', () => {
    expect(
      fixture.nativeElement.querySelector('.auth-gate__prompt app-login-prompt')
    ).not.toBeNull();
    expect(fixture.nativeElement.querySelector('dialog')).toBeNull();
    expect(fixture.debugElement.query(By.css('router-outlet'))).toBeNull();
  });

  it('renders the routed page and drops the prompt once the user is known', () => {
    profile.fetchMe().subscribe();
    http.expectOne(ME_URL).flush(testUser);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.auth-gate__prompt')).toBeNull();
    expect(fixture.debugElement.query(By.css('router-outlet'))).not.toBeNull();
  });
});

describe('AuthGate guest preview', () => {
  let fixture: ComponentFixture<AuthGate>;
  let profile: ProfileService;
  let http: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AuthGate],
      providers: [
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { data: { authGateMode: 'preview' } } }
        }
      ]
    }).compileComponents();

    profile = TestBed.inject(ProfileService);
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(AuthGate);
    fixture.detectChanges();
    fixture.detectChanges();
  });

  afterEach(() => {
    http.verify();
  });

  it('keeps the page mounted behind a blurred, inert prompt for a guest', () => {
    const preview = fixture.nativeElement.querySelector('.auth-gate__preview');

    expect(preview).not.toBeNull();
    expect(preview.classList.contains('auth-gate__preview--locked')).toBe(true);
    expect(preview.hasAttribute('inert')).toBe(true);
    expect(preview.getAttribute('aria-hidden')).toBe('true');
    expect(fixture.nativeElement.querySelector('.auth-gate__preview router-outlet')).not.toBeNull();
    expect(
      fixture.nativeElement.querySelector('.auth-gate__overlay app-login-prompt')
    ).not.toBeNull();
    expect(fixture.nativeElement.querySelector('.auth-gate__prompt')).toBeNull();
  });

  it('removes the overlay and unlocks the page once the user is known', () => {
    profile.fetchMe().subscribe();
    http.expectOne(ME_URL).flush(testUser);
    fixture.detectChanges();

    const preview = fixture.nativeElement.querySelector('.auth-gate__preview');

    expect(fixture.nativeElement.querySelector('.auth-gate__overlay')).toBeNull();
    expect(preview.classList.contains('auth-gate__preview--locked')).toBe(false);
    expect(preview.hasAttribute('inert')).toBe(false);
    expect(preview.hasAttribute('aria-hidden')).toBe(false);
  });
});
