import { credentialsInterceptor } from './core/interceptors/credentials.interceptor';
import { errorInterceptor } from './core/interceptors/error.interceptor';
import { mockApiInterceptor } from './core/interceptors/mock-api.interceptor';
import { createInterceptors } from './app.config';

describe('createInterceptors', () => {
  it('puts the mock innermost so credentials and error handling wrap it', () => {
    expect(createInterceptors(true)).toEqual([
      credentialsInterceptor,
      errorInterceptor,
      mockApiInterceptor
    ]);
  });

  it('does not include the mock outside mock mode', () => {
    expect(createInterceptors(false)).toEqual([credentialsInterceptor, errorInterceptor]);
  });
});
