import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { catchError, map, of } from 'rxjs';
import { isAdminRole } from '../models/models';
import { ProfileService } from '../services/profile.service';

export const adminGuard: CanActivateFn = () => {
  const profile = inject(ProfileService);
  const router = inject(Router);

  const resolve = () =>
    isAdminRole(profile.user()?.role) ? true : router.createUrlTree(['/home']);

  // `provideAppInitializer` normally resolves the session before the first
  // navigation, so this is a plain synchronous check. If the session is not
  // resolved yet (the initializer removed, made non-blocking, or a future
  // entry point), fetch it here rather than reading an unresolved `user()`
  // as a guest and silently bouncing everyone to /home.
  if (profile.sessionChecked() || profile.user()) {
    return resolve();
  }

  return profile.fetchMe().pipe(
    catchError(() => of(null)),
    map(resolve)
  );
};
