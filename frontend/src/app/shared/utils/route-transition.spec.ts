import { vi } from 'vitest';

import {
  sectionIndex,
  sectionTransitionDirection,
  shouldSkipRouteTransition
} from './route-transition';

describe('route-transition', () => {
  it('maps urls to their sidebar position, longest route first', () => {
    expect(sectionIndex('/home')).toBe(0);
    expect(sectionIndex('/library')).toBe(2);
    expect(sectionIndex('/settings')).toBe(3);
    expect(sectionIndex('/admin/users')).toBe(5);
    expect(sectionIndex('/admin/settings')).toBe(7);
  });

  it('strips query strings and fragments and matches child paths', () => {
    expect(sectionIndex('/settings?tab=profile')).toBe(3);
    expect(sectionIndex('/chats/42')).toBe(-1);
    expect(sectionIndex('/profile')).toBe(-1);
    expect(sectionIndex('/')).toBe(-1);
  });

  it('reports forward, backward and neutral directions', () => {
    expect(sectionTransitionDirection('/home', '/settings')).toBe('forward');
    expect(sectionTransitionDirection('/library', '/home')).toBe('backward');
    expect(sectionTransitionDirection('/home', '/home')).toBe('none');
    expect(sectionTransitionDirection('/profile', '/chats/42')).toBe('none');
    expect(sectionTransitionDirection('/home?x=1', '/settings')).toBe('forward');
  });

  it('skips a transition when only the query string changes', () => {
    expect(shouldSkipRouteTransition('/programs?page=1', '/programs?page=2')).toBe(true);
    expect(shouldSkipRouteTransition('/programs', '/home')).toBe(false);
  });

  it('skips a transition when the user prefers reduced motion', () => {
    // jsdom does not implement matchMedia, so the stub is installed and
    // removed by vitest itself.
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query === '(prefers-reduced-motion: reduce)'
    }));

    try {
      expect(shouldSkipRouteTransition('/home', '/settings')).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
