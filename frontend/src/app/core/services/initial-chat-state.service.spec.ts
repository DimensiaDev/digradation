import { TestBed } from '@angular/core/testing';

import { InitialChatStateService } from './initial-chat-state.service';

describe('InitialChatStateService', () => {
  let service: InitialChatStateService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(InitialChatStateService);
  });

  it('returns the message for the target session and clears it', () => {
    const message = { text: 'из черновика', files: [] };
    service.set('s1', message);

    expect(service.consume('s1')).toBe(message);
    expect(service.consume('s1')).toBeNull();
  });

  it('drops a message that belongs to another session', () => {
    service.set('s1', { text: 'из черновика', files: [] });

    expect(service.consume('s2')).toBeNull();
    expect(service.consume('s1')).toBeNull();
  });

  it('returns null when nothing is pending', () => {
    expect(service.consume('s1')).toBeNull();
  });
});
