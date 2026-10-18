import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Component } from '@angular/core';
import { By } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter, Router, Routes } from '@angular/router';

import { Sidebar } from './sidebar.component';
import { USER_BOTTOM_NAV_ITEMS, USER_NAV_ITEMS, ADMIN_NAV_ITEMS } from './sidebar.config';
import { environment } from '../../../../environments/environment';

@Component({
  selector: 'app-sidebar-test-host',
  imports: [Sidebar],
  template: `<app-sidebar [navItems]="items" [bottomNavItems]="bottomItems" [role]="role" />`
})
class TestHost {
  items = USER_NAV_ITEMS;
  bottomItems = USER_BOTTOM_NAV_ITEMS;
  role: 'user' | 'admin' = 'user';
}

const TEST_ROUTES: Routes = [
  { path: 'create', component: TestHost },
  { path: 'chats', component: TestHost },
  { path: 'chats/:id', component: TestHost },
  { path: 'settings', component: TestHost },
  { path: 'profile', component: TestHost }
];

describe('Sidebar accessibility', () => {
  let fixture: ComponentFixture<TestHost>;
  let router: Router;
  let http: HttpTestingController;

  const getNavLinks = () =>
    fixture.debugElement.queryAll(By.css('nav.navigation .navigation__item'));
  const getBottomNavLinks = () =>
    fixture.debugElement.queryAll(By.css('nav.sidebar__bottom .navigation__item'));

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideRouter(TEST_ROUTES), provideHttpClient(), provideHttpClientTesting()]
    }).compileComponents();

    fixture = TestBed.createComponent(TestHost);
    router = TestBed.inject(Router);
    http = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
  });

  afterEach(() => {
    http
      .match(`${environment.apiUrl}/users/me/avatar`)
      .forEach((request) => request.flush({ avatar_url: null }));
    http.verify();
  });

  it('exposes the navigation landmark with an accessible name', () => {
    const nav = fixture.debugElement.query(By.css('nav.navigation'));
    expect(nav.nativeElement.getAttribute('aria-label')).toBe('Основная навигация');
  });

  it('gives every nav link an accessible name from its label text', () => {
    const links = getNavLinks();
    expect(links.length).toBe(USER_NAV_ITEMS.length);
    links.forEach((link, i) => {
      expect(link.nativeElement.textContent).toContain(USER_NAV_ITEMS[i].label);
    });
  });

  it('renders the secondary nav items under a separate landmark', () => {
    const nav = fixture.debugElement.query(By.css('nav.sidebar__bottom'));
    expect(nav.nativeElement.getAttribute('aria-label')).toBe('Дополнительно');

    const links = getBottomNavLinks();
    expect(links.length).toBe(USER_BOTTOM_NAV_ITEMS.length);
    links.forEach((link, i) => {
      expect(link.nativeElement.textContent).toContain(USER_BOTTOM_NAV_ITEMS[i].label);
    });
  });

  it('marks the active route with aria-current="page"', async () => {
    await router.navigateByUrl('/settings');
    fixture.detectChanges();

    const current = getBottomNavLinks().find((link) =>
      link.nativeElement.getAttribute('aria-current')
    );
    expect(current?.nativeElement.textContent).toContain('Настройки');
    expect(current?.nativeElement.getAttribute('aria-current')).toBe('page');

    const allLinks = fixture.debugElement.queryAll(By.css('.navigation__item'));
    const active = allLinks.filter((l) => l.nativeElement.hasAttribute('aria-current'));
    expect(active.length).toBe(1);
  });

  it('does not mark any link active when on an unrelated route', async () => {
    await router.navigateByUrl('/create');
    fixture.detectChanges();

    const allLinks = fixture.debugElement.queryAll(By.css('.navigation__item'));
    expect(allLinks.filter((l) => l.nativeElement.hasAttribute('aria-current')).length).toBe(0);
  });

  it('renders admin nav items when the role is admin', () => {
    const adminFixture = TestBed.createComponent(TestHost);
    adminFixture.componentInstance.items = ADMIN_NAV_ITEMS;
    adminFixture.componentInstance.bottomItems = [];
    adminFixture.componentInstance.role = 'admin';
    adminFixture.detectChanges();

    const labels = adminFixture.debugElement
      .queryAll(By.css('.navigation__item'))
      .map((l) => l.nativeElement.textContent.trim());
    expect(labels).toEqual(ADMIN_NAV_ITEMS.map((item) => item.label));
  });
});

describe('Sidebar tab navigation', () => {
  let fixture: ComponentFixture<TestHost>;
  let router: Router;
  let http: HttpTestingController;

  const getNavLinks = () =>
    fixture.debugElement.queryAll(By.css('nav.navigation .navigation__item'));

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideRouter(TEST_ROUTES), provideHttpClient(), provideHttpClientTesting()]
    }).compileComponents();

    fixture = TestBed.createComponent(TestHost);
    router = TestBed.inject(Router);
    http = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
  });

  afterEach(() => {
    http
      .match(`${environment.apiUrl}/users/me/avatar`)
      .forEach((request) => request.flush({ avatar_url: null }));
    http.verify();
  });

  it('keeps every nav link in the natural tab order', async () => {
    await router.navigateByUrl('/settings');
    fixture.detectChanges();

    const links = getNavLinks();
    expect(links.length).toBe(USER_NAV_ITEMS.length);
    links.forEach((link) => {
      expect(link.nativeElement.getAttribute('tabindex')).toBeNull();
    });
  });

  it('renders the list without arrow-key widget semantics', () => {
    const list = fixture.debugElement.query(By.css('.navigation__list'));
    expect(list.nativeElement.getAttribute('tabindex')).toBeNull();
  });
});
