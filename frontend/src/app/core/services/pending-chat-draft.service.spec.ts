import { TestBed } from '@angular/core/testing';

import { PendingChatDraftService } from './pending-chat-draft.service';

function makeFile(name: string, size: number): File {
  return new File(['x'.repeat(size)], name, { type: 'application/octet-stream' });
}

describe('PendingChatDraftService', () => {
  let service: PendingChatDraftService;

  beforeEach(() => {
    service = TestBed.inject(PendingChatDraftService);
  });

  it('keeps the draft until it is consumed', () => {
    const draft = { text: 'Нужна программа', files: [makeFile('план.pdf', 1024)] };

    service.set(draft);

    expect(service.peek()).toEqual(draft);
    expect(service.consume()).toEqual(draft);
    expect(service.peek()).toBeNull();
  });

  it('returns null when there is nothing to consume', () => {
    expect(service.consume()).toBeNull();
  });

  it('replaces an earlier draft', () => {
    service.set({ text: 'Первый', files: [] });
    service.set({ text: 'Второй', files: [] });

    expect(service.peek()?.text).toBe('Второй');
  });

  it('clears the draft when the login flow is abandoned', () => {
    service.set({ text: 'Нужна программа', files: [] });

    service.clear();

    expect(service.peek()).toBeNull();
    expect(service.consume()).toBeNull();
  });
});
