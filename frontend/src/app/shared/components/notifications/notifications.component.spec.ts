import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { vi } from 'vitest';

import { NotificationsComponent } from './notifications.component';
import { NotificationService } from '../../../core/services/notification.service';

const NOTIFICATION_DURATION_MS = 5000;
const LEAVE_ANIMATION_MS = 220;

describe('NotificationsComponent', () => {
  let fixture: ComponentFixture<NotificationsComponent>;
  let component: NotificationsComponent;
  let service: NotificationService;

  const notifications = () => fixture.debugElement.queryAll(By.css('app-notification'));
  const titles = () =>
    fixture.debugElement
      .queryAll(By.css('.notification__title'))
      .map((el) => el.nativeElement.textContent as string);
  const closeButtons = () => fixture.debugElement.queryAll(By.css('.notification__close'));
  const host = (index = 0) => notifications()[index].nativeElement as HTMLElement;

  const refresh = (): void => fixture.detectChanges();

  /** Only setTimeout is faked so the Angular scheduler keeps using real timers. */
  const useFakeTimers = (): void => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [NotificationsComponent] }).compileComponents();

    service = TestBed.inject(NotificationService);
    fixture = TestBed.createComponent(NotificationsComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    await fixture.whenStable();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders an empty list when there are no notifications', () => {
    expect(fixture.debugElement.query(By.css('ul.notifications'))).not.toBeNull();
    expect(notifications()).toHaveLength(0);
  });

  it('renders the message and the type of the notification pushed by the service', () => {
    service.error('Не удалось создать чат: Ошибка сервера');
    refresh();

    expect(titles()).toEqual(['Не удалось создать чат: Ошибка сервера']);
    expect(host().getAttribute('data-type')).toBe('error');
    expect(host().getAttribute('role')).toBe('status');
    expect(host().getAttribute('aria-live')).toBe('polite');
  });

  it('renders a success notification with its own type', () => {
    service.success('Новый чат создан');
    refresh();

    expect(titles()).toEqual(['Новый чат создан']);
    expect(host().getAttribute('data-type')).toBe('success');
    expect(host().classList).not.toContain('notification--leaving');
  });

  it('stacks several notifications at once', () => {
    service.info('Первое');
    service.error('Второе');
    refresh();

    expect(titles()).toEqual(['Первое', 'Второе']);
    expect(notifications()).toHaveLength(2);
  });

  it('marks the clicked notification as leaving', () => {
    useFakeTimers();
    service.error('Заполните логин и пароль');
    refresh();

    closeButtons()[0].nativeElement.click();
    refresh();

    expect(component.isLeaving(service.notifications()[0].id)).toBe(true);
    expect(host().classList).toContain('notification--leaving');
    expect(service.notifications()).toHaveLength(1);
  });

  it('removes the notification once the leave animation ends', () => {
    useFakeTimers();
    service.error('Заполните логин и пароль');
    refresh();

    closeButtons()[0].nativeElement.click();
    vi.advanceTimersByTime(LEAVE_ANIMATION_MS);
    refresh();

    expect(service.notifications()).toHaveLength(0);
    expect(notifications()).toHaveLength(0);
  });

  it('only animates out the clicked notification, keeping the rest', () => {
    useFakeTimers();
    service.info('Первое');
    service.error('Второе');
    refresh();

    closeButtons()[0].nativeElement.click();
    refresh();

    expect(component.isLeaving(service.notifications()[0].id)).toBe(true);
    expect(component.isLeaving(service.notifications()[1].id)).toBe(false);
    expect(notifications()[1].nativeElement.classList).not.toContain('notification--leaving');
  });

  it('hides the notification automatically after five seconds', () => {
    useFakeTimers();
    service.info('Файл загружен');
    refresh();

    vi.advanceTimersByTime(NOTIFICATION_DURATION_MS - 1);
    refresh();
    expect(service.notifications()).toHaveLength(1);

    vi.advanceTimersByTime(1);
    refresh();
    expect(component.isLeaving(service.notifications()[0].id)).toBe(true);

    vi.advanceTimersByTime(LEAVE_ANIMATION_MS);
    refresh();
    expect(notifications()).toHaveLength(0);
  });

  it('keeps a persistent notification on screen past the auto-dismiss window', () => {
    useFakeTimers();
    service.info('Постоянное', { persistent: true });
    refresh();

    vi.advanceTimersByTime(NOTIFICATION_DURATION_MS * 2);
    refresh();

    expect(service.notifications()).toHaveLength(1);
    expect(titles()).toEqual(['Постоянное']);
  });
});
