import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Component } from '@angular/core';
import { By } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter, Router, Routes } from '@angular/router';

import { AuthDialogComponent } from './auth-dialog.component';
import { AuthModalService } from '../../../core/services/auth-modal.service';
import { NotificationService } from '../../../core/services/notification.service';
import { NotificationsComponent } from '../notifications/notifications.component';
import { environment } from '../../../../environments/environment';
import { User } from '../../../core/models/models';

const LOGIN_URL = `${environment.apiUrl}/auth/login`;
const ME_URL = `${environment.apiUrl}/users/me`;

const testUser: User = {
  id: 1,
  username: 'ivan',
  first_name: null,
  last_name: null,
  role: 'user'
};

@Component({ selector: 'app-blank', template: '' })
class BlankComponent {}

const TEST_ROUTES: Routes = [
  { path: 'home', component: BlankComponent },
  { path: 'create', component: BlankComponent },
  { path: 'chats/:id', component: BlankComponent }
];

@Component({
  selector: 'app-auth-dialog-test-host',
  imports: [AuthDialogComponent, NotificationsComponent],
  template: `<app-notifications /><app-auth-dialog />`
})
class TestHost {}

describe('AuthDialogComponent', () => {
  let fixture: ComponentFixture<TestHost>;
  let router: Router;
  let http: HttpTestingController;
  let modal: AuthModalService;

  const page = () =>
    fixture.debugElement.query(By.directive(AuthDialogComponent))
      .componentInstance as AuthDialogComponent;
  const dialogEl = () =>
    fixture.nativeElement.querySelector('dialog.auth-dialog') as HTMLDialogElement | null;
  const usernameInput = () =>
    fixture.debugElement.query(By.css('#username')).nativeElement as HTMLInputElement;
  const passwordInput = () =>
    fixture.debugElement.query(By.css('#password')).nativeElement as HTMLInputElement;
  const submitButton = () =>
    fixture.debugElement.query(By.css('button[type="submit"]')).nativeElement as HTMLButtonElement;
  const notificationTitle = () => fixture.debugElement.query(By.css('.notification__title'));
  const notificationText = () => notificationTitle()?.nativeElement.textContent ?? '';

  const createPage = (): void => {
    fixture = TestBed.createComponent(TestHost);
    fixture.detectChanges();
  };

  const openModal = (returnUrl?: string): void => {
    modal.open(returnUrl);
    fixture.detectChanges();
  };

  const type = (input: HTMLInputElement, value: string): void => {
    input.value = value;
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
  };

  const fillCredentials = (username: string, password: string): void => {
    type(usernameInput(), username);
    type(passwordInput(), password);
  };

  const submitForm = (): void => {
    fixture.debugElement.query(By.css('form')).triggerEventHandler('submit', new Event('submit'));
    fixture.detectChanges();
  };

  const flushLogin = (body: object | null, status = 200): void => {
    http.expectOne(LOGIN_URL).flush(body, { status, statusText: status === 200 ? 'OK' : 'Error' });
  };

  const flushProfile = (body: object | null = testUser, status = 200): void => {
    http.expectOne(ME_URL).flush(body, { status, statusText: status === 200 ? 'OK' : 'Error' });
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideRouter(TEST_ROUTES), provideHttpClient(), provideHttpClientTesting()]
    }).compileComponents();

    router = TestBed.inject(Router);
    http = TestBed.inject(HttpTestingController);
    modal = TestBed.inject(AuthModalService);
  });

  afterEach(() => {
    http.verify();
  });

  describe('dialog lifecycle', () => {
    it('stays closed until the modal service opens it', () => {
      createPage();

      expect(modal.isOpen()).toBe(false);
      expect(dialogEl()).toBeNull();

      openModal();

      expect(dialogEl()?.hasAttribute('open')).toBe(true);
    });

    it('closes when the modal service closes and when the close button is clicked', () => {
      createPage();
      openModal();

      modal.close();
      fixture.detectChanges();
      expect(dialogEl()).toBeNull();

      openModal();
      fixture.debugElement.query(By.css('.auth-dialog__close')).nativeElement.click();
      fixture.detectChanges();
      expect(modal.isOpen()).toBe(false);
      expect(dialogEl()).toBeNull();
    });

    it('closes on a backdrop press but keeps the panel presses', () => {
      createPage();
      openModal();

      fixture.nativeElement
        .querySelector('.auth-dialog__panel')
        .dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      expect(modal.isOpen()).toBe(true);

      dialogEl()?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      fixture.detectChanges();
      expect(modal.isOpen()).toBe(false);
      expect(dialogEl()).toBeNull();
    });

    it('treats a cancel event as a close request', () => {
      createPage();
      openModal();

      const event = new Event('cancel', { cancelable: true });
      dialogEl()?.dispatchEvent(event);
      fixture.detectChanges();

      expect(event.defaultPrevented).toBe(true);
      expect(modal.isOpen()).toBe(false);
    });

    it('syncs the service when the dialog reports a native close', () => {
      createPage();
      openModal();

      dialogEl()?.dispatchEvent(new Event('close'));

      expect(modal.isOpen()).toBe(false);
    });

    it('resets the submitting state and the credentials error on reopen', async () => {
      createPage();
      openModal();
      fillCredentials('ivan', 'wrong-password');
      submitForm();
      flushLogin({ detail: 'Неверные данные' }, 401);
      await fixture.whenStable();

      page().isSubmitting.set(true);
      modal.close();
      fixture.detectChanges();
      openModal();

      expect(page().isSubmitting()).toBe(false);
      expect(page().form.controls.username.hasError('credentials')).toBe(false);
    });
  });

  describe('rendering', () => {
    it('renders the login form with the Russian labels and an enabled submit button', () => {
      createPage();
      openModal();

      expect(fixture.nativeElement.textContent).toContain('Вход в аккаунт');
      expect(
        fixture.debugElement.query(By.css('label[for="username"]')).nativeElement.textContent
      ).toContain('Логин');
      expect(
        fixture.debugElement.query(By.css('label[for="password"]')).nativeElement.textContent
      ).toContain('Пароль');
      expect(submitButton().textContent.trim()).toBe('Войти');
      expect(submitButton().disabled).toBe(false);
      expect(page().form.controls.username.value).toBe('');
      expect(page().form.controls.password.value).toBe('');
    });

    it('switches the password field between masked and plain text', () => {
      createPage();
      openModal();
      expect(passwordInput().type).toBe('password');

      page().togglePassword();
      fixture.detectChanges();
      expect(page().showPassword()).toBe(true);
      expect(passwordInput().type).toBe('text');

      page().togglePassword();
      fixture.detectChanges();
      expect(page().showPassword()).toBe(false);
      expect(passwordInput().type).toBe('password');
    });
  });

  describe('successful login', () => {
    it('posts the credentials, refreshes the profile, closes and navigates to the current url', async () => {
      await router.navigateByUrl('/create');
      createPage();
      openModal();
      fillCredentials('ivan', 'password123');
      submitForm();

      const req = http.expectOne(LOGIN_URL);
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual({ username: 'ivan', password: 'password123' });
      req.flush({ message: 'ok' });
      await fixture.whenStable();

      flushProfile();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(modal.isOpen()).toBe(false);
      expect(router.url).toBe('/create');
      expect(page().isSubmitting()).toBe(false);
      expect(page().form.controls.username.value).toBeNull();
      expect(notificationText()).toBe('Вы вошли в систему');
    });

    it('trims the username before sending it', async () => {
      createPage();
      openModal('/create');
      fillCredentials('  ivan  ', 'password123');
      submitForm();

      const req = http.expectOne(LOGIN_URL);
      expect(req.request.body).toEqual({ username: 'ivan', password: 'password123' });

      req.flush({ message: 'ok' });
      await fixture.whenStable();
      flushProfile();
      await fixture.whenStable();
    });

    it('navigates to the requested return url', async () => {
      await router.navigateByUrl('/chats/42');
      createPage();
      openModal('/chats/abc');
      fillCredentials('ivan', 'password123');
      submitForm();
      flushLogin({ message: 'ok' });
      await fixture.whenStable();
      flushProfile();
      await fixture.whenStable();

      expect(router.url).toBe('/chats/abc');
    });

    it('falls back to the home page for a rejected return url', async () => {
      createPage();
      openModal('//evil.example');
      fillCredentials('ivan', 'password123');
      submitForm();
      flushLogin({ message: 'ok' });
      await fixture.whenStable();
      flushProfile();
      await fixture.whenStable();

      expect(router.url).toBe('/home');
    });

    it('disables the submit button while the request is in flight', async () => {
      createPage();
      openModal('/create');
      fillCredentials('ivan', 'password123');
      submitForm();

      const req = http.expectOne(LOGIN_URL);
      expect(submitButton().disabled).toBe(true);
      expect(page().isSubmitting()).toBe(true);

      req.flush({ message: 'ok' });
      await fixture.whenStable();
      expect(submitButton().disabled).toBe(true);

      flushProfile();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(page().isSubmitting()).toBe(false);
      expect(dialogEl()).toBeNull();
    });

    it('ignores a second submit while the first request is in flight', () => {
      createPage();
      openModal('/create');
      fillCredentials('ivan', 'password123');
      submitForm();
      http.expectOne(LOGIN_URL);

      page().submit();

      http.expectNone(LOGIN_URL);
    });

    it('closes the dialog and navigates even when the profile refresh fails', async () => {
      createPage();
      openModal('/create');
      fillCredentials('ivan', 'password123');
      submitForm();
      flushLogin({ message: 'ok' });
      await fixture.whenStable();

      flushProfile(null, 500);
      await fixture.whenStable();
      fixture.detectChanges();

      expect(modal.isOpen()).toBe(false);
      expect(router.url).toBe('/create');
      expect(notificationText()).toBe('Вы вошли в систему');
    });
  });

  describe('failed login', () => {
    it('shows the wrong credentials message and marks both fields on 401', async () => {
      createPage();
      openModal();
      fillCredentials('ivan', 'wrong-password');
      submitForm();
      flushLogin({ detail: 'Неверные данные' }, 401);
      await fixture.whenStable();
      fixture.detectChanges();

      expect(notificationText()).toBe('Перепроверьте правильность введённого логина и пароля');
      expect(page().form.controls.username.hasError('credentials')).toBe(true);
      expect(page().form.controls.password.hasError('credentials')).toBe(true);
      expect(page().isSubmitting()).toBe(false);
      expect(submitButton().disabled).toBe(false);
      expect(router.url).not.toBe('/chats/abc');
    });

    it('clears the credentials error when the form is submitted again', async () => {
      createPage();
      openModal('/create');
      fillCredentials('ivan', 'wrong-password');
      submitForm();
      flushLogin({ detail: 'Неверные данные' }, 401);
      await fixture.whenStable();
      expect(page().form.controls.password.hasError('credentials')).toBe(true);

      type(passwordInput(), 'password123');
      page().submit();

      expect(page().form.controls.username.hasError('credentials')).toBe(false);
      expect(page().form.controls.password.hasError('credentials')).toBe(false);
      http.expectOne(LOGIN_URL).flush({ message: 'ok' });
      await fixture.whenStable();
      flushProfile();
      await fixture.whenStable();
    });

    it('marks the inputs as invalid and swaps the credentials error for the validator one on edit', async () => {
      createPage();
      openModal();
      fillCredentials('ivan', 'wrong-password');
      submitForm();
      flushLogin({ detail: 'Неверные данные' }, 401);
      await fixture.whenStable();
      fixture.detectChanges();

      expect(fixture.debugElement.query(By.css('app-input')).nativeElement.classList).toContain(
        'input--error'
      );

      type(passwordInput(), 'short');

      expect(page().form.controls.password.hasError('credentials')).toBe(false);
      expect(page().form.controls.password.hasError('minlength')).toBe(true);
    });

    it('surfaces the server error message on 500', async () => {
      createPage();
      openModal();
      fillCredentials('ivan', 'password123');
      submitForm();
      flushLogin(null, 500);
      await fixture.whenStable();
      fixture.detectChanges();

      expect(notificationText()).toBe('Не удалось войти: Ошибка сервера');
      expect(router.url).not.toBe('/chats/abc');
    });

    it('surfaces the API detail message on 403', async () => {
      createPage();
      openModal();
      fillCredentials('ivan', 'password123');
      submitForm();
      flushLogin({ detail: 'Пользователь заблокирован' }, 403);
      await fixture.whenStable();
      fixture.detectChanges();

      expect(notificationText()).toBe('Не удалось войти: Пользователь заблокирован');
    });

    it('reports an unreachable server when the request never leaves', async () => {
      createPage();
      openModal();
      fillCredentials('ivan', 'password123');
      submitForm();
      flushLogin(null, 0);
      await fixture.whenStable();
      fixture.detectChanges();

      expect(notificationText()).toBe('Не удалось войти: Не удалось связаться с сервером');
    });
  });

  describe('client-side validation', () => {
    it('asks for both fields when the form is empty', () => {
      createPage();
      openModal();
      submitForm();

      expect(notificationText()).toBe('Заполните логин и пароль');
      expect(page().form.controls.username.touched).toBe(true);
      expect(page().form.controls.password.touched).toBe(true);
      http.expectNone(LOGIN_URL);
    });

    it('does not double-report when Enter implicitly submits the form', () => {
      createPage();
      openModal();

      // A browser submits an input form on Enter by dispatching keydown and
      // then a native submit. The dialog must answer only the submit.
      usernameInput().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      submitForm();

      expect(TestBed.inject(NotificationService).notifications().length).toBe(1);
      http.expectNone(LOGIN_URL);
    });

    it('rejects a password shorter than 8 characters', () => {
      createPage();
      openModal();
      fillCredentials('ivan', 'short');
      submitForm();

      expect(notificationText()).toBe('Пароль должен быть не короче 8 символов');
      http.expectNone(LOGIN_URL);
    });

    it('rejects a username longer than 32 characters', () => {
      createPage();
      openModal();
      fillCredentials('i'.repeat(33), 'password123');
      submitForm();

      expect(notificationText()).toBe('Логин не может быть длиннее 32 символов');
      http.expectNone(LOGIN_URL);
    });

    it('rejects a password longer than 128 characters', () => {
      createPage();
      openModal();
      fillCredentials('ivan', 'p'.repeat(129));
      submitForm();

      expect(notificationText()).toBe('Пароль не может быть длиннее 128 символов');
      http.expectNone(LOGIN_URL);
    });
  });
});
