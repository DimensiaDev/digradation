import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { HttpInterceptorFn, provideHttpClient, withInterceptors } from '@angular/common/http';

import { routes } from './app.routes';
import { environment } from '../environments/environment';
import { errorInterceptor } from './core/interceptors/error.interceptor';
import { credentialsInterceptor } from './core/interceptors/credentials.interceptor';
import { mockApiInterceptor } from './core/interceptors/mock-api.interceptor';

export function createInterceptors(useMocks: boolean): HttpInterceptorFn[] {
  // Angular runs interceptors in array order, so the mock must be last. It answers
  // instead of calling next(), which means credentials and error handling only see
  // its responses when they wrap it.
  return useMocks
    ? [credentialsInterceptor, errorInterceptor, mockApiInterceptor]
    : [credentialsInterceptor, errorInterceptor];
}

const interceptors = createInterceptors(environment.useMocks);

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(withInterceptors(interceptors))
  ]
};
