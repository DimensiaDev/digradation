import { HttpInterceptorFn } from '@angular/common/http';

/**
 * The production stand-in for the mock API — see `fileReplacements` in `angular.json`.
 *
 * It exists to keep the demo credentials (`admin` / `admin123`) out of the shipped bundle. The
 * obvious fix, loading the mock through a dynamic `import()` under `environment.useMocks`, does
 * not do that: `useMocks` is a property read the bundler cannot fold away, so the import site
 * survives and the mock is emitted as a lazily-servable chunk that still carries the strings.
 * Excluding the module from the build is what actually removes them.
 *
 * `createInterceptors` never reaches this: `useMocks` is false in this build. Rather than pretend
 * to mock, it passes the request on, so a production build misconfigured to enable mocks fails
 * visibly against the real API instead of answering from memory.
 */
export const mockApiInterceptor: HttpInterceptorFn = (req, next) => next(req);
