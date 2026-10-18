import { Injectable } from '@angular/core';
import { Observable, Subject } from 'rxjs';

/**
 * Carries the fact that the backend rejected a request as unauthorized from
 * where it is observed (the HTTP error interceptor) to where the session is
 * owned (`ProfileService`).
 *
 * Deliberately dependency-free: an interceptor that injected `ProfileService`
 * would close the loop `ProfileService -> HttpClient -> interceptor ->
 * ProfileService`, which only resolves because the interceptor's inject is
 * lazy. This service breaks that loop and keeps the interceptor out of the
 * session state.
 */
@Injectable({ providedIn: 'root' })
export class SessionEventsService {
  private readonly unauthorizedSubject = new Subject<void>();

  readonly unauthorized$: Observable<void> = this.unauthorizedSubject.asObservable();

  notifyUnauthorized(): void {
    this.unauthorizedSubject.next();
  }
}
