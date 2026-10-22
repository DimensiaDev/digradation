# Lessons

Traps, gotchas and non-obvious behaviours discovered the hard way.
Flat bullet list, append at the bottom. One bullet, one lesson.

---

## Environment and tooling

- In CI workflows use `poetry install --no-root`, not `poetry install --with dev`. The
  `dev` group is installed by default, and the FastAPI project root must not be installed
  into its own venv. Production images use `poetry install --only main --no-root`.
- The pre-commit Python hooks **cannot fail your commit**: `scripts/precommit-black.py` and
  `scripts/precommit-ruff.py` pass `check=False` to `subprocess.run`. A clean-looking hook
  run proves nothing, so always run `black --check` and `ruff check` explicitly.
- `.pre-commit-config.yaml` is the single source of truth for all hooks, but
  `.github/workflows/pre-commit.yml` only runs the _generic_ hooks (yaml, json, toml, eof,
  whitespace). Real lint and test coverage comes from `backend-ci.yml` and
  `processing-ci.yml`.
- `frontend-ci.yml` runs `ng lint` and `ng build` but **not** `ng test`. Vitest failures only
  ever surface locally, so run `npm --prefix frontend test` yourself.
- `pre-commit` hooks invoke a `python` executable. On systems where it is `python3` or `py`,
  the hook silently does nothing useful until you alias it.

## Docker and environment

- `docker-compose.override.yml` is **gitignored**. A fresh clone has no override, so Alembic
  cannot reach PostgreSQL because it is not published by default. Copy
  `docker-compose.override.yml.dev` to `docker-compose.override.yml`; that override publishes
  the DB on host port **5431**, which is what `backend/makemigrations.sh` expects.
- `backend/makemigrations.sh` starts the `db` service itself if it is not running, waits for
  its healthcheck, exports `POSTGRES_HOST=localhost POSTGRES_PORT=5431`, sources `.env`, and
  runs alembic. It **stops `db` on exit only if it started it** (`trap cleanup EXIT`). If it
  hangs, the healthcheck is failing: check `docker compose ps`.
- The app must run with CWD == `backend/`. `providers/sber.py` opens `res/gigachat-ca.cer` by
  **relative** path, so a different working directory raises at request time, or worse, only
  under the PERS-scope code path. `tasks/api.py` gets this right by anchoring on
  `Path(__file__).parent.parent.parent / "res/calc.xlsx"`.
- Service hostnames (`redis`, `rabbitmq`, `garage`, `taskiq_dashboard`) are hardcoded literals
  in the source. That is why the project cannot be run outside Compose; do not "improve" them
  into env vars without also changing the run model.
- Redis database **0 is general** (cache, locks, rate limits, statuses) and **1 is TaskIQ
  result storage only**. Writing task results to `/0` silently breaks every result read.

## Python and backend

- `app/providers/containers.py` builds a module-level `SberProvider(...)` **at import time**
  from `os.environ["SBER_API_KEY"]` and `os.environ["SBER_API_SCOPE"]`. Therefore _importing
  any route or task module_ requires both env vars to exist. This is why
  `tests/conftest.py` has an autouse `env` fixture monkeypatching them to mock values:
  without it, every test errors on import rather than on assertion.
- **Redis `.get()` and `db.execute(...).scalar()` return `Any` or untyped values**, so type
  narrowing needs `# type: ignore` or an explicit cast. Do not "clean up" these by adding
  asserts that change runtime behaviour.
- Model files start with `from __future__ import annotations` and use
  `if typing.TYPE_CHECKING:` for circular imports. Removing the future import makes
  SQLAlchemy's `Mapped[]` resolution fail on string annotations.
- `app/models/__init__.py` uses `pkgutil.iter_modules` plus `importlib.import_module` to
  auto-register every model. **Do not add manual imports**: a new model file in the same
  package is picked up automatically, and manual entries are how duplicates creep in.
- `alembic/versions/` is excluded from both black and ruff. Reformatting generated migrations
  produces a noisy diff on every regeneration and is explicitly not wanted.
- `alembic/env.py` reads the URL from `get_database_url()` and does `.replace("%", "%%")`. The
  doubling is required because `alembic.ini` uses `ConfigParser` interpolation, and a raw
  password containing `%` otherwise crashes the interpreter.
- `tests/test_migrations.py` runs `alembic upgrade head`, `downgrade base` **and
  `alembic check`** in a subprocess. It is a real drift gate: change a model, forget the
  migration, and the suite fails with the exact command to run. There is currently only one
  revision (`58cdf8dfcbba`, `down_revision=None`).
- Backend tests require a **Docker daemon** because `postgres_container` and
  `redis_container` are `testcontainers` fixtures scoped to the session. A test run without
  Docker fails at fixture setup, not at an assertion, so read the error before debugging code.
- `conftest.py` builds the schema with `Base.metadata.create_all`, **not** migrations. Only
  `test_migrations.py` exercises the Alembic path.
- The `httpx.AsyncClient` in tests carries cookies between requests, mirroring a browser. A
  test that logs in and then calls an admin route without re-logging in _will_ pass auth. This
  is intentional, but it means "logged out" assertions need a fresh client.
- `@pytest.mark.asyncio` is still written explicitly even though `asyncio_mode = "auto"`.
  Both work; match the surrounding file rather than "fixing" the inconsistency.

## The GigaChat harness

- **`MATERIALS` order is a data contract.** The `material` in every generated position is an
  **integer index** into that list. Inserting a grade in the middle silently reinterprets
  every already-persisted position. Append only, and note it in `DECISIONS.md`.
- **An empty `positions` list means "ask clarifying questions"**, not "generation failed". The
  prose in `message` is the question. Distinguishing the two in the UI, for example by showing
  an error, is a bug.
- The prompt-injection guard is a _model-level_ control in `SYSTEM_REMINDER`, not a parser
  check. It can be talked around, so do not describe it in docs as a security boundary.
- GigaChat PERS-scope calls take a **global Redis lock** (`api:global:lock`). Concurrent
  requests queue for up to 60 s and then raise, so a burst of generation requests looks like
  failures even though nothing is broken.

## Processing library (historical — the package was deleted on 2026-10-16)

> The `processing/` package (PDF rasteriser, DXF parser, Excel calculator) no longer exists.
> These lessons are kept for the history: if a template, a parser or a pricing behaviour is
> ever reintroduced, they are the traps that were already paid for once. `res/calc.xlsx` is
> gone too.

- **Two copies of `calc.xlsx` existed on purpose**: `backend/res/calc.xlsx` (read by
  `tasks/api.py`) and `processing/src/processing/calculator/res/calc.xlsx` (packaged with the
  library). Editing only one produced a mismatch between what the library shipped and what
  the backend rendered.
- `processing/tests/conftest.py` inserts `processing/src` into `sys.path` so the tests run
  **without installing the package**. A test that only passes after `poetry install` is
  testing something other than the library.
- The DXF parser is hand-rolled. New entity types need explicit handling in the group-code
  walk; adding `ezdxf` "just for this" would change the dependency profile and the error types
  (`DXFStructureError`) that callers already catch.
- `SUPPORTED_ENCODINGS = ["utf-8", "cp1251", "latin-1"]` reflects real customer files.
  Removing `cp1251` because it looks legacy breaks Russian-locale DXFs.
- **`float("1500,50")` raises, so a comma-decimal price is silently dropped.**
  `_load_material_prices` skips it like any other unparseable value, which means the material
  vanishes from the price map rather than being priced at zero. Worth knowing if a template
  ever arrives from a Russian-locale Excel.
- **`dxf_parser.py` can never reach 100% and that is not a testing failure.** The
  `UnicodeDecodeError` fallback after the encoding loop is unreachable because `latin-1`
  decodes any byte sequence, and the six `except ValueError, TypeError: continue` blocks
  around `_get_float` calls are unreachable because `_get_float` swallows those errors and
  returns `0.0`. Both verified by script. Do not write tests to chase them.
- **`get_measurements_data` and `extract_measurements` are separate walkers over the same
  entities**, with their own copies of every ARC and LWPOLYLINE branch. A test for one tells
  you nothing about the other, which is how `dxf_parser.py` sat at 77% while looking well
  covered.
- **A dangling group code at the end of a DXF is dropped, not stored empty.** The parser
  breaks before consuming the value, so `{10: []}` never appears; asserting on that shape
  will fail.

- **The global npm is newer than the pinned one and breaks installs two different ways.**
  `package.json` pins `packageManager: npm@10.9.2` but the machine has npm 12, which fails
  with `Cannot read properties of null (reading 'children')` (an arborist crash), and then,
  with an explicit version, with `EALLOWREMOTE` because it refuses the remote tarball
  dependency `xlsx@https://cdn.sheetjs.com/...`. **Use the pinned version:
  `npx --yes npm@10.9.2 install ...` from `frontend/`.** Do not "upgrade" the lockfile with
  a newer npm as a side effect.
- **A bare `npm install -D @vitest/coverage-v8` installs the wrong major version.** The
  registry's latest is the v5 line, which does not match the project's Vitest `4.1.11`.
  Pin it: `@vitest/coverage-v8@4.1.11`. The coverage provider and the runner must share a
  major version, and nothing warns you about the mismatch.
- **Angular's unit-test builder owns the Vitest config; do not add `vitest.config.ts`.**
  `@angular/build:unit-test` exposes `coverage`, `coverageReporters`, `coverageInclude`,
  `coverageExclude`, `coverageThresholds` and `coverageWatermarks` directly, and requires
  `@vitest/coverage-v8` by name. Settings belong in the `test` target of `angular.json`.
- **Coverage output nests under the project name**: `frontend/coverage/frontend/`, not
  `frontend/coverage/`. A CI upload path pointing at `coverage/coverage-summary.json` will
  find nothing. Already gitignored by `frontend/.gitignore`'s `/coverage`.
- **Omitting `coverageInclude` inflates the number.** V8 then reports only files a test
  actually imported, so untouched files vanish from the denominator and the figure reads far
  higher than the project is. The lower, explicit-include number is the honest one.
- **A failing suite suppresses the coverage report entirely.** A red suite makes
  `ng test --coverage` write an empty `coverage/` directory and print no summary, so the run
  looks like it measured nothing. To measure a red suite, exclude the broken spec with
  `--exclude "**/<name>.spec.ts"` and treat the result as provisional.
- **`sidebar.component.spec.ts` was red on `main`: the jsdom test environment has no
  `localStorage` at all.** The visible error was misleading. The reported failure was
  `Cannot configure the test module when the test module has already been instantiated`,
  but the real first error was
  `Cannot read properties of undefined (reading 'getItem')` thrown from
  `_ThemeService.restoreTheme` during **field initialization**; the 13 other errors were a
  cascade from the `afterEach` avatar flush hitting an unassigned `http`.
- **Diagnose from the FIRST error in the file, not the summary.** The `TestBed` message
  looked like a test-structure problem and would have sent you to `resetTestingModule()`. A
  throwaway probe spec printed `typeof window === 'object'`, `document.URL ===
'http://localhost:3000/'`, and `typeof localStorage === 'undefined'`, which located the
  real cause in one run. jsdom sets `window._localStorage` unconditionally in its
  constructor, so a _bare_ `new JSDOM(url)` returns a working `Storage`; the gap comes
  from how the environment is built for Vitest, not from jsdom.
- **Fix an environment gap in the environment, not in production code.** The shim lives in
  `src/test-setup.ts`, registered via the builder's `setupFiles` option, and only installs
  when storage is missing so it self-disables if a future upgrade fixes it. Guarding
  `ThemeService` with `typeof localStorage === 'undefined'` would have been the wrong fix:
  the app is not broken, and it would add defensive branches to every caller forever.
- **`Sidebar` is the only consumer of `localStorage` in the app**, which is why exactly one
  spec file failed and the other 19 passed. A failure isolated to a single spec is often a
  clue about which service it pulls in, not about that spec.
- **Verify a test fix with a mutation, not just green output.** Once a suite turns green, the
  assertions could have been vacuous. Renaming the history title in the sidebar template to
  `MUTANT` correctly failed a test, proving the spec really exercises the component. Reverted
  after checking.

## Frontend

- **Never hardcode a backend URL.** `environment.prod.ts` holds the placeholder
  `apiUrl: '__BACKEND_URL__/api/v1'`, rewritten at image build time by
  `docker/rewrite-env.mjs` from the `BACKEND_URL` build arg. A literal URL in a service works
  locally and breaks the moment the deployment moves.
- The build arg reaches the frontend through `docker-compose.yaml` (`args.BACKEND_URL`), and
  in dev through the bind-mounted `ng serve` in `docker-compose.override.yml.dev`. If dev and
  prod disagree about the API URL, check both.
- **`docx-preview`, `xlsx` and `hyperformula` are lazily `import()`ed.** Promoting any of them
  to a static top-level import bloats the eager bundle for every user, including those who
  never open a preview.
- The `.xlsx` preview evaluates formulas **across sheets** with HyperFormula, because the
  generated `kp.xlsx` prices rows using workbook formulas. Rendering cells without the formula
  engine shows blanks where prices should be.
- Component **class naming is inconsistent by history**: `CalculationChatComponent` versus
  `HistoryPage`, `Layout`, `Sidebar`, `Textarea`, `Select`. Files are always
  `<kebab-name>.component.ts`. Follow the file you are editing; do not mass-rename.
- Angular schematics have `skipTests: true`, so `ng generate component` **does not create a
  spec file**. If you add a component with logic, write the spec by hand.
- Component tests use two idioms: `HttpTestingController` for services, and hand-rolled fakes
  (`class FakeUploader`) plus inline `TestHost` components for components. Match the
  neighbouring spec rather than mixing the two in one file.
- ESLint lints `src/**/*.ts` and `src/**/*.html` but **not `.scss`**. Prettier formats styles,
  but there is no stylelint, so a bad SCSS rule will not be caught by any gate.
- The `app-` selector prefix is enforced by `@angular-eslint/component-selector` (kebab-case)
  and `directive-selector` (camelCase). Renaming a selector without updating the prefix fails
  `ng lint`.
- Colours must come from CSS custom properties; light and dark is `html[data-theme='dark']`
  plus `localStorage['theme']`. A hardcoded hex in a component is invisible to the theme
  toggle.
- **Explanatory comments in the code are strictly forbidden (typical "AI-style").** The only allowed type of comments
  in TS/HTML/SCSS is `// TODO: ...` for temporary notes and tasks. Self-documenting code and clear naming are prioritized over comments.

## Test infrastructure

- **Patching `app.cache.get_redis_client` does nothing for the workers.** The task modules
  do `from app.cache import get_redis_client`, which binds the name into their own
  namespace; `monkeypatch.setattr("app.cache.get_redis_client", ...)` leaves those bindings
  pointing at the real `redis://redis:6379`. Patch `app.cache.get_redis_pool` instead, so
  every call site is covered at once. The same trap applies to `tsq_db`, where all three
  task modules must be patched, not just `app.tasks.conf.broker`.
- **Worker tests must commit, not use the `db_session` fixture.** `db_session` wraps
  everything in a transaction it rolls back, and the worker's `tsq_db` opens a separate
  connection, so it cannot see pending rows at all. Tests that appeared to pass were
  silently asserting against an empty database until they committed through the engine.
- **`app.storage` and `app.providers.containers` read `os.environ` at import time**, so
  the test env must be set at the top of `conftest.py` _before_ any `app` import. An
  autouse fixture is too late: fixtures run after collection, and these modules import
  during it. This surfaced as `KeyError: S3_SECRET_KEY` at collection.
- **`get_redis_pool` and friends are `@cache`d, so tests that change the environment must
  call `cache_clear()`.** `get_debug` returned a stale value across every value of `DEBUG`
  until the cache was cleared, which looked like the truthiness logic being wrong.
- **`redis-py` 8 made `ConnectionPool.get_connection()` a coroutine.** `async with
pool.get_connection()` is a `TypeError`; it must be awaited.
- **A failing suite silently suppresses the coverage report.** With failures present,
  `pytest --cov` writes an empty `coverage/` directory and prints no summary, so a coverage
  number can look like "no change" when in fact nothing was measured.
- **Testcontainers' MinIO helper needs the `minio` python package**, which the project does
  not depend on. `DockerContainer(image, command=..., env=..., ports=[9000])` plus a retry
  loop around `create_bucket` achieves the same thing with no new dependency, and reports
  the real S3 error when MinIO never comes up.
- **`@broker.task` objects are not awaitable**; `task.original_func` is the underlying
  coroutine. `tests/helpers.run_task` wraps this so worker tests never touch RabbitMQ.
- **`RUF003` rejects Cyrillic in comments** but not in string literals, so refer to Russian
  sheet and material names without quoting them in a comment.

## End-to-end

- **A rate-limited backend makes an e2e suite trip its own limiter.** `chats:post` allows 5
  per minute per user for both chat creation and message sending. One run creates several
  chats as a single shared user, so the limit is reached _within_ the suite. The failure is
  far from the cause: the chat is created (202), the send is rejected (429), and the create
  page never navigates, so the assertion reports "expected URL to match /chats/.+".
  `workers: 1` plus clearing `ratelimit:*` in Redis before each test is the fix; parallel
  workers make per-test clearing useless because they trip each other.
- **`docker compose exec` does not forward host env vars** into the container. Seeding a
  user from a heredoc needs `docker compose exec -T -e NAME=value ...`.
- **Python inside the backend container needs `poetry run python`**, not bare `python`; the
  venv is not on the system path. This is the same reason `createsuperuser.sh` wraps it.
- **`import.meta` is unavailable in Playwright specs** here, because the config and specs
  are transpiled to CommonJS. Use `process.cwd()` or an env var for the repo root.
- **Icon-only buttons have no accessible name unless one is set.** The chat results panel
  toggles via `aria-label="Панель результатов расчёта"`, and guessing a visible-text locator
  for it fails. Prefer the aria-label in specs.
- **`app-input` renders both a hidden input and the textarea**, so
  `getByPlaceholder` resolves to two elements and trips strict mode. Target
  `textarea[placeholder="..."]` instead.
- **User-facing errors are toasts, not inline field errors.** A failed login raises a
  notification; there is no `.auth-error` element to assert on.
- **The admin user form is always visible**, not a dialog, and its submit button reads
  "Сохранить" in both create and edit mode.

## Frontend test environment

- **jsdom implements neither `window.matchMedia` nor `Element.prototype.scrollTo`**, and
  the chat component's `afterRenderEffect` uses both, so every spec that renders it must
  stub them or the render throws. This was masked as an unrelated `TypeError`.
- **`fixture.destroy()` before `TestBed.resetTestingModule()` is mandatory for components
  that own a timer.** `DestroyRef.onDestroy` is what clears the poll interval, so a
  fake-timer test that skips teardown leaves hundreds of queued HTTP requests. Flushing
  them is also wrong: the subscription is already unsubscribed, so it throws
  `Cannot flush a cancelled request`.
- **jsdom exposes no `localStorage` at all**, which broke every spec touching `ThemeService`.
  Fixed in `src/test-setup.ts` via the builder's `setupFiles`, not by guarding application
  code.
- **`extractApiErrorMessage` collapses every 5xx to `Ошибка сервера`**, so a test asserting
  the backend's `detail` string on a 500 fails. Assert the normalised message, or use a 4xx
  where `detail` is passed through.
- **`rm -rf coverage` while a test run is in flight intermittently breaks the run** with
  "Something removed the coverage directory". Just run the coverage command; do not clean it
  first.

## Object storage (Garage)

- **The `dxflrs/garage` image has no shell at all** — no `/bin/sh`, no `curl`, no `mc`, not even
  `ls`. Anything the bootstrap needs has to come from another image.
- **Do not lift binaries out of busybox into the Garage image.** Copying `/bin/sh` in that
  direction builds an image whose shell fails with `exec /bin/sh: no such file or directory`
  even though the binary is present and executable, because those binaries expect a loader
  and directory layout the distroless base lacks. Base on busybox and copy the garage binary
  _in_ instead.
- **`GARAGE_RPC_SECRET` and `GARAGE_ADMIN_TOKEN` must be exactly 32 bytes of hex (64
  characters).** The failure is `Invalid RPC secret key: expected 32 bytes of random hex`, which
  does not say the value is the wrong length. `openssl rand -hex 48`, used for every other
  secret in `.env`, produces 96 characters and is rejected.
- **Do not bind-mount host paths in a testcontainers fixture on Windows.** Docker Desktop asks
  to share the directory, and when that prompt is dismissed the whole run fails with
  `pywintypes.error: (109, 'GetOverlappedResult')` at fixture setup — 109 errors that look like
  application failures and are not. The Garage fixture mounts nothing: `/etc/garage.toml` is
  copied in with `with_copy_into_container(config_bytes, ...)` (its `Transferable` type accepts
  raw `bytes`, so no temporary file is needed either) and the metadata and data directories are
  `with_tmpfs_mount`. Both paths are memory-only and need no host permission. Previously the
  fixture bind-mounted three pytest `tmp_path` directories, which is where the prompt came from.
- **`with_kwargs(tmpfs=...)` collides with testcontainers' own `tmpfs` argument**, raising
  `TypeError: create() got multiple values for keyword argument 'tmpfs'`, because `start()`
  already passes `tmpfs=self.tmpfs`. Use `with_tmpfs_mount(path, size)` instead.
- **Garage will not start without `/etc/garage.toml`, even when every setting is supplied as a
  `GARAGE_` environment variable**; it exits with `Failed to read config file /etc/garage.toml:
No such file or directory`. Compose only sets two secrets that way and keeps the file, which is
  why env-only configuration looks viable until you try it.
- **A presigned POST rejects any form field that is not also a policy condition**, with
  `Key 'content-type' is not allowed in policy`. The upload routes pass `Content-Type` in both
  `Fields` and `Conditions`, which is why they work; a new call site that adds a field without
  the matching condition will get HTTP 400.
- **The region decides the signature version.** With credentials defaulting to `us-east-1`,
  botocore signs presigned POSTs with the legacy `signature` field. Any other region, including
  the `garage` region this project now uses, produces `x-amz-signature`. A test asserting
  `signature` was passing for the wrong reason.
- **Garage's lifecycle worker runs once per day at midnight**, so a 1-day expiration rule
  cannot be observed in a test. An already-past `Expiration.Date` is accepted but is only
  applied by the next daily sweep.
- **The garage admin API (port 3903) resets connections when proxied**, so the admin API is not
  a reliable readiness probe here. The CLI over RPC, or the S3 port answering `GET /`, is what
  the healthcheck and the bootstrap use.
- **A secret written with `echo` carries a trailing newline**, which aiohttp rejects as
  `Forbidden control character detected in headers`. Strip it when loading credentials from a
  file in a test.

## Agent skills and documentation

- **Do not hand-write a skill for a framework you can install.** `openai/skills` (curated)
  and `anthropics/skills` have no FastAPI/Angular/Docker/Python-testing entries, but
  `samuelpkg/skills`, `xfstudio/skills` and `github/awesome-copilot` do. Install with
  `~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py --repo
<owner>/<repo> --path <dir> --dest <dest>`. A hand-written flat `SKILL.md` cannot carry
  real framework depth, and guessing at framework APIs is where an agent invents things.
- **An installed vendor skill silently contradicts a project-specific one.** The Angular
  skill assumes SSR, hydration and zoneless change detection; this app has none of them. The
  FastAPI skill shows `db: AsyncSession = Depends(get_db)`, while `deps.py` mandates
  `Annotated` aliases used as parameter types. Installing without an override file makes the
  harness actively wrong.
- **A `references/` file nothing links to will never be read.** An agent loads `SKILL.md` and
  stops. Point at the overrides from the frontmatter `description` _and_ a callout at the
  top of the body, or it is dead documentation.
- **A skill's own `metadata` can misdescribe the project.** `xfstudio/skills:angular`
  declares `organization: "Antigravity Awesome Skills"` while living in a repo by a
  different author. Verify provenance from the source repo, not the payload.
- **`websearch` returned HTTP 403 in this environment; the Code Mode `search` tool returned
  nothing.** Use `curl` against `api.github.com`, or the authenticated `gh api`, instead.
  `gh` is logged in, which also unlocks code search (`gh api "search/code?q=..."`) that
  unauthenticated curl cannot do.
- **Document new tooling in README, not just AGENTS.** The old README had no
  architecture, feature, port, test or contribution documentation at all, which is why the
  project had three unrunnable-looking parts (`parser/`, no `.env.example` entry for
  `TEST_INSTANCE_MODE`, no LICENSE).

## Verifying migrations

- **`backend/tests/test_migrations.py` already proves every migration works.** It runs
  `alembic upgrade head`, `downgrade base`, re-`upgrade` and `alembic check` against a real
  PostgreSQL container. I did not know this, so I hand-rolled a throwaway postgres to run
  `alembic check` by hand — and the container published 5431, colliding with the developer's dev
  database and knocking it offline. The suite had already answered the question in 12 seconds.
  **Read the existing tests before writing a verification step.**
- **Do not run `alembic` from a worktree without a reachable database.** It resolves
  `POSTGRES_HOST` from the environment, and the db port is only published by the dev override.
  With the e2e override in place the port is unpublished and the command fails on a DNS or
  connection error that looks like a migration bug.

## Destructive operations

- **`docker compose down -v` destroyed the developer's PostgreSQL data.** I ran it to prove the
  object-storage bootstrap works from empty volumes, reasoning that the database was "just a
  test database". That reasoning was mine to make and it was wrong: the volume can hold real
  accounts, chats and attachments, and there is no way to check from the host whether it does.
  The user had to tell me not to do it again.
- **The general rule: never issue a command whose effect is irreversible and invisible.**
  `-v` is a single character that deletes a database. If a verification seems to need a
  destructive step, ask first, or verify against a throwaway name instead — in this case a
  fresh `docker run` or a separate Compose project name would have proved the same thing
  without touching anything.
- **"It is probably fine" is not a risk assessment when the cost is someone else's data.**
  Blast radius decides whether a guess is acceptable, and here the blast radius was the user's
  work.

## Cross-cutting

- **`pytest-cov` needs to be a dev-group dependency, not a global install.** `poetry add
--group dev pytest-cov` from `backend/` is the only correct route; the pre-push hook runs
  `poetry -C backend run pytest --cov`, which resolves the plugin from the backend venv.
  A globally installed `pytest-cov` will not be visible there.
- **`--cov` alone does not produce `coverage.json`.** Coverage is configured in
  `backend/pyproject.toml` under `[tool.coverage.json] output = "coverage.json"`, but that
  section only takes effect when the `json` report is actually requested. The CI step uses
  `--cov --cov-report=term-missing --cov-report=json`; a bare `--cov` prints the terminal
  report and writes nothing, so the upload-artifact step would fail on a missing file.
- **`pytest-cov` is silent about a missing threshold, which is fine.** With `fail_under = 0`
  the run exits 0 and still prints `TOTAL`. Do not read "exit 0" as "no coverage"; read the
  `TOTAL` line.
- **Coverage of untested code should be read as a work list, not a score.** The `Missing`
  column of the terminal report is the actionable output: it names the exact lines. This is
  also why no coverage figure is written into `.context/` — the report is regenerated on
  demand, so a recorded number only goes stale.
- **A pre-push hook that needs a Docker daemon will fail on a machine without one.** The
  coverage hook inherits the backend suite's testcontainers requirement, so a developer
  without Docker cannot push. This is the same constraint as the existing `pytest` hook and
  is accepted deliberately; CI is the authoritative check.
- **`pre-commit install --hook-type pre-push` must be re-run** after adding a hook with
  `stages: [pre-push]`. A clone that only installed `pre-commit` and `commit-msg` has no
  `.git/hooks/pre-push` and will silently skip the coverage report entirely.
- **The root `parser/` directory is dead**: only `__pycache__` from the pre-`processing/` era,
  with Python 3.13 bytecode. Nothing in the build, pre-commit or CI references it. If a file
  you need seems to be "missing" from `processing/`, search the git history
  (`git log --all --diff-filter=D -- 'parser/*'`) rather than writing it into `parser/`.
- Read `backend/app/harness.py` before changing anything about generation output. The
  structured schema and the prompt are a single coupled contract; a prompt edit without a
  schema edit, or the reverse, is how the model starts returning unparseable JSON.
- **`except ValueError, TypeError:` is valid Python 3.14 here, not Python 2 syntax.** PEP 758
  allows `except` and `except*` to omit parentheses for multiple exception types. It appears in
  `processing/src/processing/calculator/calc.py` and six times in
  `processing/src/processing/parser/dxf_parser.py`, and black with
  `target-version = ["py314"]` **formats it without parentheses**. `requires-python = ">=3.14,<4"`
  in both `pyproject.toml` files makes this safe. It looks exactly like Python 2 syntax to a
  reader, an older linter, or an automated review, and has been misreported as a critical bug
  more than once. Do not "fix" it by adding parentheses; doing so produces a diff against the
  project's own formatter and will be reverted by the next `black` run.
- **Check `target-version` before judging unusual syntax in either language.** This project
  pins `py314` for black and ruff and Angular 21 / ES2022 for the frontend, so modern-only
  constructs are expected rather than suspicious.
- **`Position.material_id_to_material_str` catches the wrong exception**
  (`app/providers/models.py`). It does `MATERIALS[int(val)]` inside
  `except KeyError`, but `MATERIALS` is a **list**, so an out-of-range index raises
  `IndexError` and the raw error escapes `model_validate` instead of the intended
  `ValueError("Invalid material id")`. A negative index also silently wraps to the last
  element. A test pins the current behaviour and says what the fix is; the one-word fix
  (`except IndexError`) is left for a maintainer.
- **`cleanup_orphan_attachments` deletes rows but leaks their S3 objects**
  (`app/tasks/files.py`). It calls `orphans.all()` to log a count, which exhausts the
  `ScalarResult`; the following list comprehension then iterates a closed result and
  collects nothing, so `asyncio.gather` runs zero `_s3_try_delete` calls. The logged
  `count` is rows found, not rows whose objects were removed.
- **The assistant's own reply lives in `chat_messages`, so "the whole history" includes it.**
  `process_response` stores the raw provider JSON as a `ChatMessage` with
  `role=UserRole.ASSISTANT`, not in `generation_results` alone, which is why a retry handed the
  model the answer it was being asked to replace. Anything that reads the session as history
  has to cut it at the last `UserRole.USER` row; the reasoning is in `DECISIONS.md`.
- **`redis_keys` in a worker test signature is not dead weight, and deleting it breaks the test
  with a DNS error.** It looks unused when a test never calls it, but it is what pulls in
  `redis_session`, which stands the testcontainer up and repoints the `@cache`d pool at it.
  Remove it and the worker dials the Compose hostname `redis`, failing with
  `socket.gaierror: Name or service not known` inside aioredis' reconnect loop.
- **`generate_chat_message` cannot tell a retry from a new message.** Both `send_message` and
  `retry_send` call the same task with the same session id, so history shaping has to live in
  the worker. Fixing it in the route looks right and leaves the other caller unfixed.
- **Two chat endpoints disagree about attachment readiness.** `GET /chats/` returns every
  attachment while `GET /chats/{id}` filters on `ready == True`. Pinned by tests rather
  than papered over; one of the two is a bug.
- **Verify claims against the source before reporting them, and verify your own corrections.**
  The misdiagnosis above was first rejected with the wrong reason ("the review misread a
  wrapped tuple") and only later traced to PEP 758. Confirming _that_ a file parses is not the
  same as confirming _why_ it parses, and a confident wrong explanation is worse than the
  original false positive.
- **`PurePath` does not split on a backslash when running on POSIX.** `PurePath("..\\..\\x.pdf").suffix`
  is `.pdf` and `PurePath("/etc/passwd.pdf").suffix` is `.pdf` too, so an extension check built
  on `PurePath` happily accepts a name that is really a path. That is why the chat upload route
  tests the name shape separately (`_is_bare_filename`) before it tests the suffix.
- **A NUL byte in a string column is a 500, not a 400.** asyncpg refuses the write with
  `CharacterNotInRepertoireError: invalid byte sequence for encoding "UTF8": 0x00`, so a request
  carrying `"filename": "dr\0awing.pdf"` used to get past every check in `upload_file` and blow
  up in the commit. `str.isprintable()` is False for NUL and for every other control character, so
  one call covers the whole class.
- **`HttpTestingController.expectOne(url)` matches the full URL including the query
  string.** Passing `` `${apiUrl}/chats/` `` to a request that now carries `?page=0&...`
  fails to match with an unhelpful "Expected no open requests", and the error names the
  URL it did find. Match with a predicate on `r.url` plus `r.params.get(...)`, and prefer
  that when the params are the thing under test.
- **Client-side sorting does not survive pagination.** The history page sorted the whole
  array in a `computed()`, which is exactly what stops being possible once the server
  returns one page: page 2 under one order has nothing to do with page 2 under another.
  The same trap applies to any list that gains paging later, so "sort in the browser" in
  this codebase is a decision with an expiry date.
- **A loading flag that hides the table makes paging look like a crash.** With
  `@if (loading())` around the whole list, clicking "next page" replaced the rows with a
  skeleton and any assertion on the pager failed because the pager was gone. Gating the
  skeleton on `loading() && sessions().length === 0` keeps the current page on screen
  while the next one is in flight, which is also what the user expects.

- **`ScalarResult.all()` exhausts the result; a second iteration silently yields nothing.**
  `cleanup_orphan_attachments` in `app/tasks/files.py` calls `orphans.all()` for the count and
  then iterates `orphans` again to build the S3 delete tasks, so it always logs `count=0` and
  never deletes an object. The bulk `delete(Attachment)` after it is a separate statement and
  does still run, so rows disappear and the S3 objects are left to the lifecycle rules. Not
  fixed here (issue #47 does not own it); collect into a list once instead of iterating twice.
- **`Literal` in a response schema turns a stale cache value into a 500.** `list_attachments`
  maps any Redis status it does not recognise to `unknown` rather than handing it to
  `ChatSessionAttachmentSchema`, because a 500 for a cosmetic label is the wrong trade.
- **A frontend "delete" that only forgets locally is a data leak.** `DragNDropComponent.removeItem`
  dropped the row from its signal and nothing else, so the file stayed on the session and was
  attached to the next message. The upload observable now reports the attachment id as soon as
  the backend registers the file, which is what makes the delete reachable at all.
- **`redis.mget([])` never reaches the server, and that is redis-py, not Redis.** `MGET` with
  no keys is a protocol error, so `list_attachments` would have 500ed on a session with no
  attachments if the client passed it through; `redis.commands.core.mget` substitutes an
  `EMPTY_RESPONSE` option and answers `[]` locally. Convenient, but it is a client detail, so
  a route that relied on it is one redis-py rewrite away from a 500.

- **A Compose override cannot isolate volumes; only the project name can.** Compose prefixes
  named volumes with the project name, and volume names are not overridable per service. The
  e2e suite therefore ran against the _developer's_ `digradation_postgres_data` and had written
  13 users and 278 seeded chat sessions into it. Fixed with `-p digradation-e2e`. Anything that
  shells out to Docker from a test must repeat that `-p`, or it silently targets the dev
  stack — which is exactly how `clearRateLimits` ended up clearing the developer's Redis
  while the e2e counters went uncleared and the suite tripped its own rate limiter.

- **Compose MERGES a service's `ports` across files; it does not replace them.** A plain
  list in an override leaves the base file's binding in place, so `docker-compose.override
.yml.e2e` requesting 18080 still tried to bind the base file's 8080 and the container
  refused to start whenever a dev stack held it. Use the `!override` tag when you mean to
  replace. `ports: !reset []` is the same idea for clearing.

- **Copying an override over `docker-compose.override.yml` corrupts unrelated stacks.** The
  e2e setup used to `cp` the e2e override into place, so a later `docker compose` command in
  the repo — a developer's — inherited the e2e ports and recreated their containers with
  them. The dev frontend ended up publishing both 8080 and 18080. Name the file with `-f`
  instead of copying it.

- **A container left in a failed port-bind can come up with no network attached.** After a
  bind collision, `digradation-e2e-frontend-1` was stuck `restarting` with an empty
  `NetworkSettings.Networks` map, and nginx reported `host not found in upstream "backend"`
  even though the backend container was healthy and correctly aliased. `--force-recreate`
  fixed it; a plain restart would not have.

- **A `1fr 1fr` grid with a table inside blows out the centered container.** Grid `1fr` tracks are `minmax(auto, 1fr)`, so a wide table (unbreakable badges like `Суперпользователь`) forces the track past its share and the grid spills right past `max-width: 1200px; margin: 0 auto`, making the page look shifted versus single-column pages. Fix is `min-width: 0` on the grid children plus an `overflow-x: auto` wrapper around the table, the same pattern `history-page` already uses. No SCSS gate catches this (ESLint skips `.scss`, no stylelint).
- **Garage has no default CORS policy, and the 403 it returns does not name the cause.** Every
  browser upload and download goes through a presigned URL, so each one is preflighted; with no
  `cors_config` on the bucket, `handle_options_for_bucket` finds no matching rule and answers
  `403 This CORS request is not allowed.` MinIO allowed `*` by default, so the same buckets were
  unusable from a browser the moment they moved. Test suites do not catch it: nothing in the
  backend performs a preflight, and presigned POST and GET both succeed server-side.

- **Garage resolves `OPTIONS` against the bucket's CORS config only, never the key.** It calls
  `resolve_global_bucket_fast` on an unauthenticated request, so a _local_ alias gets a
  permissive allow-everything answer while a _global_ bucket with no CORS config gets a hard 403. The failure therefore depends on how the bucket was created, not on what the client did.

- **`garage bucket` has no `cors` subcommand.** Subcommands are alias, allow,
  cleanup-incomplete-uploads, create, delete, deny, info, inspect-object, list, set-quotas,
  unalias and website. CORS, like lifecycle, has to go over the S3 API, which is why both live
  in `backend/apply_s3_config.py`. Run `/garage <group> --help` before assuming a command is
  missing; the image has no shell, so the binary has to be invoked by absolute path.

- **The e2e attachment specs pass even when the upload never reaches object storage.** The row
  in "Загруженные файлы" and the `GET /chats/{id}/attachments` listing are both satisfied by the
  backend registering the attachment, which happens _before_ the browser performs the presigned
  POST. A run on 2026-10-09 passed all three specs with zero `POST` requests in the Garage log.
  Two causes stacked: the e2e override resets Garage's published ports, so the browser has no S3
  endpoint in the e2e project at all, and on a developer machine it would reach the _dev_ Garage
  instead, because both stacks read the same `S3_ACCESS_KEY`/`S3_SECRET_KEY` from `.env`. So
  neither the CORS regression nor a dead store is covered. Asserting the upload finished
  requires waiting for the status to leave `queued`, and the e2e project needs its own published
  S3 port and matching `S3_PUBLIC_URL`.

- **A `--cov` run started before an edit finishes measures a half-written tree.** A run kicked
  off before an edit and read afterwards showed a far lower figure than the same tree once
  settled, alongside a failing `alembic check` that looked like real model drift. Coverage
  measured concurrently with your own edits is not a baseline; re-run it after the tree is
  quiet, and treat the `alembic check` failure as expected rather than as drift.
- **On a 4 GB Docker Desktop, bring the e2e stack down before running Compose tooling.** With
  `digradation-e2e` up, `docker compose up -d db` and even a bare `docker compose ps` hung for
  minutes, in the bash script and in the PowerShell one alike. The giveaway was a
  `502 Bad Gateway` on `//./pipe/docker_engine` from `docker inspect`: the engine's API proxy was
  failing, so the scripts were innocent and retrying into a sick proxy is what wasted the time.
  `docker compose -p digradation-e2e -f docker-compose.yaml -f docker-compose.override.yml.e2e down`
  (no `-v`) makes the tooling reliable again.

- **A 422 whose detail quotes a `NaN` cannot be encoded, and the failure looks like a 200 or a
  crash rather than a validation error.** Python's `json.loads` accepts the `NaN` and `Infinity`
  literals, so a client can send one; Starlette's `JSONResponse` writes with `allow_nan=False`,
  and FastAPI's default `RequestValidationError` handler copies the offending input into the
  detail. The response then fails to serialise. Fixed by a handler in `app.main` that replaces
  non-finite floats with their text form and then applies `jsonable_encoder` — omitting the
  latter breaks any error detail containing a Pydantic model, including pre-existing routes.
  `httpx` cannot _encode_ these values, so a test using `json=` proves nothing; send the raw body.
- **A test that builds a request body from a dict of values can silently omit the wrapper key,
  and the endpoint then reports success while changing nothing.** The 422-vs-200 confusion while
  adding the rates was entirely this: the body was the bare rate object, `AdminSettingsUpdate`
  ignores unknown keys, so `parameters` was absent and the route correctly did nothing. Chasing
  it through Pydantic unions, `allow_inf_nan`, stale bytecode and installed-package shadowing was
  the wrong path — the endpoint's own logging of the received body answered it in one run. When a
  validation test disagrees with the schema, log what the endpoint actually received before
  theorising about the validation library.

- **A `rollback()` expires every instance in the session, so reading an ORM attribute
  afterwards raises `MissingGreenlet` in async SQLAlchemy.** It surfaced in the settings
  concurrency fix: the recovery path re-applied the update after rolling back and touched
  `admin.id`, which the rollback had expired. The attribute read is a synchronous implicit
  refresh, and async SQLAlchemy only performs IO inside awaited session methods. Copy anything
  you need off ORM objects _before_ the rollback, or the retry cannot run at all.
- **A template can hold the rate a setting appears to configure.** `res/calc.xlsx` ships
  `=E{row}/5.53` in the painting-hours cell, so `Parameters.painting_rate_m2_per_hour` was
  accepted by the API and ignored by the calculator; the other three rates were applied because
  their cells start empty. When a value becomes configurable, check what the artefact already
  encodes — a formula in the sheet outranks the dataclass.
- **In e2e, edit form fields with real keystrokes, not `fill()`.** The custom `app-input` /
  `app-textarea` controls propagate only trusted input events to the reactive form: a
  programmatic `fill()` changes the DOM value while the form stays pristine, so the save
  button never enables and the failure reads as "button is disabled" with the right text
  visibly in the field. `fill("")` plus `pressSequentially()` behaves like a user and updates
  the model. Assert the button is enabled before clicking, or the test passes vacuously.
- **The e2e override needs the same `linux/amd64` platform pins as the dev override.**
  Every service built from `backend/Dockerfile` fails to build natively on ARM Macs because
  pymupdf has no musl/aarch64 wheel; without the pins `setup.sh` dies in `poetry install`.
  The garage service itself stays unpinned. CI never sees this because its runners are amd64.
- **A green DOM is not a quiet backend: wait for `chat:generation:*` between e2e tests.**
  `POST /chats/` answers 409 while the previous test's generation is still draining the
  worker pipeline, and a test that only asserts on the DOM finishes long before that. The
  failure then lands on the _next_ test's `createChat` navigation with no mention of a lock.
  The auto fixture in `tests/helpers.ts` polls the key out of the e2e Redis before every
  test (bounded, 120 s); on a fast machine it is one round trip. The 429 rate-limit
  clearing next to it fixes a different symptom — check the backend log for which one fired
  before debugging the test.
- **Open the users slide-over before touching the form in e2e.** The admin specs were
  written when the form was always visible; since it became a panel, its controls exist in
  the DOM while closed, so visibility assertions fail and count assertions pass vacuously.
  Click `Новый пользователь` (or the row edit action) first.
- **Renaming the project silently orphans every Compose volume and the Garage key name.**
  `digraduation` -> `digradation` changed the _defaults_ of `POSTGRES_USER` / `POSTGRES_DB`,
  and Compose derives both the volume names and the container names from the project
  directory name. After the rename a developer gets a fresh, empty database and a fresh
  `garage-bootstrap` that imports the key as `digradation-app`; the old
  `digraduation_postgres_data` volume is still on disk, untouched and unmounted, holding
  whatever accounts and chats it held. Nothing errors and nothing warns. The data is
  recoverable by pointing `POSTGRES_*` at the old names, so **do not `docker volume prune`
  after a rename** (see the "never destroy volumes you did not create" rule).
- **The Garage key's _name_ is cosmetic, its id and secret are identity.** `garage key import`
  with the same id and secret is idempotent, so renaming the key label in `conf/garage-init.sh` from `digraduation-app` to
  `digradation-app` changes only the display name. This is the opposite of `S3_ACCESS_KEY`,
  where Garage refuses to reuse an id with a different secret.
- **The brand mark is duplicated in three places, and two e2e specs assert on its text.**
  `frontend/src/index.html` (`<title>`), `frontend/src/app/shared/components/sidebar/
sidebar.component.html` (a `div.h1`, not a heading) and the locator
  `.h1:has-text("di:gradation")` in `e2e/tests/helpers.ts` and `e2e/tests/admin-roles.spec.ts`.
  A rename that touches only the frontend shows up as a failing e2e suite, not as a stale
  label.
- **The e2e Compose project name is hard-coded in four places** and is not derived from
  anything: `e2e/scripts/setup.sh` (`E2E_PROJECT`), `e2e/tests/helpers.ts`,
  `e2e/tests/history-pagination.spec.ts`, and `.github/workflows/e2e.yml`. Change one and the
  suite quietly talks to the developer's own stack — which is the accident recorded at the top
  of this file. `E2E_PROJECT` guards in the specs throw rather than fall back.
- **A mass rename has to include the lockfiles and the `sed` order matters.**
  `package-lock.json` carries the project name in its root `packages[""]` entry, so replacing
  only `package.json` leaves git dirty on the next `npm install`. And `digraduation` does not
  contain `di:graduation`, so a single pattern with the longer form first would silently miss
  the brand mark: `sed -i 's/digraduation/digradation/g; s/di:graduation/di:gradation/g'`
  handles both because neither pattern contains the other.
- **An e2e spec that uploads through the browser needs the S3 port published, and a dev
  stack on 3900 hides the fact that it does not.** `docker-compose.override.yml.e2e` used to
  `ports: !reset []` Garage, so nothing listened on `S3_PUBLIC_URL`; the three
  `attachments.spec.ts` specs failed with a file that never appeared, while passing on any
  machine where a development stack already held 3900 — which is why they survived a green
  local run and could not have passed in CI. Fixed 2026-10-16: Garage publishes
  `${E2E_S3_PORT:-13900}` and the backend's `S3_PUBLIC_URL` is rewritten to match, on a
  dedicated port so it cannot collide with a dev stack. **Nothing inside the Compose network
  can stand in for a presigned URL: the browser leaves the network to follow it.**
- **Garage refuses a key id whose secret changed, which makes a stale e2e volume look like a
  bootstrap bug.** Re-running `setup.sh` with a freshly generated `S3_SECRET_KEY` against an
  existing `digradation-e2e_garage_metadata` volume fails with `key ... exists but does not
match S3_SECRET_KEY`. It is not a bug: keep `S3_SECRET_KEY` stable, or start the e2e project
  from empty volumes (`down -v`, which is safe only for a project you brought up yourself).
- **An `exclude` entry does not survive a pre-commit hook that passes filenames
  explicitly.** The hooks here call `black <files>` and `ruff check --fix <files>`, and both
  tools honour `exclude` only for files found by directory traversal, so a path listed in
  `pyproject.toml` was still rewritten. `[tool.black] force-exclude` already guarded the
  generated migrations; ruff has no config key for it, only the `--force-exclude` flag, so the
  shim reformatted `alembic/versions/*.py` (101 lines: double quotes, `Union[...]` -> `|`) on
  every run. That was the ping-pong: `trailing-whitespace` strips the single trailing space
  Alembic's own template emits after `Revises:`, then ruff reformats the whole file back, and
  `pre-commit run --all-files` never came back clean. Fixed 2026-10-16 with `--force-exclude`
  in `scripts/precommit-ruff.py`. **The whitespace hooks were deliberately left enabled**
  rather than excluded from the generated directory: they make a freshly generated revision
  dirty exactly once, which converges, and one stripped space beats a special case in the
  config. **Symptom to recognise: a generated file that a hook "fixes" differently on every
  run.** Note also that `scripts/precommit-*.py` pass `check=False`, so these hooks modify
  files without ever failing the commit they belong to.
- **A closed `<dialog>` kept in the DOM still contributes its form controls to the document.**
  The sign-in dialog was mounted permanently and hidden with `display: none`, which did not stop
  Playwright strict mode from resolving `#username` to two elements: the hidden dialog input and
  the admin user form's own `#username`. Four admin specs failed with a strict-mode violation
  that never shows up in the browser. Fixed by rendering the dialog under `@if (isOpen())`.
  **Hidden form markup is still duplicate markup; scoping the specs would only have hidden the
  duplicate ids.** (2026-10-18)
- **jsdom implements `<dialog>` without `showModal`/`close`, and that is the environment CI runs
  in.** Every dialog needs the two-line fallback (`showModal` exists ? `showModal()` :
  `setAttribute('open', '')`) or its unit specs cannot even prove it opens. The real-browser path
  is covered by e2e, not by the unit suite. (2026-10-18)
- **A click handler on `<dialog>` trips `@angular-eslint/template/click-events-have-key-events`.**
  Use `(mousedown)` for the backdrop press: Escape already closes a modal dialog natively, and
  the rule only watches click events — no `eslint-disable` needed (the repo has none). (2026-10-18)
- **Guest mode puts several "Войти" buttons on screen at once** (the gate prompt and the
  sign-in dialog submit; the header chip is a third entry point, labelled "Войти в аккаунт"),
  so a bare Playwright `getByRole("button", { name: "Войти" })` violates strict mode. Scope
  lookups by their container: `page.locator(".login-prompt")` for the gate prompt and
  `getByRole("dialog", { name: "Вход в аккаунт" })` for the dialog. (2026-10-18)
- **`provideAppInitializer` blocks the initial navigation, so route guards may be synchronous.**
  The default `enabledBlocking` navigation waits for initializers, so `adminGuard` reads
  `ProfileService.user()` without a fetch, a `catchError` or a race. The same guarantee applies
  to anything added to the initializer — and so does the cost: every reload waits for it.
  (2026-10-18)
- **`withComponentInputBinding` writes every declared input from the route data, and a key
  the data omits arrives as `undefined` — wiping the input's default.** The admin routes
  never carried `bottomNavItems`, so the sidebar read `undefined.length` in its template on
  every change detection that touched it, the cycle aborted, and interactions later in the
  same cycle silently never rendered — the admin role select stayed open over the save
  button and two e2e tests timed out. The fix is not route data: the inputs run a
  `transform: (v) => v ?? []` so the lists stay arrays whoever binds them. (2026-10-18)
- **The layout's `main` is the scroll container (`overflow-y: auto`), not the window.**
  Playwright scrolls it while clicking, and the select closes any dropdown whose trigger
  scrolls out of view — the admin user form is taller than the viewport, so the two fought
  and the listbox detached mid-click. Drive such selects from the keyboard in e2e
  (`ArrowDown` + `Enter` — a first-class path of the component); it touches no scrolling and
  no hit-testing. (2026-10-18)
- **`::view-transition-old(root)` / `new(root)` cross-fade the entire page by default.**
  Without `animation: none` on the root pair, naming one element does not stop the sidebar
  and header from fading on every navigation. Only the named-element pair should carry
  keyframes. (2026-10-18)
- **A `calc(100dvh - Npx)` "fill the rest of the viewport" rule is a bet on a number that no
  one owns.** `min-height: calc(100dvh - 80px)` on the auth gate missed the header's real
  83px (its height comes from the avatar, not from a constant) and produced a 3px scrollbar
  on every gated route. Let the layout hand the space over instead: `main` is a column flex
  container, the header is `flex-shrink: 0`, the filler is `flex: 1 1 auto; min-height: 0`.
  Check the free space at more than one viewport size. (2026-10-18)
- **"These two icons are a slightly different colour" is usually a stroke-width illusion.** The
  sidebar's collapse and theme buttons both resolved `color` to `--sidebar-muted`
  (`rgb(148,163,184)`, byte-identical), yet looked different: Lucide's stroke is 2 _viewBox_
  units, so a `size="20"` icon drew a 1.67px stroke where a `size="18"` one drew 1.5px, and the
  thinner stroke antialiases lighter against the sidebar. Compare the rendered stroke px
  (`size / 24 × 2`) before reaching for the colour token. (2026-10-18)
- **`(keydown.enter)="submit()"` on an input inside a `<form>` fires the submit twice.** The
  browser also implicit-submits the form on Enter (there is a `type="submit"` button), so
  `ngSubmit` runs `submit()` a second time in the same keystroke. A valid form hides it because
  the first call sets `isSubmitting`; an invalid form does not, and both calls raise the
  validation toast. Rely on the native implicit submission and drop the extra handler — the
  admin user form already does. Guarded by a spec that dispatches `keydown` then `submit`.
  (2026-10-18)
- **`@if (isOpen())` around a `<dialog>` hides it, but the component instance and its reactive
  form outlive the cycle.** Resetting the form only in the success path left a dismissed wrong
  password in memory, re-rendered on the next open. Clear it on every close path (button,
  Escape, backdrop, native `close`) — centralise them through one `close()` that resets first.
  (2026-10-18)
- **An HTTP interceptor that injects the service which owns `HttpClient` is a latent NG0200.**
  `ProfileService -> HttpClient -> errorInterceptor -> ProfileService` resolved only because
  the interceptor's `inject()` runs per request (the service is already constructed). It stays
  a cycle, so an eager `inject` added anywhere on that path detonates it. Report the event on a
  dependency-free carrier (`SessionEventsService.unauthorized$`) and let the owner subscribe.
  (2026-10-18)
- **jsdom's `HTMLDialogElement` exposes only `open`.** `showModal` and `close` do not exist on it,
  so a unit test never opens a modal dialog: `openDialog` falls through to `setAttribute('open')`,
  and calling `dialog.close()` in app code throws `close is not a function` and takes the whole
  submit path down with it. Do not paper over that with a `typeof dialog.close === 'function'`
  guard — the dialog's Escape and Enter behaviour cannot be asserted in jsdom at all and belongs
  in `auth.spec.ts`. (2026-10-18)
- **A stale test count is not fixed by refreshing it.** `PROJECT_STATE.md` carried test counts
  and coverage figures that a branch dutifully updated, in the same file whose footer says they
  are deliberately not recorded — the two paragraphs then contradicted each other. When you
  touch a figure `AGENTS.md` §6 forbids, delete it and point at where the gate lives, not at a
  newer number. (2026-10-18)

## Providers and live API tests

- **The autouse `env` fixture in `conftest.py` pins `SBER_API_KEY=mock`, so every `live`
  test sees the mock key unless it asks otherwise.** `conftest.py` captures the process
  environment into `REAL_ENV` at import and the `real_provider_env` fixture restores it;
  a live test must request that fixture (directly or via a provider fixture) or the
  key it reads is the mock one. Without this, `pytest -m live` collected and passed while
  hitting nothing.
- **`app/providers/openai.py` shadows the top-level `openai` package name, and that is
  safe.** Python 3 uses absolute imports by default, so `from openai import AsyncOpenAI`
  inside the module resolves the installed package, not the module itself. Verified by the
  provider's own tests; no `importlib` or `sys.path` gymnastics are needed.
- **The `openai` SDK (v3) pins `httpx2`, a second HTTP stack next to the project's
  `httpx` 0.28.** They coexist as separate distributions, so the SDK upgrade in the lockfile
  also adds `httpx2` and its transitive deps. Do not "consolidate" them: the SDK imports its
  own package by name, and removing it breaks the provider while the rest of the app keeps
  working.
- **The school hub counts about 1200 prompt tokens before your text arrives.** A one-line
  question came back as `prompt_tokens=1223` with `cached_tokens=1200`, so the hub injects a
  sizeable preamble of its own. Budget accordingly if a future change starts caring about
  prompt size; the number does not reflect the conversation the app sent.
- **`AsyncOpenAI` extends whatever `base_url` you give it, so `OPENAI_URL` is the full
  prefix including the API version.** The hub's URL ends in `/llm/v1` and the SDK appends
  `/chat/completions`; a URL missing the version segment is not the endpoint the SDK
  actually calls.
- **An explicit `{"type": "text"}` is accepted by the hub, so omitting `response_format`
  for a text request is a choice, not a workaround.** The provider omits it because the
  internal text `ResponseFormat` carries no schema to send; verified live that
  `response_format={"type": "text"}` also succeeds, so this is not a compatibility
  constraint to preserve.
- **`SBER_API_KEY=mock` must be checked before the scope, or a leftover
  `SBER_API_SCOPE=openai` defeats it.** Selection used to test `scope == "openai"` first, so
  the e2e contract — the key alone guarantees no call leaves the process — held only while
  the scope was also `PERS`. `SBER_API_KEY=mock` with `SBER_API_SCOPE=openai` built a real
  `OpenAIProvider` carrying the literal token `mock`, which is a network call where a
  developer expects none. The container now tests the mock key first. The follow-on:
  `MockProvider` then receives scopes it used to reject, so `MOCK_SCOPES` derives from the
  `Scope` literal instead of listing the GigaChat scopes by hand. Found 2026-10-18 against a
  `.env` that sets `SBER_API_SCOPE=openai`.
- **GigaChat v2 returns `function_call.arguments` as a JSON object, not a string**, and
  nests it inside `messages[].content[]` rather than a top-level `tool_calls`. A parser that
  trusts the spec's "wrapped in a string" wording ends up with a `dict` where `FunctionCall`
  expects `str`. Normalise in a `mode="before"` validator. Found 2026-10-18 against the live
  API.
- **A PERS-scope live run needs the stack's Redis**, because every `_authed_request` takes
  `api:global:lock` through the hardcoded `redis://redis:6379`. On a bare host the call dies
  with `Timeout connecting to server` before it reaches GigaChat. Run PERS live tests inside
  the Compose network, or replace `_pers_api_lock` with a no-op for a one-off probe.
  B2B/CORP skip the lock entirely.
- **The Angular CLI's first-run analytics prompt aborts an `ng` run, and it first bites inside
  the pre-push hook.** `vitest-coverage` runs `npx ng test`; on a machine whose CLI has never
  been asked, the "share pseudonymous usage data?" question aborted the run (`User force
closed the prompt with 0 null`, hook exit 127) and the push died before anything was sent.
  `SKIP=vitest` does not prevent it: the hook ids are `vitest-coverage` and
  `pytest-coverage`, and there is no plain `vitest` hook. The remedy is per machine: answer
  it once (`npx ng analytics disable` in `frontend/`, or `NG_CLI_ANALYTICS=false`); a
  repo-level `"cli": { "analytics": false }` in `frontend/angular.json` was tried on
  2026-10-18 and reverted the same day. Found 2026-10-18 on `feat/gen-cicle`.
- **RUF002/RUF003 fire on docstrings and comments even though RUF001 (strings) is ignored.**
  A quoted Russian phrase inside an English docstring, or a `×` in a comment, fails
  `ruff check .` on an ambiguous character while the same text inside a string literal is
  fine. Paraphrase in English or move it into a string. Found 2026-10-18 on `feat/gen-cicle`.
- **The `chat_session` fixture in `tests/conftest.py` is broken and unused.**
  `ChatSession.session_id` is the primary key with no default, so requesting the fixture
  inserts NULL and dies with a `NotNullViolation`; nothing has ever requested it. A test
  that needs a chat builds its own row with an explicit `uuid4()`.
  Found 2026-10-18 on `feat/gen-cicle`.
- **A `.ruff_cache` verdict can outlive the tree it was computed on.** `ruff check` reported
  "All checks passed" on a tree where a clean checkout of the same commit failed I001 in
  `tests/test_agentic_tools.py`: the cache entry was written while `app/models/program.py`
  still existed, and the cache key does not include which modules are on disk. Run
  `ruff check --no-cache` (or delete `.ruff_cache`) after moving modules out of the tree.
  Found 2026-10-18 on `feat/gen-cicle`.
- **An import of a module the tree does not contain is re-sorted as third-party by ruff's
  isort — in `tests/`, but not inside `app/`.** With `app/models/program.py` missing,
  `from app.models.program import ChatProgram` was flagged as an import-order error in
  `tests/test_agentic_tools.py` while the identical import in `app/agentic/tools.py` passed:
  inside the `app` package the file's own package prefix keeps the import first-party. So a
  module absent from the tree shows up as a lint error in the tests and nowhere else, and
  "fixing" the order locally only moves the error to CI. Found 2026-10-18 on `feat/gen-cicle`.
- **`commitlint` validates every commit of a pull request, not just the new push.** A
  sentence-case subject in the branch's first commit failed the `pull_request` check while
  the `push` check for the same range passed. The only fix is to rewrite that commit's
  message (rebase + force-push); a new commit on top cannot clear it, and relaxing
  `subject-case` would weaken the gate for the whole repository. Found 2026-10-18 on
  `feat/gen-cicle`.
- **A failed `git push` can leave the pre-push `ruff` auto-fix in the working tree.** The
  hook reported "files were modified" and aborted the push, but its import reorder stayed
  behind after the message "Rolling back fixes...", so the next `git status` decides whether
  the change was wanted. Found 2026-10-18 on `feat/gen-cicle`.
- **A conflicting pull request gets no `pull_request` workflow runs at all.** GitHub skips
  the event when the merge commit cannot be built: no runs, no cancelled entries, nothing in
  the checks list, while `push`-triggered workflows (commitlint) keep reporting for the same
  commits. After #26 merged and `main` moved, PR #24's checks vanished this way. Re-pushing
  and reopening the pull request do not help — `reopened` is skipped for the same reason —
  the conflict has to be resolved first. Found 2026-10-18 on `feat/gen-cicle`.
- **`AsyncOpenAI` silently defaults to the public OpenAI endpoint when `base_url` is
  omitted, and an empty string counts as omitted.** The provider now requires a non-blank
  `OPENAI_URL` and always passes `base_url`, so a missing setting fails at startup instead
  of quietly sending traffic to a public host. Found 2026-10-21.
- **A Playwright test timeout cannot interrupt a synchronous `execFileSync`.** The e2e
  helpers run it inside an autouse fixture, so a hung Docker call would outlive the test's
  deadline and hang the worker. Bound the subprocess with `execFileSync(..., { timeout })`
  and use `setTimeout` rather than `Atomics.wait` for the retry backoff, so the event loop
  stays free. The global test timeout is not a backstop for `execFileSync`. Found 2026-10-21.
- **A fixture that swallows a Redis or Docker timeout leaves stale state for the test.** A
  failed rate-limit clear or an unconfirmed generation lock used to be ignored, so the next
  test failed later with a misleading 429 or 409. Retry within a deadline, then throw with
  the underlying reason; "the suite is already broken" is not a reason to let the next
  assertion misdiagnose it. Found 2026-10-21.
  - **Poetry writes absolute `file://` URLs for `poetry add ../path` on some
    installs.** When committing a path dependency, verify the URL in
    `[project.dependencies]` is `file:../<pkg>`, not `file:///home/<user>/...`.
    An absolute path works locally and fails on CI with "path not found".

- **`dimension=0` is falsy.** `dimension or default` silently replaces 0
  with the default. Use `dimension if dimension is not None else default`
  when 0 must be rejected explicitly.
- **`public/assets/svgs/logo.svg` is not a vector mark.** It is a 1.6 MB SVG whose only
  child is one `<image>` holding a base64 PNG, so there are no paths to recolour or morph and
  editing it to animate is not possible. It stays the sidebar mark unchanged; the landing
  animates around the rendered `<img>` (aura, breathing, masked sheen) rather than editing the
  file. Read an SVG's contents before promising to animate it. Found 2026-10-19 on
  `feat/main-page`.
- **A `--modifier` class loses to `&:focus-within`.** A pseudo-class counts like a class,
  so `.field:focus-within` (0,2,0) outranks `.field--dragging` (0,1,0) no matter the source
  order; the focus ring kept painting under the drag blur until the state was repeated as
  `&--dragging:focus-within`. Found 2026-10-20 on `feat/main-page`.
- **Changing `animation-duration` on a running animation jumps its progress.** The elapsed
  time is kept, so the new duration maps it to a different keyframe and the element snaps;
  the landing logo sped up 7s to 3.8s on focus and occasionally popped in size. Keep the
  duration fixed and animate a separate property for the active state. Found 2026-10-20 on
  `feat/main-page`.
- **A click handler that only moves focus still trips two a11y lint rules.**
  `click-events-have-key-events` and `interactive-supports-focus` treat the composer wrapper
  as an interactive control even though its click only focuses the textarea. Adding
  `tabindex` would create a redundant tab stop; `role="presentation"` is the accurate signal
  that the wrapper is not an action, and both rules accept it. Found 2026-10-20 on
  `feat/main-page`.
- **A local `ng build` keeps the `__BACKEND_URL__` placeholder.** Only
  `docker/rewrite-env.mjs` replaces it at image build time, so serving `dist/frontend/browser`
  statically sends API calls to `/__BACKEND_URL__/api/v1/...`. Match that suffix in test
  interception, or rewrite the output first; the app is not broken. Found 2026-10-20 on
  `feat/main-page`.
- **TypeScript has no `SpeechRecognition` interface.** `lib.dom.d.ts` ships
  `SpeechRecognitionResult`, `SpeechRecognitionResultList` and
  `SpeechRecognitionAlternative`, but not the controller or either event, so a wrapper needs
  minimal local interfaces for `start` / `stop` and the two events. Do not reach for `any`.
  Found 2026-10-20 on `feat/main-page`.
- **In continuous recognition, every result event carries all results seen so far.**
  `event.resultIndex` is the first changed one; accumulating from `0` duplicates earlier
  finals, while starting at `resultIndex` without having processed the earlier events drops
  them. A real browser delivers from `0`, but a test that jumps straight to `resultIndex: 1`
  produces a transcript missing the first phrase. Found 2026-10-20 on `feat/main-page`.
- **A Web Speech `network` error in Chrome is the browser's cloud recogniser, not our
  backend.** Chrome sends audio to Google; where that is blocked, or in Chromium builds
  without the API keys, every start fails with `network`. Safari uses Apple and worked on the
  same machine, which is the quick way to tell the two apart. Found 2026-10-20 on
  `feat/main-page`.
- **The home-page component stylesheet is close to the `anyComponentStyle` error budget.**
  `angular.json` warns at 6 kB and fails the production build at 10 kB; the home composer
  alone was around 9.9 kB, and a tile's worth of CSS crossed the error line. Trim
  declarations rather than raising the budget, or move a self-contained block into its own
  component as the attachment tile did, and run `npm run build` after styling this page.
  Found 2026-10-20 on `feat/main-page`.

- **A conditionally rendered dialog host is still a flex item.** Rendering `<app-file-preview>` at the end of the home's flex column inserted a zero-size host into the flow, and the column's `gap` shifted the composer up by one gap the moment a tile was clicked. `display: contents` on the host removes its box while the native `<dialog>` stays in the top layer. Found 2026-10-20 on `feat/main-page`.

- **Commitlint needs three things that are all per clone.** The root `npm install` provides `@commitlint/*`, `pre-commit install --hook-type commit-msg` wires the hook, and the hook entry must let pre-commit pass the message path; with `pass_filenames: false`, `commitlint --edit` silently linted the stale `.git/COMMIT_EDITMSG` instead of the message being committed. Found 2026-10-20 on `feat/main-page`.

- **A transcript-rendering effect that reads the input signal retriggers on every keystroke.** Read the composer value with `untracked()` and replace the previously applied transcript in place; rebuilding from a value captured at recording start discards anything the user typed mid-dictation. Found 2026-10-20 on `feat/main-page`.

- **A request that never closes its socket is not the same as a failed one.** RxJS `timeout()` on each upload phase plus `XMLHttpRequest.timeout` on the storage POST is what actually ends the starter stream; without them `forkJoin` waits forever, `finalize` never runs and the send button stays disabled. Polling limits alone do not help while the status request itself is hung. Found 2026-10-20 on `feat/main-page`.

- **A fire-and-forget start subscription outlives the page that owns it.** Without `takeUntilDestroyed` the success handler still navigates after the user left `/home`; with it, leaving cancels the request and keeps the user's chosen destination. Lock the composer while `isStarting()` too, otherwise edits made during the request silently vanish on navigation. Found 2026-10-20 on `feat/main-page`.

- **An effect tracks every signal its call chain reads, not just the ones in its own body.** Leaving a failed guest draft in place while the effect called `startChat` made the effect depend on `isStarting()`, which toggled on each retry, so the effect rescheduled itself forever. Wrap the whole consume-and-start block in `untracked()`; keeping the draft until the start succeeds is then a safe way to "restore" it. A synchronous run that never finishes is a hint that effects are looping, not that a request is hung. Found 2026-10-20 on `feat/main-page`.

- **Name and size do not identify a `File`.** Two different files can share both, and the composer deduplicated on that pair, silently dropping the second attachment. Compare the `File` reference instead; the same instance presented twice is the only safe "already attached" signal. Found 2026-10-20 on `feat/main-page`.
- The home composer must validate an attachment's extension and size in `addFiles`, before it
  registers a `PendingFile`. The accepted list and the size cap already existed, but only the
  drag-and-drop component on the chat page enforced them; the home flow discovered a bad file
  after `ChatStarterService` had already created the session, so the user saw an upload error
  instead of an immediate one. Both paths now gate on `ACCEPTED_EXTENSIONS` and `MAX_FILE_SIZE`.
- `speech.stop()` is not the end of the utterance: the engine delivers its final result and
  only then fires `onend`. Reading the message right after `stop()` drops the last words.
  `SpeechRecognitionService.stopAndWait()` resolves on `onend`/`onerror`; the home page holds
  the composer while awaiting it and folds the settled transcript in synchronously, because
  the transcript `effect()` runs on a later tick than the awaited continuation.
- **A 1px border on a rounded corner is a dotted arc at DPR 1, and `outline` draws the same dots.** The border's antialiasing leaves partially covered pixels that blend with the page background; dark theme makes that worse because the background is darker than the surface, so the arc reads as fading out. Raising `--color-outline` (`#263349` to `#33445f`) keeps the arc visible without thickening it. Verified against the running dev server at 1x and 2x, 2026-10-21.
- **A masked gradient sheen must not tile.** The logo wave animated `background-position` on a `260%` gradient, so the image repeated and its edge crossed the mark, cutting the highlight off with a straight line mid-sweep. Move the band to an oversized (`inset: -100%`) pseudo-element and animate `transform` instead; the tile seam then stays outside the mask. Found 2026-10-21 on `feat/main-page`.

- **`redis-py`'s `lock()` owns its token, so it cannot be released from another connection.**
  The context manager's token is private, and `delete()` on the key would remove whatever lock
  is there now. A task that must release a lock it took in another `async with` block needs its
  own `uuid4().hex` token plus a Lua compare-and-delete. Found 2026-10-20 on
  `feat/program-cycle`.
- **`generation_results.chat_session_id` is the primary key, so a second result for the same
  chat is an `IntegrityError`, not a second row.** Any worker that wants to post a message into
  a chat mid-run must update the existing result instead of adding one — the frontend reads one
  result per poll and `generate_chat_message` deletes it at the start of the next turn. Found
  2026-10-20 on `feat/program-cycle`.
- **TaskIQ picks a new task module up through `-tp 'app/tasks/*.py'`, not through
  `app/tasks/__init__.py`.** There is no `__init__.py` and `import app.tasks` imports nothing,
  so a worker does not need a registration step — but a _test_ that imports the task body does,
  and the `task_db` fixture has to patch `tsq_db` in the new module by name or the task quietly
  writes to the environment's PostgreSQL. Found 2026-10-20 on `feat/program-cycle`.
- **The block models are frozen, so a test cannot mutate a nested field.** `topic.hours = ...`
  raises `frozen_instance`, and `model_copy(deep=True)` does not help because the copy is frozen
  too. Build the variant from the JSON payload (`model_copy(update=...)` on each level, or edit
  the loaded example) instead of assigning. Found 2026-10-20 on `feat/program-cycle`.
- **`store.save` refuses an older version even when nothing else changed.** A test that wants an
  aged state has to age one produced by a transition (`started`, `enqueued`), because
  `model_copy(update={"updated_at": ...})` on a saved state keeps its version and is rejected as
  stale by the row it just wrote. Found 2026-10-20 on `feat/program-cycle`.
- **The `stage` mock has to read the volume back out of its own prompt.** The curriculum block
  must total `requested_hours` or `to_draft` refuses the whole draft, and the hours stage only
  knows the volume because the brief states it — so the mock parses "Заявленный объём программы"
  from the same text the real model reads. Found 2026-10-20 on `feat/program-cycle`.
- **The two TaskIQ worker services both consume both queues.** `AioPikaBroker.with_queue()`
  _appends_ to `_task_queues` and `listen()` consumes every declared queue, so
  `worker_default` and `worker_network` are the same worker with different replica counts:
  a `default`-queue task can run in `worker_network`. Messages are not duplicated (each queue
  binds with routing key = its own name), so state guards still see one execution; but the
  `queue_name` split buys no latency isolation until `with_queues()` replaces the list
  instead. Found 2026-10-20 on `feat/program-cycle`, watching `finalize_program_draft` run in
  a network worker.
- **A `generation_results` row already exists during a run, so a mid-run message must upsert
  it.** The intake's turn writes one, the finalize task writes the draft, and both key on
  `chat_session_id`: the second insert fails on the primary key, the draft is never stored,
  and the run sits in `pending_approval` until the recovery cron retries the same failing
  write. Caught only by the Docker run, because every unit test started from a chat with no
  result row. Found 2026-10-20 on `feat/program-cycle`.
- **A replayed stage must not re-enqueue its successor.** The "worker died between the commit
  and the enqueue" replay calls `_advance` again, and a second `kiq` spends one of the
  successor's three attempts on a message the queue never lost; with the budget at three, one
  such replay turns a healthy stage into a failed run. The state can tell the two cases apart
  — a crash leaves `attempts[next] == 0`, a successful enqueue leaves `>= 1`. Found 2026-10-20
  in review of `feat/program-cycle`.
- **`execute_tool` is not the dispatcher; `call_executor` is.** The handlers are in a registry
  keyed by name, so calling `execute_tool` runs any tool that exists, advertised or not. Every
  new caller has to wrap the context in `call_executor(ctx, offered)` or the "only what this
  turn offered" rule silently stops holding for that path. Found 2026-10-20 in review of
  `feat/program-cycle`.
- **`git rm --cached` plus a rebase deletes the untracked files.** Once the deletion commit is
  part of the rebase range, checking that commit out removes the files from the working tree
  as well, because git has no reason to keep what the tree no longer has — the ignore rule
  only stops them being re-added. Recover with `git show <last-commit-with-them>:.context/X.md`;
  recover _before_ letting a rebase run over the deletion commit. Found 2026-10-20 on
  `feat/program-cycle`.
- **Do not rewrite comments in files the change only passes through.** A provider docstring
  that gains a sentence about the pipeline makes the diff look as if every provider changed,
  and the reviewer has to diff prose before code. Rationale belongs in `.context`. Review of
  #32, 2026-10-21.
- **GigaChat v2 spells the field `tool_state_id`, and `function_result.result` is a string.**
  The schema is in the gitignored `docs/gigachat.yml`, so a plausible internal spelling
  (`tools_state_id`) silently goes out as an unknown key: the id is never sent, never parsed
  back, and the second round cannot be tied to the first. Match the contract spelling verbatim,
  keep the excerpt in `DECISIONS.md`, and let the live round test prove the shape. Review of
  #32, 2026-10-21.
- **A session context manager that captured the session maker at import cannot be
  monkeypatched.** `tsq_db` used to call the `get_session_maker` imported into
  `app.tasks.conf.broker`, so patching `app.database.get_session_maker` was invisible and the
  fixture had to replace every module's binding. Defining `db_session()` in `app.database` and
  resolving the maker at call time leaves one patch point. Review of #32, 2026-10-21.
- **A lock TTL shorter than a stage's worst case loses the lock while the worker is alive.**
  Two workers then run the same stage; the version check prevents the double write, but both
  pay for provider calls. Renew the lock before every turn rather than assuming a stage fits in
  the TTL, and treat the lost compare-and-expire as "superseded", not as a stage failure.
  Review of #32, 2026-10-21.
- **A host-side live PERS test needs the Redis test container.** `SberProvider._pers_api_lock`
  serialises requests through `get_redis_client`, which is hardcoded to `redis://redis:6379`;
  that hostname does not resolve from the host, so the test dies before it reaches GigaChat.
  Depend on the `redis_client` fixture even though the test never touches Redis itself: it
  patches the pool onto the container. Found 2026-10-21.
- **Angular `animate.leave` does not run in jsdom.** `areAnimationSupported` checks for
  `document.documentElement.getAnimations`, which jsdom does not provide, so `@if` content is
  removed immediately and a test cannot wait for `animationend`. Assert removal after
  `TestBed.tick()` instead of trying to observe the leave animation. Found 2026-10-21 on
  `feat/my-programs-page`.
- **A full-card overlay button does not receive clicks through a higher content layer, even
  when that content is non-interactive.** The overlay is a sibling, and the content catches
  the hit first; give the content `pointer-events: none` and opt the context menu back in
  with `pointer-events: auto`. Found 2026-10-21 on `feat/my-programs-page`.
- **A component stylesheet over the 10 kB `anyComponentStyle` error budget fails `ng build`,
  even if the repeated rules could live globally.** Global styles — a partial `@use`d from
  `styles.scss` — do not count against the component budget, so that is where a utility shared
  by several components belongs. Measured twice: a popover block moved to a partial took the
  panel from a 12.01 kB error to 9.51 kB (2026-10-21), and the update backdrop moved to
  `_flash.scss` took the programme preview back under the budget (2026-10-22).
- **`@HostListener('document:keydown.tab')` types `$event` as `Event`, not `KeyboardEvent`.**
  Strict template checking rejects reading `event.key`; listen on `document:keydown` and
  branch on `event.key === 'Tab'` instead. Found 2026-10-21 on `feat/my-programs-page`.
- **A mask does not clip scrollable overflow; an oversized pseudo-element still counts.**
  `.brand-logo__sheen::before` is `inset: -100%` and the parent masks it, so it looks clipped,
  but `.home__body`'s scrollable overflow grew on any layout invalidation — opening the file
  preview was only the trigger. `overflow: hidden` on `.brand-logo__sheen` clips the box
  without changing the visible sheen. Found 2026-10-21 on `feat/my-programs-page`.
- **A structured output schema and a tool loop are not the same thing.** The chat turn used
  to ask for `{chat_name, message}` and validate the reply; the moment the model has to
  _decide_ something — start a programme, confirm a draft — a fixed schema cannot carry the
  decision, and the tool specifications become the contract. Anything the schema used to
  carry has to find another home: the chat name went to `app/naming.py`. Found 2026-10-21.
- **A tool loop does not fail the way a single completion does.** An empty answer is not an
  error the provider reports, it is an empty string, and publishing it would leave the
  methodist with a blank bubble. The turn checks the answer itself.
- **The provider's reply and the chat's transcript are two different texts.** `ChatMessage`
  keeps the machine-readable payload in `content` and the text the methodist read in
  `display_text`, and the provider has to read the second one — passing the first makes the
  model read its own JSON envelope.
- **A live test that always skips is not a test.** The `live_provider` fixture checked
  `SBER_API_KEY` after the autouse fixture had already pinned it to `mock`, so two of the
  four GigaChat live tests had never run. A fixture that needs the real credential has to
  restore it first, the way `real_provider_env` does. Found 2026-10-21.
- **A mirrored state and a row can disagree for a moment.** The mirror is written after the
  row is committed, so a reader that takes the mirror first may see the state one write
  behind. That is the right trade for an endpoint a client polls, and the wrong one for the
  pipeline, which must act on the row. Two readers, two rules: `store.load` for the
  pipeline, the mirror for the endpoint.
- **An answer that is checked against a question the run never asked is a hole.** The
  answers move a run, so they are matched against the open questions by id, every open
  question needs exactly one answer, and a duplicate is refused. Found 2026-10-21.
- **A seeded e2e row must not name the database.** The pagination spec seeded rows with
  `psql -U digradation`, a name hardcoded next to the compose project. A developer whose
  `.env` names the role differently got a bare "command failed" and three red specs, and the
  failure looked like the change under test rather than the seed. The seed now asks the
  container for its own `POSTGRES_USER` and `POSTGRES_DB`, and the script goes in on stdin
  because a `DO $$ ... $$` block cannot survive the shell quoting of `-c`. Found 2026-10-21.
- `GET /chats/programmes` returns only `session_id`, `title`, `status` and `updated_at`.
  Type, hours, tags, author and the draft text are absent; they come from
  `GET /chats/{session_id}/program`, which is a per-chat request. Do not render a catalogue
  field as if the list had it, and do not open a detail request for every card to fill one.
  The optional fields in `ProgramApiItem` are the seam for the catalogue contract growing.
  Found 2026-10-21.
- **A new page component imported eagerly can break the bundle budget even when it looks
  small.** `/chats/:id` needed the preview, stepper and question form, and importing
  `ChatPage` directly in `app.routes.ts` pushed the initial bundle past the 1 MB error
  threshold. The route now uses `loadComponent`; the build's budget is the check, not a guess.
  Found 2026-10-22 on `feat/chat-page`.
- **Web Speech `stop()` is not synchronous.** The service stops recognition and emits a final
  result later, so sending immediately after `stop()` can send the field without the last
  phrase. The composer sets `isStoppingSpeech`, waits for that final result, then reads the
  text; a plain `stop(); send()` drops words. Found 2026-10-22.
- **An absolutely positioned drawer is trapped by the first positioned ancestor.** The files
  drawer lives inside `app-chat` but must cover the preview, so `.chat-page__workspace` is the
  containing block and the chat pane and `app-chat` host stay unpositioned. Adding
  `position: relative` to either intermediate element silently confines the drawer to the
  chat column. Found 2026-10-22.
- **A new close button can make an existing Playwright locator ambiguous.** The files drawer
  gained `aria-label="Закрыть панель файлов"`, which contains the old `Панель файлов` name;
  `getByRole('button', { name: 'Панель файлов' })` then matched both the toggle and the close
  button, and strict mode fails even while the drawer keeps the close button hidden. All
  three attachment specs now pass `exact: true`. Found 2026-10-22.
- **A read-only mock makes the primary write path look like a dead button.** `/home` sends
  `POST /chats` and then `POST /chats/{id}` before navigating, so a mock that serves only
  the history list answers 404 and the redirect never happens. Any screen whose main action
  is a write needs its mock to own state, not just return fixtures. Found 2026-10-22.

- A corpus description puts the heading of the next document on the line **before** its `ID:`
  line, so an entry's extract ends where that heading starts. Reading the body up to the next
  `ID:` line therefore mixes the next document's title into this document's summary, and a
  search for "ФГОС среднего общего образования" then ranks the _основного_ general education
  standard first, because its summary contains the next document's heading.

- A run that is `approved` is terminal, so the approval of the skeleton must not set that
  status: the content is written after the approval, and `program_stage` refuses a terminal
  run before it looks at the stage. The approval therefore moves the run back to `generating`,
  and `approved` means the programme is complete — the content's last stage. A whole cycle
  test that drives the approval into the content stages is what proves it: without it the
  content half of the programme never runs and nothing fails.

- The Wikipedia API refuses a request without a `User-Agent`, and it refuses a generic one as
  well: it wants a descriptive agent with a contact in it, and it says so in the body of the 403. A request that fails with "Please set a user-agent" is answered by sending one, not by
  giving up on the search.

- The repository-level `.dockerignore` decides what the backend image holds, and it excluded
  `edu_materials/`: the image built fine until a `COPY` of the corpus failed with a path
  that looked like it did not exist. The corpus has to be copied out of the exclusion list
  when the backend starts to read it.
- A block the offline mock fabricates has to be pushed through the same schema and the same
  checks as a real one. The mock's assessment went unchecked for a long time, so a missing
  `tool_id` in a fabricated question survived: the stage refused the block, the run died
  after three attempts, and nothing in the suite failed because the mock's content stages
  were never reached.

- **HNSW refuses a `vector` column above 2000 dimensions.** `vector(2048)` with
  `vector_cosine_ops` fails during `create_all`/migration with `column cannot have more than
2000 dimensions for hnsw index`. Use `HALFVEC(2048)` and `halfvec_cosine_ops`; pgvector
  supports halfvec to 4000 dimensions. Found 2026-10-22.
- **`HalfVector` is a data helper, not the SQLAlchemy column type.** In pgvector-python
  0.3.6, `pgvector.sqlalchemy.HalfVector` expects a vector value and raises `expected ndim
to be 1` when used as `HalfVector(2048)`. The column type is the uppercase `HALFVEC(2048)`.
  Found 2026-10-22.
- **`create_all` does not install the pgvector extension.** The backend test database is
  built from `Base.metadata`, so the fixture must run `CREATE EXTENSION IF NOT EXISTS vector`
  before `create_all`; the migration does the same before creating the table. Without it the
  DDL fails before any test can report the real problem. Found 2026-10-22.
- **Importing `edu_rag` now imports SQLAlchemy's async extension.** Exporting
  `PgVectorStore` from the package makes the `greenlet` dependency load-bearing at import
  time, not only when the store is used; declare it in `rag/pyproject.toml` or the rag test
  suite fails during collection. Found 2026-10-22.
- **A cached retriever and a monkeypatched retriever cannot both be cleared.** `get_retriever`
  is `lru_cache`d, but tests that replace it with a plain fake leave no `cache_clear`
  attribute. `clear_retriever_cache` has to use `getattr(get_retriever, "cache_clear", None)`
  rather than calling the attribute directly. Found 2026-10-22.

- **A programme export is an attachment with `chat_message_id = null`.** `GET
  /chats/{id}/attachments` returns it beside the methodist's staged uploads, so a client that
  renders that list must subtract the ids in `program.exports`; otherwise `programme.docx`
  looks like a pending upload and can be deleted, leaving the programme state pointing at a
  missing file. Found 2026-10-22.
- **Padding on a `0fr` grid item keeps an animated accordion open by exactly that padding.**
  The panel collapses only its content box; `padding` and `border` still contribute height, so
  a collapsed section kept a 23px strip. Put the padding and the divider on a child of the grid
  item, and keep the grid item itself padding-free with `min-height: 0; overflow: hidden`.
  Found 2026-10-22.
- A programme block is not a pipeline stage. The preview placeholders said «Паспорт программы», «Учебный план» and «Источники» as if they were stages the client shows; the wizard names only five steps, so placeholders must point at «Каркас программы» or describe the agent's action, never invent a stage name.
- `angular.json` caps a single component's stylesheet at 10 kB (`anyComponentStyle`, error) and warns from 6. The programme preview's 700 lines sit close to it, so adding a shared utility to that file pushed the build over by 24 bytes. The fix is a global partial under `src/app/` `@use`d from `styles.scss` — the pattern `_skeletons.scss` already sets — not a raised budget. Found 2026-10-22.
