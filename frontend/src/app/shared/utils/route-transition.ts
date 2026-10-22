import {
  ADMIN_NAV_ITEMS,
  USER_BOTTOM_NAV_ITEMS,
  USER_NAV_ITEMS
} from '../navigation/navigation.config';

export type SectionDirection = 'forward' | 'backward' | 'none';

// Sidebar order, top to bottom: moving to a lower entry plays "forward".
// Sections reachable outside the sidebar (the chat workspace and the profile)
// are absent on purpose, so their transitions fall back to the neutral fade.
const SECTION_ORDER: readonly string[] = [
  ...USER_NAV_ITEMS.map((item) => item.route),
  ...USER_BOTTOM_NAV_ITEMS.map((item) => item.route),
  ...ADMIN_NAV_ITEMS.map((item) => item.route)
];

function pathOf(url: string): string {
  return url.split(/[?#]/, 1)[0];
}

export function sectionIndex(url: string): number {
  const path = pathOf(url);
  let best = -1;
  SECTION_ORDER.forEach((route, index) => {
    const matches = path === route || path.startsWith(`${route}/`);
    if (matches && (best === -1 || route.length > SECTION_ORDER[best].length)) {
      best = index;
    }
  });
  return best;
}

export function sectionTransitionDirection(fromUrl: string, toUrl: string): SectionDirection {
  const from = sectionIndex(fromUrl);
  const to = sectionIndex(toUrl);
  if (from === -1 || to === -1 || from === to) {
    return 'none';
  }
  return to > from ? 'forward' : 'backward';
}

export function shouldSkipRouteTransition(fromUrl: string, toUrl: string): boolean {
  if (pathOf(fromUrl) === pathOf(toUrl)) {
    return true;
  }
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}
