# AGENTS.md

> **Mandatory harness for any AI or human agent working in this repository.**
> Read this file in full before making a change. It describes how this project
> actually works, not how it ideally would.

**Repo:** `digradation` — chat application with file attachments
**Product language:** Russian (all UI strings, LLM prompt)
**Code language:** English (identifiers, comments, docs)

---

## 0. Non-negotiable rules

These five rules override any other instinct you have.

| # | Rule |
|---|---|
| 1 | **All Python dependencies are added via `poetry add`.** Never hand-edit `pyproject.toml` dependencies, never write a `requirements.txt`. Use `poetry add <pkg>` (runtime) or `poetry add --group dev <pkg>` (dev) from `backend/`. Commit the updated `poetry.lock` with it. |
| 2 | **All frontend dependencies are added via `npm install`.** From `frontend/`, use `npm install <pkg>` (runtime) or `npm install -D <pkg>` (dev). Commit `frontend/package-lock.json` with it. Never hand-edit dependency versions in `package.json`; let npm resolve them and write the lockfile. |
| 3 | **The full project can only be run via Docker.** `docker compose up -d --build`. There is no supported local run of the application: the backend expects service hostnames (`db`, `redis`, `rabbitmq`, `garage`) and resolves `res/gigachat-ca.cer` relative to `backend/` as CWD. Never claim the app works without stating how you started it in Docker. |
| 4 | **Tests may be run locally.** `poetry -C backend run pytest`, `npm --prefix frontend test`. Backend tests need a running Docker *daemon* (they use testcontainers) but not the full stack. Testcontainers fixtures must **not bind-mount host paths**: on Windows Docker Desktop prompts to share the directory, and a dismissed prompt fails the run with a `pywintypes` error at fixture setup. Use `with_copy_into_container` and `with_tmpfs_mount`. |
| 5 | **Update `.context/` as you work.** See [section 6](#6-context-directory). A change with no recorded decision or lesson is a change the next agent will re-litigate. |
| 6 | **Never destroy Docker volumes you did not create.** No `docker volume prune`, no `docker system prune`. The named volumes hold real state — the PostgreSQL database can contain a developer's accounts, chats and uploaded attachments, and you cannot see whether it does. `docker compose down -v` is acceptable for a stack you brought up yourself in this session; ask first when in doubt whose data it is. `docker compose down` on its own is always fine. |

---

## 1. Repository layout

```
digradation/
├── backend/            FastAPI service + TaskIQ workers     owner @yel-gar
├── frontend/           Angular 21 SPA                        owner @krchvl
├── rag/                edu_rag package: chunking, embeddings, pgvector store, retrieval
├── parser/             edu_parser package: source PDFs/docx -> document.json
├── e2e/                Playwright suite against the Compose stack
├── conf/               infra config: redis.conf, garage.toml, garage init, lifecycle rules, pg init
├── scripts/            pre-commit shims for per-package Poetry tools
├── .context/           decisions / lessons / project state
├── .agents/skills/     task-specific playbooks
├── docker-compose.yaml
└── .pre-commit-config.yaml   single source of truth for hooks
```

There is **no monorepo tool**: no npm workspaces, no Nx, no Bazel. Orchestration is
Docker Compose only. The root `package.json` holds *only* commitlint (for the
`commit-msg` hook); the real frontend dependencies live in `frontend/package.json` with
its own lockfile.

---

## 2. Backend conventions

**Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 (async), asyncpg, Alembic, Pydantic v2,
TaskIQ, structlog, argon2-cffi, aioboto3, Pillow.

**3.14-only syntax is expected.** `requires-python = ">=3.14,<4"` and
`target-version = ["py314"]` for both black and ruff. In particular, PEP 758 makes
`except ValueError, TypeError:` (no parentheses) valid, and black *emits* that form. Do not
"fix" it; adding parentheses fights the formatter.

### Layering (there is no service/repository layer)

```
routes/  ->  deps.py  ->  models/ + schemas/  ->  tasks/  ->  providers/
```

Routes talk to the database directly. Do **not** introduce a repository layer unless the
user explicitly asks for one.

### Naming

| Thing | Convention | Example |
|---|---|---|
| Route handler | `<domain>_<action>` | `admin_get_users`, `send_message` |
| Router | `APIRouter(prefix=..., tags=..., dependencies=[...])` | `chat.py`, `users.py` |
| Pydantic schema | `*Schema` for payloads, `*Request` / `*Response` otherwise | `UserSchema`, `MessageResponse` |
| Cache key | `get_*_key(...)` in `cache.py`, never inline f-strings | `get_generation_key(uuid)` |
| S3 key | `get_*_key(...)` in `storage.py`, never inline f-strings | `get_attachment_key(id)` |
| Model constants | defined next to the model that uses them | `MAX_USERNAME_LENGTH = 32` |
| Enum | `StrEnum` | `UserRole`, `GenerationResultType` |

### The Annotated dependency idiom

`app/deps.py` defines `Annotated[...]` aliases used **directly as parameter types**,
with no `Depends(...)` at the call site:

```python
from app.deps import AdminUser, DbSession

@router.post("/users/{user_id}/password")
async def admin_set_password(user_id: int, admin: AdminUser, db: DbSession):
    ...
```

Existing aliases: `DbSession`, `CurrentUser`, `AdminUser`, `RedisSession`,
`VerifiedMessageSession`, `VerifiedAttachmentId`, `S3PublicClient`, `S3InternalClient`.

Rate limiters are **factories** returning a dependency:

```python
router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(user_rate_limiter(3, timedelta(minutes=5), "user:avatar"))],
)
```

### Async, honestly

Everything is `async`. **CPU-bound and blocking work must be offloaded with
`await asyncio.to_thread(...)`**: attachment preparation in `tasks/files.py`, Pillow
resizing, anything you add to `_prepare_for_llm`. Never block the event loop.

### Docstrings are part of the API contract

Chat routes carry a multi-line docstring **and** a `responses={...}` dict documenting
401 / 403 / 404 / 409 / 429. Keep this pattern when adding endpoints.

### Models

Every model file starts with `from __future__ import annotations` (SQLAlchemy needs it),
uses `Mapped[]` / `mapped_column`, and sets `nullable` explicitly.
`models/__init__.py` auto-imports every submodule via `pkgutil` so Alembic always sees the
full metadata. **Never add manual imports to it.**

### Logging

`structlog` only: `log = structlog.stdlib.get_logger(__name__)`. Bind context per task
with `log = log.bind(attachment_id=...)`. Never use `print()`.

---

## 3. Frontend conventions

**Stack:** Angular 21.2 standalone, signals, TypeScript 5.9 strict, SCSS.

### Folder tiers

| Folder | Holds |
|---|---|
| `core/` | singletons: `services/`, `guards/`, `interceptors/`, `models/` |
| `features/` | route-level feature areas (`chat/`) |
| `pages/` | routed pages (`home-page/`, `programs-page/`, `profile-page/`, `admin/`) |
| `shared/` | reusable components and utils |

### State

No NgRx or Redux. **Signals only**: `signal()`, `computed()`, `effect()`,
`afterRenderEffect()`, `toSignal()`, `takeUntilDestroyed()`. Services are
`@Injectable({ providedIn: 'root' })`. Cross-component handoff uses small one-shot "state
services" (`set()` / `consume()`).

A list that reloads from a user action guards against out-of-order responses with a local
request-id counter (`const requestId = ++this.lastRequestId`) and drops a stale response that
no longer matches, rather than assuming responses arrive in order.

### Templates and styles

- Selector prefix **`app-`** (kebab-case), enforced by ESLint. Directives use `app` camelCase.
- `ChangeDetectionStrategy.OnPush`.
- One `.component.scss` per component, referenced via `styleUrl` (singular).
- Theming via **CSS custom properties**, never hardcoded colours. Light/dark under
  `:root` and `html[data-theme='dark']`.
- BEM-ish modifiers: `.button--main`, `.card--shadow`, `.input--error`.
- `@use '...' as *` with relative paths and **no file extension**.

### Rendering HTML

- **`[innerHTML]` is the only HTML an Angular *template* binds, and it is the safe one**
  because Angular sanitizes the binding. `bypassSecurityTrust*` is used nowhere; do not
  introduce it.
- **It is not the only HTML sink, and do not treat it as one.** The file preview
  (`file-preview.component.ts`) appends a rendered `.docx` to the DOM imperatively, with no
  template binding and no sanitizer anywhere on the path. Anything else that builds DOM by
  hand is a sink too, and carries the same duty: a new sink must harden what it inserts and
  say so in a comment, rather than assuming the sanitizer covers it.
- **Call `DomSanitizer.sanitize(SecurityContext.HTML, …)` explicitly** at the boundary where
  untrusted text becomes markup — the message bubble does, and the spreadsheet preview sanitizes
  before it builds nodes. It is a second pass over the same sanitizer, so it changes nothing at
  runtime; it exists so the sanitization is visible in the source, because a reviewer reading
  `innerHTML` cannot tell an Angular binding from a raw assignment.
- **A shadow root is how a rendered document's styles are confined.** `docx-preview` builds a
  stylesheet from the document's own style and font names and interpolates their values into
  rules unescaped, so a crafted name opens a global rule. The preview renders into a shadow
  root, where no rule can reach the app around it; removing the stylesheet instead would leave
  the document unstyled. A shadow boundary is not a sandbox for scripts — links still need
  their `href` checked, which `hardenRenderedDocument` does.
- The message text is untrusted twice over: the model writes the assistant's half, and the methodist's
  own half is echoed back from the API.

### API layer

Interceptor order is load-bearing: `credentialsInterceptor`, `errorInterceptor`, then
`mockApiInterceptor` **last**. The mock answers instead of calling `next()`, so if it is
outermost the other two never run in mock mode. `createInterceptors()` in `app.config.ts` is
the single source of the order and is unit-tested.

Services build URLs as `` `${environment.apiUrl}/chats` ``. `apiUrl` comes from
`src/environments/environment*.ts`, injected at image build time by
`docker/rewrite-env.mjs` from the `BACKEND_URL` build arg. **Never hardcode a backend
URL.** Auth is cookie-based; `credentialsInterceptor` sets `withCredentials: true`.

### Polling, not WebSockets

Chat results poll `GET /chats/{id}/result` every 2 s with a 5 min timeout
(`POLL_INTERVAL_MS`, `POLL_TIMEOUT_MS` in the chat component). Upload status polls every
2 s up to 150 attempts (`AttachmentUploadService`). Respect these constants.

### Heavy deps are lazy

`docx-preview`, `xlsx` and `hyperformula` are **dynamically `import()`ed** inside
`file-preview.component.ts` so they never enter the eager bundle. Keep new heavy
dependencies lazy too.

---

## 4. Docker and environment

### Bringing the stack up

```bash
cp .env{.example,}                      # then fill in required values
docker compose up -d --build
docker compose logs -f
```

Dev hot-reload (this override also publishes PostgreSQL on port **5433**, which the
host-side migration scripts need — never 5432, which a developer may already be
using for their own PostgreSQL):

```bash
cp docker-compose.override.yml{.dev,}
docker compose up -d --build
```

The resulting `docker-compose.override.yml` is **gitignored**. Never commit it.

### Hardcoded service hostnames

`redis://redis:6379`, `amqp://...@rabbitmq:5672/taskiq`, `http://garage:3900` and
`http://taskiq_dashboard:8000` are literals in the source. Do not parameterise them
without being asked; it would break the Docker-only run model this project relies on.

### Object storage is Garage

Storage is [Garage](https://garagehq.deuxfleurs.fr), an S3-compatible server. It replaced
MinIO in October 2026, because MinIO was deleted from both quay.io and Docker Hub and became
unpullable from any registry. The S3 wire protocol is unchanged, so `app/storage.py` and every
route are unaffected; what differs is the bootstrap.

| What | Where |
|---|---|
| Server config | `conf/garage.toml` (secrets come from the environment, never from this file) |
| Key import, buckets, grants | `conf/garage-init.sh`, run by the `garage-bootstrap` service |
| Lifecycle rules | `conf/garage-rules/*.json`, applied by `backend/apply_s3_config.py` |
| Bucket CORS rules | set by the same `apply_s3_config.py`, origins from `S3_CORS_ORIGINS` |
| Bootstrap image | `conf/garage.Dockerfile` — busybox with the garage binary copied in |

Five things about Garage are not obvious and will cost you an hour if you rediscover them:

- **The image has no shell.** Not `/bin/sh`, no `curl`, no `mc`. The old `mc`-based init
  pattern is impossible, which is why `garage.Dockerfile` exists. Note the copy direction:
  busybox is the base and the garage binary comes in from upstream. Lifting binaries *out* of
  busybox into the Garage image yields a shell that fails to exec.
- **`--single-node` assigns the cluster layout at startup.** There is no separate layout
  bootstrap, and no `replication_factor` to satisfy by hand.
- **Access keys must be imported, not created.** `garage key create` generates a random
  secret that can never be displayed again, so credentials could not be handed to the
  backend. `garage key import` accepts a fixed id and secret, which is what lets
  `S3_ACCESS_KEY` and `S3_SECRET_KEY` stay static in the environment. Garage will not let a
  key id be reused with a different secret, so **rotating the secret also requires a new key
  id**.
- **`GARAGE_RPC_SECRET` and `GARAGE_ADMIN_TOKEN` must be exactly 32 bytes of hex** — 64
  characters. Garage refuses to start otherwise, so generate those two with
  `openssl rand -hex 32`, not the `-hex 48` used for every other secret in `.env`.
- **There is no default CORS policy, and `OPTIONS` on an existing bucket is answered
  against its CORS config alone.** A browser talking to a presigned URL always preflights,
  so a bucket with no CORS configuration is unusable from a browser: you get
  `403 This CORS request is not allowed.` on every upload and download. MinIO shipped a
  permissive default, so this only surfaced after the migration. `garage bucket` has no
  `cors` subcommand; the rules go over the S3 API from `apply_s3_config.py`.

There are three buckets, and the list is declared in three places that must agree:
`conf/garage-init.sh` (creates them), `backend/apply_s3_config.py` (applies their rules
and CORS), and `backend/tests/conftest.py` (the testcontainer).

| Bucket | Holds | Retention |
|---|---|---|
| `avatars` | normalised profile pictures | unprocessed uploads expire after 1 day |
| `uploads` | files a user attaches to a chat | expire after 7 days |
| `artifacts` | files the service derives from uploads | expire after 1 day |

Garage implements `Expiration` and `AbortIncompleteMultipartUpload` only, which is all
this project uses; it has no ACL API at all, and buckets are private unless a key is
granted access.

### One CWD-sensitive path

| Path | Consumer |
|---|---|
| `res/gigachat-ca.cer` | `providers/sber.py`, relative path, requires CWD == `backend/` |

The Dockerfile handles this with `WORKDIR /app`. Follow the same pattern for any new
resource file.

### Build contexts

`backend` builds from the **repository root**, because its image needs both
`backend/` and the sibling `rag/` package. The root `.dockerignore` excludes
everything else; the Dockerfile copies `backend/pyproject.toml`,
`backend/poetry.lock` and `rag/` before `backend/`. `frontend` builds from
`frontend/` and the Garage bootstrap from `conf/`; each of those contexts carries
its own `.dockerignore`. Do not move the backend back to a `backend/` context
without also vendoring or publishing `edu_rag`.

### Build caching is CI-only

`docker-compose.build-cache.yml` carries the BuildKit `type=gha` cache settings and is
added as a third `-f` file by `e2e/scripts/setup.sh` **only when `CI` is set**. The `gha`
backend can only work inside an Actions job, so a dev stack has nothing to gain from it. It
needs both a Buildx builder (`docker/setup-buildx-action`) and the `ACTIONS_*` variables
that authenticate against the Actions cache service. Those variables exist in an *action's*
environment, not in a `run` step, which is why `e2e.yml` also runs
`crazy-max/ghaction-github-runtime` before `setup.sh`. Without them Buildx drops the
`type=gha` entries **silently**: the build succeeds and no cache is read or written, so
check the log for an `importing cache manifest from gha` line rather than assuming.

All services built from `backend/Dockerfile` — `garage-config` included — share
`scope=backend`, or they would each miss on each other's layers. `garage-bootstrap` builds
`conf/garage.Dockerfile` and has its own scope.

Compose 2.38+ delegates `build` and `up --build` to Bake by default, and `e2e.yml` sets
`COMPOSE_BAKE=true` explicitly anyway: Bake is the code path that carries `cache_from` /
`cache_to`, and the classic builder ignores both, so the fallback has to be impossible.

### `.env` is secret

Never commit `.env`; use `.env.example`. Generate secrets with `openssl rand -hex 48`,
except `GARAGE_RPC_SECRET` and `GARAGE_ADMIN_TOKEN`, which need `openssl rand -hex 32`.

---

## 5. Quality gates

Run all of these **before** proposing a change is done.

### Backend

```bash
poetry -C backend run black --check .      # line-length 120, py314
poetry -C backend run ruff check .         # select F,E,W,I,N,UP,B,ASYNC,C4,PIE,T20,Q,RET,SIM,ARG,PTH,RUF
poetry -C backend run pytest               # needs Docker daemon (testcontainers)
poetry -C backend run pytest --cov         # with a branch-coverage report
```

### Rag package

```bash
poetry -C rag run black --check .
poetry -C rag run ruff check .
poetry -C rag run pytest                   # the pgvector store test needs Docker
poetry -C rag run pytest --cov             # gate lives in rag/pyproject.toml
```

### Coverage

Backend coverage is measured with `pytest-cov` and **enforced at 90%**. The threshold
lives in exactly one place, `fail_under` in `backend/pyproject.toml`, which pytest-cov
reads and enforces.

| Where | What it does |
|---|---|
| `backend/pyproject.toml` | `[tool.coverage.*]` config, including `fail_under = 90` |
| `.pre-commit-config.yaml` | `pytest-coverage` hook, **pre-push** stage |
| `.github/workflows/backend-ci.yml` | runs the suite with `--cov`, uploads `coverage.json` |

**To change the gate:** edit `fail_under` in `backend/pyproject.toml`. Nothing else names
a percentage. Verified to bite: at `fail_under = 90` the run exits 0, at `99` it exits 1.

The `rag/` package has the same gate in `rag/pyproject.toml`; read its `fail_under` there
rather than copying the figure into another file.

**There is one test run, not two.** The workflow that used to run a second full suite just
to produce a coverage report is deleted; `backend-ci.yml` collects coverage in the same run
that runs the tests, and uploads the artifact from there.

Branch coverage is on (`branch = true`) and `source = ["app"]`. Coverage artifacts
(`.coverage*`, `coverage.json`, `coverage.xml`, `htmlcov/`) are gitignored. No current
figure is recorded here — read it off the `TOTAL` line.

### Provider selection and tool calling

`SBER_API_SCOPE` selects the provider in `app/providers/containers.py`:

| `SBER_API_SCOPE` | Provider | Talks to |
|---|---|---|
| `PERS` / `B2B` / `CORP` | `SberProvider` (`sber.py`) | GigaChat, SDK-free `httpx` |
| `openai` | `OpenAIProvider` (`openai.py`) | any OpenAI-compatible endpoint via the `openai` SDK |
| `mock` | `MockProvider` (`mock.py`) | nothing |

`SBER_API_KEY=mock` still selects the mock regardless of scope, which is what the test and
e2e stacks set. The OpenAI-compatible client takes its token from `SBER_API_KEY`, its
endpoint from `OPENAI_URL` (required when the scope is `openai`; there is no implicit public
endpoint) and its model from `OPENAI_MODEL`.

`OpenAIProvider` and `SberProvider` implement tools; each advertises the offered set on every
request and maps the model's requests onto `GenerationResponse.tool_calls`. A provider may be
constructed with a default set, and a caller narrows it per turn with `generate(..., tools=)`.
`mock` accepts `tools=` and scripts a call from it only in `stage` mode.

The hub can deliver GigaChat's native call as message text — `content='<|function_call|>{...}'`
with `tool_calls=None` — so `OpenAIProvider` normalises that marker into a `ToolCall` before
anything above it sees the reply. The payload may carry junk after the JSON object, so the
first complete value is decoded and the tail is dropped. Nothing from the marker on ever
leaves the provider, even when the payload does not parse: the token is a control token, and
the hub answers a request that carries one with `400 Raw model control token in input`, which
would otherwise turn a nudge into a failed attempt. `SberProvider` talks to the v2 wire shape
directly and needs none of this.

The two real providers use different wire shapes, and the internal model hides the difference:

- **OpenAI**: `tools[].function` with `arguments` as a JSON *string*.
- **GigaChat v2** (`/v2/chat/completions`): `tools[].functions.specifications[]` plus
  `tool_config.mode`, and the reply nests `function_call` inside `content[]` with
  `arguments` as a JSON *object*. `SberProvider` normalises both back to `ToolCall`.

The container passes `TOOLS` to every provider and it is **empty on purpose**: every turn
names the set it offers. The chat intake offers `INTAKE_TOOLS`, a programme stage offers
`stage_tools(stage)`, and the difference is load-bearing — a stage that saw the intake tools
could start a second run mid-document, and a union would advertise four same-named
`save_program_draft` specs, one per block schema. `SberProvider` rejects `strict=True` tools
at construction because GigaChat v2 has no strict mode; the OpenAI client honours `strict`.

### The exports are attachments

A finished programme is written to the attachments bucket as a DOCX document and a PPTX file,
registered as attachments of the chat, and their ids are recorded on the programme's state.
`GET /chats/{session_id}/program` carries them in `exports`, and the client fetches them
through the attachment route it already uses for a file the methodist uploaded. There is no
download endpoint of their own, and no bucket was added for them.

### The mock provider

`SBER_API_KEY=mock` (or `SBER_API_SCOPE=mock`) selects `app/providers/mock.py` instead of the
real GigaChat client.
It performs no network I/O, which is what lets the worker, route and e2e suites run
without credentials and without LLM cost. Its behaviour is set by `MOCK_PROVIDER_MODE`:

| Mode | Behaviour | Used to exercise |
|---|---|---|
| `ok` (default) | a well-formed reply and a chat name | the happy path, e2e |
| `empty` | a response with no content | "Provider did not respond properly" |
| `error` | returns `None` | the backend's error path |
| `stage` | scripts `begin_program` when that tool is offered, and the stage's own `save_program_draft` block otherwise | the programme stages end to end, once a run exists |

Output is deterministic: the reply derives from a hash of the conversation, so the same
input always yields the same text. The `stage` mode's fabricated programme is one fixed
document sized to the volume the brief names, because the pipeline validates the blocks
against each other and a block that disagrees fails the run.

### The sources a programme may cite

`app/agentic/sources.py` is the catalogue: it reads the corpus in `edu_materials/` — the
federal educational standards, the professional standards and the programmes of the
organisation — and turns each corpus description into a record the programme can cite.

| What | Where |
|---|---|
| The corpus | `edu_materials/<corpus>/` — the documents and the description of each |
| The catalogue | `app/agentic/sources.py` — `entries()`, `get_source()`, `search()` |
| The path | `EDU_MATERIALS_PATH`, defaulting to the checkout's `edu_materials` |
| The image | `backend/Dockerfile` copies the corpus to `/edu_materials` |

- **A source is confirmed when the catalogue holds it, and only then.** The backend sets
  `Source.confirmed`, not the model: a document the catalogue does not know is kept in the
  programme and marked as unconfirmed, in the report, in the chat reply and in the DOCX.
- **A professional standard a competence rests on is a refusal**, because a competence has to
  stand on a standard that exists. A bibliography entry may be unconfirmed.
- **The search is lexical** — a keyword match over the descriptions, with the title counting
  double. The semantic index over the corpus arrives with the vector store; the seam is
  `search()`, and both answers are the same shape.
- **The online search is `app/agentic/websearch.py`.** The corpus search is lexical and offline;
  the web search asks the encyclopedia by default (`WEB_SEARCH_URL` points it at a search
  service of the deployment's own). It is off on the mock provider, so the end-to-end suite
  never needs the internet. What it finds is marked as unconfirmed: the corpus alone confirms
  a document.

### The programme pipeline

The sequential educational-programme cycle is the reference implementation for any future
multi-stage job. `app/agentic/state.py` defines `ProgramState`, the envelope over the
`ProgramDraft` blocks plus the methodist's requirements, the volume, per-stage status,
attempts and `run_id`; `app/agentic/store.py` is its only writer, and `to_draft()` is the
completeness gate that a partial state cannot pass.

- **One TaskIQ task owns one stage**, in the order of `STAGE_ORDER` (competences, hours,
  sources, summary, assessment, materials). A stage commits its block and only then enqueues
  the next task or `finalize_program_draft`. There is no loop over stages inside a task, and
  that is what makes a worker death stop at a durable boundary.
- **The programme is written in two halves.** `SKELETON_ORDER` (competences, hours, sources,
  summary) is the skeleton: the goal, the competences and the hours the methodist approves.
  When the skeleton is complete the run waits in `pending_approval` and `finalize_program_draft`
  renders it; the content (`CONTENT_ORDER`: assessment, materials) is written only after the
  methodist has approved it, and the approval is what puts the first content stage on the
  queue. Nothing is generated from an unapproved skeleton.
- **The state is `chat_sessions.program_state` (JSONB)**, mirrored to
  `program:state:{session_id}` in Redis for `PROGRAM_STATE_TTL`. PostgreSQL is canonical and
  a mirror that cannot be written never fails a task.
- **Three guards, not one.** The chat row lock orders simultaneous writers, the version on
  the envelope refuses a writer that loaded the row long ago, and a per-stage Redis lock
  (`PROGRAM_STAGE_LOCK_TTL`) keeps a slow stage from overlapping its own recovery. The lock is
  renewed before every provider turn with a Lua compare-and-expire, so a stage may run longer
  than the TTL, and is released with a Lua compare-and-delete, never a plain `DEL`.
- **The worker session is `app.database.db_session()`.** `store` and the task bodies take it
  from there; `tsq_db` in `app/tasks/conf/broker.py` is only an alias for the imports that
  predate it. The row lock is explicit: `async with db_session() as db, db.begin():`.
- **`program_stage` refuses a task from another run** by comparing the `run_id` in its
  argument with the row, and replays a completed stage by handing over to its successor
  instead of regenerating the block.
- **`recover_stalled_programs`** runs every five minutes, skips a stage whose lock is alive,
  and re-enqueues the stage a stalled run stopped on, spending an attempt. When the budget is
  spent the run is failed and the chat is told, so a stage that kills its worker cannot cycle.
- **`finalize_program_draft`** assembles the draft, renders it with `render.py`, renames a
  default-named chat, and writes the state, the result and the assistant message in one
  transaction. A state whose blocks cannot produce a valid draft is failed, not retried.
- **A stage sends exactly one system message.** GigaChat rejects a second one, so the stage
  prompt is the tool contract and the conversation is `STAGE_SYSTEM_PROMPT` plus the brief.
  The admin `prompt_extension` from `harness.build_system_prompt` does not reach programme
  stages; carrying it there would mean storing it in `ProgramState` from the chat worker's
  intake turn. That is a deliberate decision, recorded in `.context/DECISIONS.md`.
- **A stage does not take prose for a block it has not saved.** `run_tool_loop` accepts a
  `complete` callback, and `program_stage` passes `lambda: ctx.saved`. While it says no, a text
  answer is kept as the model's turn and followed by a user nudge instead of ending the stage,
  because a refused call is otherwise answered with "I fixed it" and no second call. The intake
  passes nothing: a refused intake call is a legitimate answer to the methodist. A truncated
  reply (`finish_reason=length`) is still refused before the nudge.

### Source retrieval (RAG)

The source-citing programme stages can search a pgvector index of the curated corpus. The
feature is off unless `RAG_ENABLED` is truthy, so a fresh checkout starts without a hub or an
index.

- **Embeddings come from the school hub, not GigaChat.** Real indexing and retrieval require
  `SBER_API_SCOPE=openai` plus `OPENAI_URL`; `PERS`/`B2B`/`CORP` have no `/embeddings`
  endpoint. `mock` is preferred when `SBER_API_KEY=mock`, and returns deterministic vectors
  without a socket.
- **The vector is fixed at 2048 dimensions.** The column is `HALFVEC(2048)`, not
  `vector(2048)`: HNSW refuses more than 2000 dimensions for `vector`, while `halfvec`
  supports 4000. `OPENAI_EMBEDDING_DIMENSION` must be `2048`; changing the model dimension
  needs a migration, not just an environment edit.
- **The catalog supplies citation ids.** `backend/res/rag-sources.json` maps stable `src-NN`
  ids to parser `source_path`s. `backend/app/rag/catalog.py` validates it; the parser's own
  `doc_id` is a content hash and is not a citation id.
- **Ingestion is the one-off CLI `backend/index_rag.py`.** Run it in the backend image with
  parser output mounted at `/materials/parsed`. It is idempotent by fingerprint and supports
  `--dry-run`, `--prune`, `--strict` and `--batch-size`. There are no HTTP indexing or search
  endpoints yet.
- **`search_sources` is offered only by `stage_tools(stage)`**, never by the chat intake.
  A hit is mapped to its catalog `Source`. An empty result is `found=false`; a provider or
  database failure is `RagUnavailable`, which the tool turns into a note rather than failing
  the stage.
- **The catalogue answers when the index does not.** The corpus descriptions in
  `edu_materials/` are the same documents in prose, and they need neither an index nor an
  embedding provider, so a deployment without one still finds its documents. The two
  catalogues describe the same ten documents, and a document has one id: the corpus entry is
  filed under the catalogue's `src-NN` id.
- **The score floor is measured, not guessed.** On this corpus an unrelated query scores about
  0.18 and a related one about 0.44, and the two distributions do not overlap, so the floor
  is 0.30. A lower floor admits exactly the junk the measurement excludes.
- **A source the catalogue does not hold is kept and marked**, not refused: the programme
  shows that nobody verified the document. A professional standard a competence rests on is
  still a refusal, because a competence has to stand on a standard that exists.
- **The backend and rag test suites need a Docker daemon.** The PostgreSQL testcontainer is
  `pgvector/pgvector:pg18`, and the backend fixture runs
  `CREATE EXTENSION IF NOT EXISTS vector` before `Base.metadata.create_all`. See
  `.context/LESSONS.md` for the HNSW and `HALFVEC` traps.

### The chat turn and the programme API

`app/tasks/api.py` runs one turn of a chat. The turn is a **tool loop**, not a single
completion: the model is offered `INTAKE_TOOLS` and may call `begin_program` (which starts a
run), `approve_program_draft` (which confirms a rendered draft), `revise_program` (which asks
for one part to be redone), or simply answer in prose.
`run_chat_turn` is the task; `process_response` publishes the answer.

- **The methodist can ask for one part to be redone.** The intake offers `revise_program` with
  the stage to redo and the notes: the stage runs again with the methodist's notes in its
  brief, the blocks that are already committed stay, and the run goes straight back to where
  the methodist left it instead of walking the pipeline again. The revision resets the stage's
  budget, and the run it queues is the first of the three attempts: a change is not a failure,
  but a stage that keeps failing while it is redone still has a limit.
- **The model no longer names the chat.** `HARNESS_STRUCTURED_SCHEMA` and
  `HarnessStructuredOutput` are gone: the reply is prose, and the name comes from
  `app/naming.py`, which holds the one rule — *a programme's title wins, otherwise the first
  thing the methodist said* — used by the intake, the finalise and the failure path alike.
- **A run may wait for answers.** `begin_program` takes an optional `questions` list; a run
  with questions is created in `AWAITING_ANSWERS` and does not generate until the methodist
  answers. The questions live on the envelope, so the client can show them one at a time.
- **The answers travel with the message.** `POST /chats/{session_id}` takes an `answers`
  array next to `content`; every open question needs exactly one answer, and an answer to a
  question nobody asked is refused with 400. The answers are repeated in the message next to
  the question each one answers, so the transcript reads as a conversation.

The endpoints the client uses:

| Endpoint | What it answers |
|---|---|
| `POST /chats/{session_id}` | sends a message, and optionally the answers to the open questions |
| `GET /chats/{session_id}/result` | the last turn's reply, and the open questions |
| `GET /chats/{session_id}/program` | where the programme stands: status, stage, the blocks saved so far, the self-check report |
| `POST /chats/{session_id}/program/approve` | approves a rendered draft: releases the content stages and records who approved |
| `POST /chats/{session_id}/program/revision` | asks for one part of the programme to be redone, with the notes |
| `GET /chats/programmes` | the user's programmes, paginated with `page` and `items_per_page` |
| `GET /chats/{session_id}/program.docx` | the programme as a DOCX document |
| `GET /chats/{session_id}/program.pptx` | the slides of the programme as a PPTX file |

The approval and the revision are the methodist's own decisions, and the model can reach the
same two through its intake tools. The rules that decide whether a decision is allowed live
once, in `app/agentic/actions.py`, and both surfaces call them: a rule written twice is a
rule that will drift. Both endpoints answer with the programme's state, in the shape
`GET /program` answers with, and refuse what the tools refuse — 404 when the chat holds no
programme, 409 when the decision is not allowed right now.

**The methodist takes the decision in the chat.** While `status` is `pending_approval` and
`busy` is false, `app-program-decision` replaces the composer the way the question form does,
and offers an approval and a revision whose picker lists the stages whose status is
`completed`. The blocks come from `programDecision` in `features/chat/program-view.ts`, and
the two requests are `ChatService.approveProgram` / `reviseProgram`. A 409 is a rule of the
programme, not a bug: show `detail` as it is and refresh the programme. Only the approval gate
asks anything — a finished programme keeps the composer, and a content revision after the
approval goes through the model's `revise_program`, which the methodist reaches by asking.

`GET /program` reads the Redis mirror first and falls back to the row, so a client that
polls it does not touch PostgreSQL; the fallback re-mirrors the state. The response carries
the state's `updated_at` and `version`, so a client can see that it is looking at an older
state — a mirror write that fails while Redis is up leaves the endpoint behind until the
next successful write.

**A client polls on `busy`, not on `running`.** `busy` is true for as long as the programme
is being worked on, the finalisation included, and false once the run is waiting for the
methodist or has stopped. `running` is only a live hint — it is true while a worker holds
the current stage's lock, so it is false while a stage waits in the queue and false during
the finalisation, which has no lock at all. `GET /result.running` is a different thing
entirely: it is the chat turn's key, and it is released while the stages still run.

**The view a client sees is built in `app/agentic/view.py`**, not in the route: the status
projection (a run that died reads as failed), the blocks that are already saved and the open
questions are the programme's rules. `store.load_cached(db, session_id)` reads the mirror
first and falls back to the row through the caller's session, because a route reads inside
its own transaction.

**A chat is named once, and the name is the programme's title when it has one.** Until the
title exists the chat takes the name of the first thing the methodist said, so a running
programme is not "Новый чат" in the chat list; when the draft is rendered the title replaces
it. `app/naming.py` holds the rule and the three callers use it.

### Live API tests

Tests marked `live` hit a real provider API and **are deselected by default** via
`addopts = "-m 'not live'"`, because they cost money. Run them deliberately:

```bash
SBER_API_KEY=<real-key> SBER_API_SCOPE=PERS poetry -C backend run pytest -m live
```

The OpenAI-compatible client's live tests carry a second marker, so with
`SBER_API_SCOPE=openai OPENAI_URL=... OPENAI_MODEL=...` they can be selected alone:

```bash
poetry -C backend run pytest -m 'live and openai'
```

The autouse `env` fixture pins `SBER_API_KEY=mock` for the normal suite; the
`real_provider_env` fixture restores whatever the process was started with, which is how a
live test sees a real credential at all. Both provider suites carry a full model -> tool ->
model round through `run_tool_loop`, which is the only test that can prove the real API
accepts the `function_result` and `tool_state_id` shape.

### End-to-end (Playwright)

The e2e suite drives the real Compose stack with **`SBER_API_KEY=mock`**, so the whole
request path is exercised while the LLM itself is offline and free.

```bash
./e2e/scripts/run.sh              # bring the stack up, seed it, run the suite
./e2e/scripts/setup.sh            # stack and seed only
cd e2e && npx playwright test --ui
```

| What | Where |
|---|---|
| Specs | `e2e/tests/*.spec.ts` |
| Config | `e2e/playwright.config.ts` |
| Stack override | `docker-compose.override.yml.e2e` (forces `SBER_API_KEY=mock`, moves the frontend to port 18080, publishes Garage's S3 port on 13900 so the browser can reach the presigned URLs) |
| Seed + stack | `e2e/scripts/setup.sh` (creates the `e2e` superuser, clears rate limits) |
| CI | `.github/workflows/e2e.yml` |

**The e2e stack runs under its own Compose project, `digradation-e2e`,** and names its
override with `-f` rather than copying it over `docker-compose.override.yml`. Both are
load-bearing, see [`.context/LESSONS.md`](.context/LESSONS.md). In short: Compose prefixes
named volumes with the project name, so sharing the project name would put the suite's
seeded users and chats into the developer's own database; and copying the override meant a
later `docker compose` command in the repo silently recreated the developer's containers
with the e2e ports.

Four constraints worth knowing before adding a spec:

- **The suite runs as one user, and the backend limits chat creation and message sending
  to 5 per minute per user.** The helper fixture clears the `ratelimit:*` counters in Redis
  before each test and `workers: 1` keeps the tests serial. Adding a chat-creating test
  without those will produce a 429 and a test that fails with a confusing "did not
  navigate" error.
- **The mock provider names every chat the same** (`Чат (mock)`), so identify a
  session by its URL id, not by its title.
- **Every blocking wait is bounded, and a failed cleanup fails the fixture.** The config
  sets the global test, expect, action and navigation ceilings; `waitForRequest` /
  `waitForResponse` / `waitForEvent` calls also pass their own `timeout`. Docker calls in
  the helpers use `execFileSync` with a `timeout`, because Playwright cannot interrupt a
  synchronous subprocess: without one, a stuck `docker compose exec` can outlive the test
  timeout. The rate-limit clear retries within 15s and the idle-generation scan within 45s,
  then throws rather than letting the next test report a misleading 429 or 409; retry waits
  use `setTimeout` so the event loop stays free.
- **A seeded programme is a real run, so the recovery cron will end it.** `recover_stalled_programs`
  fails any non-terminal run untouched for ten minutes, so a seeded status decays within minutes:
  seed a run that is terminal, waiting, or `finalized` (see `setup.sh`). `program-decision.spec.ts`
  opens the seeded `pending_approval` programme, which is `finalized` with a committed skeleton for
  exactly this reason, and it answers `POST /program/approve` and `/program/revision` locally —
  a real revision starts a stage, and the offline provider cannot save one, so the run would fail
  and the programme would stop being a draft for the rest of the suite. **An export needs three
  things, not one**: the attachment row, the object in `uploads`, and the ids on the state — a
  presigned URL for an object that was never stored answers 404, which the UI reports as a button
  that does nothing. `program-exports.spec.ts` downloads both forms of the seeded `approved`
  programme for that reason.

### Frontend

```bash
npm --prefix frontend run lint             # ng lint (eslint, ts + html)
npm --prefix frontend run format           # prettier --write src
npm --prefix frontend test                 # vitest via @angular/build:unit-test
npm --prefix frontend run build            # prod build; also typechecks
```

### Frontend coverage

Coverage uses `@vitest/coverage-v8`, driven by the `@angular/build:unit-test` builder's
**native** `coverage` options. There is no `vitest.config.ts` in this project and there
should not be one: the builder owns the Vitest configuration and hard-requires
`@vitest/coverage-v8` when `coverage` is enabled.

```bash
npx ng test --coverage --watch=false      # or: npx --prefix frontend ng test --coverage
```

Config lives in the `test` target of `frontend/angular.json` (`coverage`,
`coverageReporters`, `coverageInclude`, `coverageExclude`). Reporters are `text-summary`,
`json-summary` and `html`; output lands in `frontend/coverage/frontend/`, which is already
gitignored.

**`coverage` is already `true` by default on that target,** so a plain `npx ng test` collects
coverage and enforces the thresholds without a `--coverage` flag. Passing it is harmless and
explicit, but the CI workflow does not need it. There is one test run, not two: the separate
coverage workflow is deleted and `frontend-ci.yml` uploads the report from the run it already
performs.

**To change the gate:** edit the `coverageThresholds` object on the `test` target in
`frontend/angular.json`. It is already set for statements, functions and lines, and the
builder enforces it and exits non-zero, so no percentage is hardcoded in the hook or the
workflow. Verified to bite: at `statements: 90` the run exits 0, at `95` it exits 1.
**Branches are deliberately not gated.**

`coverageInclude` counts untested files at 0%, so the reported figure is honest rather
than flattering. No current figure is recorded here — run the command above and read it.

> **Test setup:** `src/test-setup.ts` is registered through the builder's `setupFiles`
> option. It installs an in-memory `localStorage` / `sessionStorage` because the jsdom test
> environment exposes neither, which `ThemeService` needs at construction time. Without it
> every spec touching the sidebar fails. Do not add defensive `typeof localStorage` checks
> to application code to work around this.


### Migrations are mandatory

Any change to `app/models/` **must** ship a migration. `tests/test_migrations.py` runs
`alembic upgrade head`, `downgrade base` and `alembic check`, and fails if the models
drift. Generate one with:

```bash
./backend/makemigrations.sh "short description"   # or makemigrations-local.ps1 on Windows
```

Both scripts need the dev override in place, because they reach PostgreSQL from the
host on **5433**. They start `db` if it is not already running and stop it again on the
way out.

Note that `alembic/versions/` is **excluded** from black and ruff. Do not reformat
generated files.

**Postgres enum types are not owned by the column.** `drop_table` drops the column,
not the type, so a `downgrade base` that does not also drop the enum leaves it behind
and the next `upgrade head` fails on `CREATE TYPE ... already exists`. Every migration
that introduces an enum must drop it in `downgrade()` with `checkfirst=True`.

---

## 6. `.context/` directory

`.context/` is the project's memory. **Every meaningful change updates it.** Three flat
files, no subdirectories:

| File | Contains | Write an entry when |
|---|---|---|
| `.context/DECISIONS.md` | Architectural and design decisions with the **why**. One `##` section per topic, appended chronologically. | You choose an approach, change a convention, or set a non-obvious rule (for example why a queue is split, why a column is nullable, why a status code is unusual). |
| `.context/LESSONS.md` | Traps, gotchas and non-obvious behaviours discovered the hard way. A flat bullet list, newest at the bottom. | You hit something surprising, lost time to it, or found a fix whose mechanism is not obvious from the diff. |
| `.context/PROJECT_STATE.md` | Where the project stands right now: what is done, what is in flight, what is next. Rewritten in place. | You complete a feature, change the roadmap, or finish a batch of work. |

Rules for writing them:

- **Record the reason, not just the outcome.** A future agent reading "use TaskIQ" learns
  nothing; reading "TaskIQ because Celery's async Redis broker blocked the event loop"
  prevents the same mistake.
- **Do not duplicate what the code already says.** `AGENTS.md` documents *how*;
  `.context/` documents *why it is that way*.
- **One bullet, one lesson.** Long paragraphs get skimmed and skipped.
- **Append** to `DECISIONS.md` and `LESSONS.md`; **rewrite** `PROJECT_STATE.md` wholesale
  so it cannot go stale.
- **Include a date or commit** so entries can be aged out later.
- **Never record a test count or a coverage percentage.** Not the number of tests, not
  `99.33%`, not "across 353 tests", not a threshold either. Every one of those is
  deterministic: `pytest --cov` and `ng test --coverage` print them in seconds, so a
  recorded copy is stale the moment it is written and wrong by the next commit, and
  nobody can tell which by reading the file. A test count in particular looks like a
  quality signal when it is really just a diff against the previous commit. This applies
  to `PROJECT_STATE.md`, to `DECISIONS.md`, to `LESSONS.md` and to this file.

  Record instead **where the gate lives and how to read it**, which is the part that is not
  obvious and does go stale: `fail_under` in `backend/pyproject.toml`,
  `coverageThresholds` on the `test` target in `frontend/angular.json`. State the
  *configuration* and the *command*; leave the *result* to the tool that just produced it.

  If a coverage number appears in a historical lesson, keep it only where the number is
  the point — for example "an edit measured mid-run reported a collapsed gate", which is
  about the mechanism rather than about the figure.

---

## 7. `.agents/skills/` directory

Task-specific playbooks live in `.agents/skills/<skill-name>/SKILL.md`, each with YAML
frontmatter (`name`, `description`) and progressive disclosure through a `references/`
subfolder for detail that is only sometimes needed.

### Provenance and precedence

Eight skills are project-specific and were written from this repository's source:
`sqlalchemy-async`, `taskiq-workers`, `python-testing`, `frontend-file-preview`,
`docker-compose`, `e2e-playwright`, `parallel-issues`, `conventional-commits`. They encode
digradation's actual conventions.

Two are **third-party** and installed from public registries:

| Skill | Source | License |
|---|---|---|
| `fastapi` | [`samuelpkg/skills`](https://github.com/samuelpkg/skills) | MIT |
| `angular` | [`xfstudio/skills`](https://github.com/xfstudio/skills) | see `metadata.json` |

Each third-party skill ships a **`references/digradation-overrides.md`** that states where the
generic guide conflicts with this project. **Precedence, highest first:**

1. `AGENTS.md` and `.context/DECISIONS.md` — this project's rules
2. `references/digradation-overrides.md` inside a skill — project-specific divergences
3. The skill's own `SKILL.md` — generic framework guidance
4. Your own general knowledge

Where a generic guide and this project disagree, the project wins. Read the overrides file
before writing code against `fastapi` or `angular`.

**Exception to the no-emoji rule:** the upstream `SKILL.md` bodies of the two third-party
skills are kept **verbatim** so they can be re-installed or diffed against their source
repos. The Angular guide contains a few checkmark and cross marks. Everything authored for
this repository, including both `digradation-overrides.md` files, is emoji-free.

| Skill | Origin | When to load it |
|---|---|---|
| `fastapi` | third-party | Writing or changing routes, dependencies, schemas, response models |
| `sqlalchemy-async` | project | Touching ORM models, sessions, queries, or migrations |
| `taskiq-workers` | project | Adding or changing a background job, queue assignment, or schedule |
| `python-testing` | project | Writing tests or fixtures, or debugging test failures |
| `angular` | third-party | Writing components, services, signals, templates, or styles |
| `frontend-file-preview` | project | Working on the docx/xlsx/pdf/image preview and its lazy dependencies, or the upload flow |
| `docker-compose` | project | Changing services, Dockerfiles, env plumbing, or the dev overrides |
| `e2e-playwright` | project | Writing or debugging an end-to-end spec, or changing the e2e stack override |
| `parallel-issues` | project | Running several issues at once via worktrees and subagents |
| `conventional-commits` | project | Writing a commit message or naming a branch |

**Load the relevant skill before doing the work it covers.** If a skill is missing or
inaccurate, fixing it as part of your change is expected, not out of scope.

The reviewer agent, which assesses a change before it lands, is at
`.opencode/agents/reviewer.md`. It is `mode: subagent`, so it runs in a child session rather
than as a main agent, and its permissions deny editing while allowing the project's own gates
to be run. Both that path and `opencode.json` are **gitignored**, so the agent and any OpenCode
config are local-only and reach neither other developers nor CI.

Markdown agents in `.opencode/agents/` are discovered automatically, so no `opencode.json`
entry is needed; that file exists only for agents you define in JSONC.

When you fix a defect, ask whether the reviewer should know about it: if the trap is new, add
it, otherwise the reviewer keeps re-discovering the same thing.

### Parallel issue workflow

The orchestrator agent delegates issues to worker subagents that each work in a **separate
`git worktree` on a separate branch**, then merges once they report ready. The point is to keep
concurrent edits to different issues from colliding in one working tree.

1. The orchestrator branches each worker off the integration branch and gives it a worktree
   outside the main checkout.
2. The worker implements the issue, runs the **full** test suites for every package it touched,
   and ends its last commit message with `(closes #NN)`. That is what makes GitHub close the
   issue on merge, so it must be in the **final** commit, not an earlier one.
3. The worker launches the `reviewer` subagent and has it review the branch against the
   integration branch — `git diff <integration-branch>...HEAD` from inside the worktree.
4. If the review finds issues, the worker fixes them and reviews again. A second review whose
   findings are **only nitpicks** may be dismissed; nitpicks are only worth fixing when they
   arrive alongside something more serious.
5. The worker reports ready for merge.
6. The orchestrator waits for **all** workers to finish, then merges one at a time and resolves
   conflicts. One at a time, because resolving two conflicting merges at once is how context gets
   lost.

Every agent, orchestrator included, runs the full test suites. See
`.agents/skills/parallel-issues/SKILL.md` for the worktree commands, the conflict-resolution
order, and the traps that this workflow has actually hit.

---

## 8. Commits and branches

**[Conventional Commits](https://www.conventionalcommits.org/)**, enforced by
`commitlint.config.js` through the `commit-msg` pre-commit hook.

```
<type>(<optional scope>): <subject>
```

Examples:

```
feat(backend): add pagination to the admin user list
fix(frontend): sliding pill indicator stuck on a hidden xlsx sheet
docs: explain the two task queues
refactor!: drop the legacy parser shim
```

| Type | Use for |
|---|---|
| `feat` | new capability |
| `fix` | bug fix |
| `refactor` | behaviour-preserving change |
| `perf` | performance |
| `docs` | documentation only |
| `test` | tests only |
| `build` / `ci` | dependencies, workflows, Docker |
| `chore` | housekeeping |
| `!` after type or scope, plus a `BREAKING CHANGE:` footer | breaking change |

Observed scopes: `backend`, `frontend`, `ci`. Branch prefixes in use: `jut/`,
`feat/`, `fix/`, `docs/`, `feature/`.

### Keep commit messages short

**A commit message is a log entry, not a report.** The subject is one line under ~72
characters; subject plus body should fit in roughly **15 lines**. If it needs more, the
detail belongs in the pull request description, or in `.context/` when it is a decision or a
lesson.

Do not re-narrate the diff, list the tests you added, or recount your own reasoning
("I first tried X, then switched to Y"). A body earns its place only for a non-obvious
constraint that forced the design, a deliberate omission, or a gotcha not already recorded in
`.context/LESSONS.md`. Keep it to three or four lines and point at the record instead of
duplicating it.

### No commit metadata beyond the message

The commit message is the **only** metadata allowed. Never add these, and never accept them
from a tool that appends them automatically:

- `Co-Authored-By:` trailers, or any other attribution trailer
- `Generated with ...` style footers, with or without a robot emoji
- Signed-off-by, Reviewed-by, or any other `git interpret-trailers` entry
- `--author` overrides or `--trailer` flags passed to `git commit`
- `git commit --amend` used to inject attribution

If a template, hook or assistant suggests appending attribution, drop it. Authorship and
history are already recorded in git itself: the commit author, the branch and the PR link.

Commitlint validates the message only, so none of this is machine-enforced. It is a rule you
follow.

---

## 9. Things that will bite you

1. **`providers/containers.py` constructs the provider at import time.** Importing any
   task or route module requires `SBER_API_KEY` and `SBER_API_SCOPE` to exist. The tests
   set them at the top of `conftest.py`, *before* any `app` import, for exactly this reason.
1. **`SBER_API_KEY=mock` (or `SBER_API_SCOPE=mock`) means no LLM call happens.** If you are
   surprised by a deterministic-looking response in tests or e2e, that is the mock provider,
   not a real model. Use `MOCK_PROVIDER_MODE` to change its behaviour.
2. **The app must run with CWD == `backend/`.** See the CWD-sensitive paths in section 5.
3. **The LLM contract is the prompt plus the tools.** `harness.py` holds `SYSTEM_PROMPT` and
   `PROMPT_EXTENSION_PREAMBLE`; the chat turn runs a tool loop, so what the model must
   produce is the tool specification in `app/agentic/tools.py`, not a fixed output schema.
   There is no output schema any more. Admins append their own text to the prompt through
   `PATCH /admin/settings`, and `build_system_prompt` composes it once per session at
   creation time.
4. **`TEST_INSTANCE_MODE` blocks admin mutations with HTTP 450**, except `PATCH
   /admin/settings`, which is allowed on purpose. This non-standard code will look like a
   typo in any frontend `switch`. It is intentional.
5. **`_prepare_for_llm` in `tasks/files.py` is a deliberate pass-through.** It is the seam
   where per-content-type processing goes, and it returns its input unchanged because
   nothing needs one today. Do not "implement" it speculatively, and do not delete it
   either: it documents where that work belongs.
6. **The frontend suite runs in CI** via `npx ng test --watch=false` in
   `frontend-ci.yml`. `--watch=false` is passed explicitly because the builder defaults watch
   mode to `true` in TTY environments; without it an interactive run hangs.
7. **Pre-commit Python hooks never fail the commit.** `scripts/precommit-*.py` call
   `subprocess.run(..., check=False)`. Rely on the explicit `black --check` and
   `ruff check` commands instead.
8. **There is no service layer.** If you want to inject a repository, ask the user first.

---

## 10. Pre-submission checklist

- [ ] Dependencies added with `poetry add` / `npm install`, lockfiles committed
- [ ] `black --check` and `ruff check` clean in every touched Python package
- [ ] `pytest` green in every touched Python package (backend gate: 90% coverage)
- [ ] `poetry -C backend run pytest --cov` run if backend code changed; still at or above 90%
- [ ] `ng lint`, `ng build` and `vitest` green if the frontend was touched
- [ ] `npx ng test --coverage` run if frontend code changed; still at or above 90%
- [ ] Migration added if any ORM model changed (`alembic check` proves it)
- [ ] If backend behaviour changed: did the offline mock provider still model it? (`MOCK_PROVIDER_MODE`)
- [ ] No secrets, no committed `.env`, no committed `docker-compose.override.yml`
- [ ] `.context/DECISIONS.md`, `LESSONS.md` and `PROJECT_STATE.md` updated as warranted
- [ ] Commit message follows Conventional Commits, and is **under ~15 lines**: no re-narration
      of the diff, no list of added tests, no account of your own reasoning
- [ ] No commit metadata beyond the message (no `Co-Authored-By`, no generated-with footer)
- [ ] Any new skill or convention recorded in `AGENTS.md` or `.agents/skills/`
