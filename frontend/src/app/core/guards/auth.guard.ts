import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { catchError, map, of } from 'rxjs';
import { isAdminRole } from '../models/models';
import { ProfileService } from '../services/profile.service';

export const authGuard: CanActivateFn = () => {
  const profile = inject(ProfileService);
  const router = inject(Router);

  if (profile.user()) {
    return true;
  }

  return profile.fetchMe().pipe(
    map(() => true),
    catchError(() => of(router.createUrlTree(['/home'])))
  );
};

export const adminGuard: CanActivateFn = () => {
  const profile = inject(ProfileService);
  const router = inject(Router);

  const checkAdmin = () =>
    isAdminRole(profile.user()?.role) ? true : router.createUrlTree(['/home']);

  if (profile.user()) {
    return checkAdmin();
  }

  return profile.fetchMe().pipe(
    map(checkAdmin),
    catchError(() => of(router.createUrlTree(['/home'])))
  );
};
