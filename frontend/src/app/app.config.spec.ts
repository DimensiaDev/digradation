import { vi } from 'vitest';

import { credentialsInterceptor } from './core/interceptors/credentials.interceptor';
import { errorInterceptor } from './core/interceptors/error.interceptor';
import { mockApiInterceptor } from './core/interceptors/mock-api.interceptor';
import { applyRouteTransition, createInterceptors } from './app.config';

describe('createInterceptors', () => {
  it('puts the mock innermost so credentials and error handling wrap it', () => {
    expect(createInterceptors(true)).toEqual([
      credentialsInterceptor,
      errorInterceptor,
      mockApiInterceptor
    ]);
  });

  it('does not include the mock outside mock mode', () => {
    expect(createInterceptors(false)).toEqual([credentialsInterceptor, errorInterceptor]);
  });
});

describe('applyRouteTransition', () => {
  const stubTransition = () => {
    let finish!: () => void;
    const transition = {
      skipTransition: vi.fn(),
      finished: new Promise<void>((resolve) => {
        finish = resolve;
      })
    } as unknown as ViewTransition;
    return { transition, finish: () => finish() };
  };

  const tick = () => new Promise((resolve) => setTimeout(resolve));

  it('skips when only the query string changes', () => {
    const { transition } = stubTransition();
    const root = document.createElement('div');

    applyRouteTransition(transition, '/chats?page=1', '/chats?page=2', root);

    expect(transition.skipTransition).toHaveBeenCalled();
    expect(root.dataset['navDirection']).toBeUndefined();
  });

  it('marks the section direction and clears it once the transition finishes', async () => {
    const { transition, finish } = stubTransition();
    const root = document.createElement('div');

    applyRouteTransition(transition, '/home', '/settings', root);
    expect(root.dataset['navDirection']).toBe('forward');

    finish();
    await tick();
    expect(root.dataset['navDirection']).toBeUndefined();
  });

  it('marks a transition between unranked sections as neutral', () => {
    const { transition } = stubTransition();
    const root = document.createElement('div');

    applyRouteTransition(transition, '/create', '/chats', root);

    expect(transition.skipTransition).not.toHaveBeenCalled();
    expect(root.dataset['navDirection']).toBe('none');
  });

  it('leaves a newer direction in place when an older transition finishes', async () => {
    const { transition, finish } = stubTransition();
    const root = document.createElement('div');

    applyRouteTransition(transition, '/home', '/settings', root);
    root.dataset['navDirection'] = 'backward';

    finish();
    await tick();
    expect(root.dataset['navDirection']).toBe('backward');
  });
});
