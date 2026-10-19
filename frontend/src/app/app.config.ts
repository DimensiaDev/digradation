import {
  ApplicationConfig,
  inject,
  provideAppInitializer,
  provideBrowserGlobalErrorListeners
} from '@angular/core';
import { DOCUMENT } from '@angular/common';
import {
  Router,
  provideRouter,
  withComponentInputBinding,
  withViewTransitions,
  type ViewTransitionInfo
} from '@angular/router';
import { HttpInterceptorFn, provideHttpClient, withInterceptors } from '@angular/common/http';

import { routes } from './app.routes';
import { environment } from '../environments/environment';
import { errorInterceptor } from './core/interceptors/error.interceptor';
import { credentialsInterceptor } from './core/interceptors/credentials.interceptor';
import { mockApiInterceptor } from './core/interceptors/mock-api.interceptor';
import { ProfileService } from './core/services/profile.service';
import {
  sectionTransitionDirection,
  shouldSkipRouteTransition
} from './shared/utils/route-transition';

export function createInterceptors(useMocks: boolean): HttpInterceptorFn[] {
  // Angular runs interceptors in array order, so the mock must be last. It answers
  // instead of calling next(), which means credentials and error handling only see
  // its responses when they wrap it.
  return useMocks
    ? [credentialsInterceptor, errorInterceptor, mockApiInterceptor]
    : [credentialsInterceptor, errorInterceptor];
}

const interceptors = createInterceptors(environment.useMocks);

// Runs before the router swaps the DOM, so the direction is set in time for the
// view-transition rules in styles.scss. `router.url` is still the old url here:
// the navigation ends only after the swap.
export function onRouteViewTransition(info: ViewTransitionInfo): void {
  const router = inject(Router);
  const document = inject(DOCUMENT);
  const fromUrl = router.url;
  const toUrl = router.getCurrentNavigation()?.finalUrl?.toString() ?? '';

  applyRouteTransition(info.transition, fromUrl, toUrl, document.documentElement);
}

export function applyRouteTransition(
  transition: ViewTransition,
  fromUrl: string,
  toUrl: string,
  root: HTMLElement
): void {
  if (shouldSkipRouteTransition(fromUrl, toUrl)) {
    transition.skipTransition();
    return;
  }

  const direction = sectionTransitionDirection(fromUrl, toUrl);
  root.dataset['navDirection'] = direction;

  transition.finished
    .catch(() => undefined)
    .finally(() => {
      // A second navigation can start before this one finishes; only clear its
      // own direction.
      if (root.dataset['navDirection'] === direction) {
        delete root.dataset['navDirection'];
      }
    });
}

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideAppInitializer(() => inject(ProfileService).ensureSession()),
    provideRouter(
      routes,
      withComponentInputBinding(),
      withViewTransitions({
        skipInitialTransition: true,
        onViewTransitionCreated: onRouteViewTransition
      })
    ),
    provideHttpClient(withInterceptors(interceptors))
  ]
};
