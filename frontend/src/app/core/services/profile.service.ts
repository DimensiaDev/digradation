import { DestroyRef, Injectable, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { HttpContext, HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, catchError, finalize, of, share, tap } from 'rxjs';
import { environment } from '../../../environments/environment';
import { S3AvatarUrl, User } from '../models/models';
import { SKIP_AUTH_ERROR_HANDLING } from '../interceptors/skip-auth-error-handling';
import { NotificationService } from './notification.service';
import { SessionEventsService } from './session-events.service';
import { extractApiErrorMessage } from '../../shared/utils/api-error';

@Injectable({
  providedIn: 'root'
})
export class ProfileService {
  private readonly http = inject(HttpClient);
  private readonly notifications = inject(NotificationService);
  private readonly sessionEvents = inject(SessionEventsService);
  private readonly destroyRef = inject(DestroyRef);

  constructor() {
    // The interceptor reports an unauthorized response as an event instead of
    // reaching into this service, which keeps the `ProfileService -> HttpClient
    // -> interceptor -> ProfileService` loop out of the graph.
    this.sessionEvents.unauthorized$
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.clear());
  }

  private readonly userState = signal<User | null>(null);
  private readonly avatarUrlState = signal<string | null>(null);
  private readonly avatarLoadingState = signal(true);
  private readonly sessionCheckedState = signal(false);

  readonly user = this.userState.asReadonly();
  readonly avatarUrl = this.avatarUrlState.asReadonly();
  readonly avatarLoading = this.avatarLoadingState.asReadonly();
  readonly sessionChecked = this.sessionCheckedState.asReadonly();

  private meRequest$: Observable<User> | null = null;
  private requestGeneration = 0;

  ensureSession(): Observable<User | null> {
    return this.fetchMe().pipe(
      catchError((error: unknown) => {
        // A 401 is the expected "no session" answer and stays silent. Anything
        // else means the session could not be verified, so the guest shell must
        // not be presented as if it were the truth: say so instead of degrading
        // silently.
        if (!(error instanceof HttpErrorResponse) || error.status !== 401) {
          const description =
            error instanceof HttpErrorResponse
              ? `${extractApiErrorMessage(error)}. Обновите страницу.`
              : 'Попробуйте обновить страницу.';
          this.notifications.errorOnce('Не удалось проверить сессию', { description });
        }
        return of(null);
      }),
      tap(() => this.sessionCheckedState.set(true))
    );
  }

  fetchMe(): Observable<User> {
    if (this.meRequest$) {
      return this.meRequest$;
    }

    const generation = this.requestGeneration;
    const context = new HttpContext().set(SKIP_AUTH_ERROR_HANDLING, true);
    const request = this.http.get<User>(`${environment.apiUrl}/users/me`, { context }).pipe(
      tap((user) => {
        if (generation === this.requestGeneration) {
          this.userState.set(user);
        }
      }),
      finalize(() => {
        if (this.meRequest$ === request) {
          this.meRequest$ = null;
        }
      }),
      share()
    );
    this.meRequest$ = request;
    return request;
  }

  /**
   * Re-read the profile for a session that has just changed.
   *
   * `fetchMe` hands back a request that is already in flight, which is what keeps
   * two callers from asking twice, and that reuse is wrong the moment the session
   * identity changes: the request in flight was issued without the new cookie, so
   * joining it answers 401 and the caller's own `catchError` turns a successful
   * sign-in into one that never reached the app. Retiring the request here does
   * for a sign-in what `clear` does for a sign-out, and the read that follows
   * carries the new session.
   */
  refresh(): Observable<User> {
    this.requestGeneration++;
    this.meRequest$ = null;
    return this.fetchMe();
  }

  loadAvatar(): void {
    if (!this.userState()) {
      this.avatarLoadingState.set(false);
      return;
    }

    const context = new HttpContext().set(SKIP_AUTH_ERROR_HANDLING, true);
    this.http
      .get<S3AvatarUrl>(`${environment.apiUrl}/users/me/avatar`, { context })
      .pipe(finalize(() => this.avatarLoadingState.set(false)))
      .subscribe({
        next: (response) => this.avatarUrlState.set(response.avatar_url),
        error: () => this.avatarUrlState.set(null)
      });
  }

  resetAvatar(): void {
    this.avatarUrlState.set(null);
  }

  clear(): void {
    this.requestGeneration++;
    this.meRequest$ = null;
    this.userState.set(null);
    this.avatarUrlState.set(null);
    this.avatarLoadingState.set(true);
    this.sessionCheckedState.set(true);
  }
}
