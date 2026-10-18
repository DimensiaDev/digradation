import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { NotificationService } from './notification.service';

describe('NotificationService', () => {
  let service: NotificationService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(NotificationService);
  });

  it('starts with an empty list', () => {
    expect(service.notifications()).toEqual([]);
  });

  it('adds a success notification', () => {
    service.success('Файл загружен');

    const list = service.notifications();
    expect(list.length).toBe(1);
    expect(list[0].type).toBe('success');
    expect(list[0].message).toBe('Файл загружен');
    expect(list[0].id).toBeTruthy();
  });

  it('appends an info and a warning notification', () => {
    service.info('Ответ готов');
    service.warning('Проверьте размер');

    const list = service.notifications();
    expect(list.length).toBe(2);
    expect(list[0].type).toBe('info');
    expect(list[1].type).toBe('warning');
    expect(list[1].message).toBe('Проверьте размер');
  });

  it('stacks new notifications instead of replacing the current one', () => {
    service.success('Первый');
    const firstId = service.notifications()[0].id;

    service.error('Второй');

    expect(service.notifications().length).toBe(2);
    expect(service.notifications()[1].message).toBe('Второй');
    expect(service.notifications()[1].id).not.toBe(firstId);
  });

  it('removes a notification by id', () => {
    service.success('Файл загружен');
    const id = service.notifications()[0].id;

    service.remove(id);

    expect(service.notifications()).toEqual([]);
  });

  it('keeps the notification when the id is unknown', () => {
    service.success('Файл загружен');

    service.remove('missing-id');

    expect(service.notifications().length).toBe(1);
  });

  describe('errorOnce', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('emits the error only once inside the dedupe window', () => {
      service.errorOnce('Ошибка сервера');
      service.errorOnce('Ошибка сервера');
      service.errorOnce('Ошибка сервера');

      expect(service.notifications().length).toBe(1);
    });

    it('emits the error again after the window has passed', () => {
      service.errorOnce('Ошибка сервера');
      vi.advanceTimersByTime(3000);
      service.errorOnce('Ошибка сервера');

      const list = service.notifications();
      expect(list.length).toBe(2);
      expect(list[1].message).toBe('Ошибка сервера');
    });

    it('never dedupes a different message', () => {
      service.errorOnce('Ошибка сервера');
      service.errorOnce('Проверьте поля');

      const list = service.notifications();
      expect(list.length).toBe(2);
      expect(list[1].message).toBe('Проверьте поля');
    });

    it('respects a custom dedupe window', () => {
      service.errorOnce('Ошибка сервера', { windowMs: 1000 });
      vi.advanceTimersByTime(1500);
      service.errorOnce('Ошибка сервера', { windowMs: 1000 });

      expect(service.notifications().length).toBe(2);
    });
  });
});
