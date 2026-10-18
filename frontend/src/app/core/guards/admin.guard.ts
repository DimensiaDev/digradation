import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { isAdminRole } from '../models/models';
import { ProfileService } from '../services/profile.service';

export const adminGuard: CanActivateFn = () => {
  const profile = inject(ProfileService);
  const router = inject(Router);

  return isAdminRole(profile.user()?.role) ? true : router.createUrlTree(['/home']);
};
