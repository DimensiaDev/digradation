import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';

import { AuthModalService } from './auth-modal.service';

@Component({ selector: 'app-stub', template: '' })
class StubComponent {}

describe('AuthModalService', () => {
  let service: AuthModalService;
  let router: Router;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideRouter([{ path: 'chats/:id', component: StubComponent }])]
    });
    service = TestBed.inject(AuthModalService);
    router = TestBed.inject(Router);
  });

  it('is closed initially', () => {
    expect(service.isOpen()).toBe(false);
  });

  it('opens on the current url by default', async () => {
    await router.navigateByUrl('/chats/42');

    service.open();

    expect(service.isOpen()).toBe(true);
    expect(service.returnUrl()).toBe('/chats/42');
  });

  it('keeps an explicit return url', () => {
    service.open('/settings');

    expect(service.returnUrl()).toBe('/settings');
  });

  it('falls back to /home for external urls', () => {
    service.open('https://example.com');
    expect(service.returnUrl()).toBe('/home');

    service.open('//evil.example.com');
    expect(service.returnUrl()).toBe('/home');
  });

  it('closes', () => {
    service.open();
    service.close();

    expect(service.isOpen()).toBe(false);
  });
});
