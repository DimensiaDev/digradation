import { HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, throwError } from 'rxjs';
import { NotificationService } from '../services/notification.service';
import { ProfileService } from '../services/profile.service';
import { extractApiErrorMessage } from '../../shared/utils/api-error';
import { SKIP_AUTH_ERROR_HANDLING } from './skip-auth-error-handling';

export const errorInterceptor: HttpInterceptorFn = (req, next) => {
  const notifications = inject(NotificationService);
  const profile = inject(ProfileService);

  return next(req).pipe(
    catchError((error: HttpErrorResponse) => {
      if (req.url.endsWith('/auth/login') || req.context.get(SKIP_AUTH_ERROR_HANDLING)) {
        return throwError(() => error);
      }

      const message = extractApiErrorMessage(error);

      if (error.status === 401) {
        profile.clear();
        notifications.errorOnce('Вы не авторизованы', {
          description: 'Войдите в систему, чтобы продолжить.'
        });
        return throwError(() => error);
      }

      if (error.status >= 500) {
        notifications.errorOnce('Ошибка сервера', {
          description: 'Попробуйте повторить действие позже.'
        });
      } else {
        notifications.errorOnce(message, { description: 'Попробуйте повторить действие.' });
      }

      return throwError(() => error);
    })
  );
};
