import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { App } from './app.component';
import { appConfig } from './app.config';
import { routes } from './app.routes';
import { SettingsPage } from './pages/settings-page/settings-page.component';

describe('App bootstrap pieces', () => {
  it('exposes the routes table and the providers list', () => {
    expect(routes.length).toBeGreaterThan(0);
    expect(appConfig.providers.length).toBeGreaterThan(0);
  });

  it('renders the placeholder settings page', () => {
    const fixture = TestBed.configureTestingModule({ imports: [SettingsPage] }).createComponent(
      SettingsPage
    );
    fixture.detectChanges();
    expect(fixture.debugElement.queryAll(By.css('*')).length).toBeGreaterThan(0);
  });

  it('creates the root component', () => {
    const fixture = TestBed.configureTestingModule({
      imports: [App],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()]
    }).createComponent(App);
    fixture.detectChanges();
    expect(fixture.componentInstance).toBeInstanceOf(App);
  });

  it('previews /programs for guests and keeps /programs/new behind the full gate', async () => {
    TestBed.configureTestingModule({
      providers: [provideRouter(routes), provideHttpClient(), provideHttpClientTesting()]
    });

    const harness = await RouterTestingHarness.create('/programs');

    expect(harness.routeNativeElement?.querySelector('.programs-page')).not.toBeNull();
    expect(harness.routeNativeElement?.querySelector('.auth-gate__overlay')).not.toBeNull();

    await harness.navigateByUrl('/programs/new');

    expect(harness.routeNativeElement?.querySelector('.programs-page')).toBeNull();
    expect(harness.routeNativeElement?.querySelector('.auth-gate__overlay')).toBeNull();
    expect(harness.routeNativeElement?.querySelector('.auth-gate__prompt')).not.toBeNull();
  });
});
