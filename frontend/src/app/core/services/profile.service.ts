import { Injectable, inject, signal } from '@angular/core';
import { HttpContext, HttpClient } from '@angular/common/http';
import { Observable, catchError, finalize, of, share, tap } from 'rxjs';
import { environment } from '../../../environments/environment';
import { S3AvatarUrl, User } from '../models/models';
import { SKIP_AUTH_ERROR_HANDLING } from '../interceptors/skip-auth-error-handling';

@Injectable({
  providedIn: 'root'
})
export class ProfileService {
  private readonly http = inject(HttpClient);

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
      catchError(() => of(null)),
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
