# Decisions

Architectural and design decisions, with the reasoning behind them.
Append new entries at the bottom, one `##` section per topic, chronological.

---

## Repository shape

- **`backend/` and `frontend/` are two independently installed units**: one Poetry project
  and one npm project, plus a root `package.json` that exists *only* to provide `commitlint`
  to the `commit-msg` pre-commit hook. The root project has no runtime dependencies.
- **No monorepo tool** (no npm workspaces, Nx, Turborepo, Bazel, Pants). Docker Compose is
  the sole orchestrator. The project was simple enough that workspace tooling would cost
  more than it saves, and Compose was already the required run path.
- **Each package is also its own Docker build context** (`backend/`, `frontend/`, `conf/`).
  Nothing builds from the repo root, so there is no root `.dockerignore`; each context
  carries its own. This only became possible when `processing/` went — the backend used to
  need a `COPY processing/ /processing/` from the root context.
- **`parser/` at the repo root was a dead leftover** containing only `__pycache__` directories
  from the pre-rename layout. It was never tracked in git and is gone from the working tree
  now (verified 2026-10-16). If it ever reappears, it is not a missing source directory.

## Running the project

- **The full application runs only through Docker Compose** (`docker compose up -d --build`).
  This is not laziness, it is load-bearing:
  - Redis, RabbitMQ, Garage and the TaskIQ dashboard hostnames are **hardcoded literals** in
    the backend (`redis://redis:6379`, `amqp://...@rabbitmq:5672/taskiq`,
    `http://garage:3900`, `http://taskiq_dashboard:8000`), matching Compose service names.
  - `providers/sber.py` loads `res/gigachat-ca.cer` by **relative path**, so the process
    must have CWD == `backend/`.
  - Service-to-service DNS only resolves inside the Compose network.
- **Tests, by contrast, run locally.** They use `testcontainers`, so they need a Docker
  *daemon* but not the full 13-service stack.
- **Two override files are shipped but gitignored** (`docker-compose.override.yml`):
  - `.dev` — bind mounts for hot reload, watchers with polling forced on, and publishes
    PostgreSQL on host port **5433** so Alembic can run from the host. Not 5432: a
    developer may already be running PostgreSQL there, and the host-side migration
    scripts would then silently autogenerate against somebody else's schema.
  - `.memlim` — per-service `mem_limit` values. No limits by default.

  Copying one to `docker-compose.override.yml` is an explicit, local developer decision.
  Never commit the result.

## Backend architecture

- **FastAPI with SQLAlchemy 2.0 async, not Django.** Chosen for native `async`/`await`
  end-to-end and a lighter schema layer; the team writes Pydantic directly rather than
  through an ORM.
- **No service / repository layer.** Routes query the database directly. There is no
  repository abstraction anywhere in the codebase. Adding one would be an unrequested
  architectural change, so ask first.
- **`app/deps.py` centralises every dependency behind `Annotated[...]` aliases**
  (`DbSession`, `CurrentUser`, `AdminUser`, `RedisSession`, `VerifiedMessageSession`,
  `VerifiedAttachmentId`, `S3PublicClient`, `S3InternalClient`). Call sites use the alias
  as the parameter type with **no `Depends(...)`**, which keeps route signatures readable
  and makes the dependency surface greppable from one file.
- **Rate limiting is a factory**: `user_rate_limiter(requests, per, key)` returns an async
  dependency that does Redis `INCR` + `EXPIRE` and raises `429` with `Retry-After`. Applied
  at router level via `dependencies=[...]`, not per-handler.
- **Session auth is an opaque cookie, not JWT.** `secrets.token_urlsafe(32)` stored in a
  `Session` row, delivered as an httpOnly `session_token` cookie with `samesite="lax"`,
  `secure=not DEBUG()`, 7-day lifetime. Argon2-cffi hashes passwords. Chosen because the
  token must be revocable server-side (an admin deletes a user, sessions cascade), which a
  self-contained JWT cannot offer without a deny-list.
- **`models/__init__.py` auto-imports every submodule** via `pkgutil.iter_modules` plus
  `importlib.import_module`. Alembic's `env.py` does `import app.models` once and is then
  guaranteed to see complete metadata without anyone maintaining a manual list.
- **Every cache key and every S3 key has a named builder function** in `cache.py` and
  `storage.py`. Inline f-string keys were rejected because key-format bugs are silent and
  ungreppable.
- **`app/main.py` also starts the TaskIQ broker in its lifespan**, so task declarations are
  registered when the API starts and not only when a worker does.

## Task queue

- **TaskIQ, not Celery.** Chosen for native async: the backend is async end-to-end, and
  Celery's synchronous workers plus its async Redis broker were a poor fit. The broker is
  **RabbitMQ** (`taskiq-aio-pika`, vhost `taskiq`) and results land in **Redis `/1`**.
- **Two queues, split by cause of slowness:**

  | Queue | Nature | Tasks |
  |---|---|---|
  | `default` | local / CPU-bound | `process_attachment`, `process_document`, `process_avatar`, `process_response`, `cleanup_old_results`, `cleanup_orphan_attachments`, `cleanup_stale_results`, `cleanup_expired_sessions` |
  | `network` | outbound GigaChat calls | `generate_chat_message` |

  **Why:** the `network` queue is bounded by external API latency. Isolating it means a slow
  or rate-limited GigaChat cannot starve attachment preparation or the cleanup crons.
  Separate worker pools (`worker_default` x2, `worker_network` x2) allow independent scaling.
- **`redis://redis:6379/0` is general purpose** (cache, locks, rate limits, statuses);
  **`/1` is TaskIQ result storage only.** Confusing the two silently breaks result reads.
- **Blocking work is always offloaded via `await asyncio.to_thread(...)`**: attachment
  preparation, Pillow resizing. The event loop must never be blocked.
- **Cron jobs use TaskIQ's `schedule=[{"interval": timedelta(...)}]`** with
  `LabelScheduleSource`, so the scheduler service is the only place periodic work is defined.
- **GigaChat PERS scope is rate-limited by a global Redis lock** (`api:global:lock`, timeout
  120 s, blocking timeout 60 s) taken only when `scope == "PERS"`. B2B and CORP are
  unrestricted.

## The GigaChat harness

- **`app/harness.py` owns the LLM contract**, and it has two halves that must agree:
  `SYSTEM_PROMPT` (what the model is told) and `HARNESS_STRUCTURED_SCHEMA` (what it is
  asked to return). The reply is validated against `HarnessStructuredOutput` in
  `app/providers/models.py`, so changing the schema without changing the model makes every
  reply a validation error, and the failure surfaces as a generic internal error.
- **The schema is `{chat_name, message}`.** `chat_name` exists so a session can be titled
  from the model's own words rather than staying "Новый чат" forever; `message` is the whole
  reply. `chat_name` is optional in the Pydantic model, so a model that omits it still
  yields a usable answer and the session just keeps its default title.
- **The prompt is deliberately short and domain-free.** It states the agent's job in two
  sentences and nothing about any particular subject. That is a starting point, not a
  design: the project is a chat application awaiting its purpose, and a longer prompt would
  encode an assumption about what that purpose is.
- **The prompt-injection defence is in the prompt.** The agent is told to treat instructions
  found inside uploaded files as an attack, warn the user, and decline off-topic
  conversation. The defence is therefore a soft model-level control, not a parser-level one,
  and should not be described as a security boundary.

## Provider history ends at the last user message

- **`generate_chat_message` cuts the stored `ChatMessage` history right after the last
  `UserRole.USER` row** (`_history_up_to_last_user_message`) before building the
  `list[Message]` it hands to the provider. Added 2026-10-03 for issue #59.
- **Why:** `process_response` persists the assistant's reply as a `ChatMessage` with
  `role=ASSISTANT`, so after an error that row is still the last one in the session. A
  completion request must end with something the user said; ending on the model's own
  previous answer makes the provider continue that message instead of replying. Issue #59
  reported exactly this on retry.
- **The trim lives in the worker, not in `retry_send`,** because both `send_message` and
  `retry_send` enqueue the same `generate_chat_message` task with the same session id and the
  task has no way to know which route triggered it. A fix in the route would leave the worker
  still able to send a trailing assistant turn.
- **Assistant turns before the last user message are kept.** Trimming the whole history
  would throw away the conversation the model is replying in; only the trailing turns go.
- **A history with no user message is an error**, reported through the existing
  `Invalid message session: no messages to send` result. It used to be sent to the provider
  as-is.

## Commit message length

- **Commit messages are capped at roughly 15 lines, subject plus body.** This was added
  after observing the opposite: the three large commits in this branch each ran to twenty-odd
  lines of body, and most of it re-narrated the diff or recounted the agent's own reasoning.
  `git log` is scanned, not read, so a long message costs more than it gives.
- **The rule is stated as what to leave out, not just a limit**, because a line count alone
  still permits a 15-line re-narration. A body earns its place only for a non-obvious
  constraint, a deliberate omission, or a gotcha not already in `LESSONS.md`; anything longer
  belongs in the pull request or in `.context/`. The `conventional-commits` skill carries a
  worked too-long/too-short example pair, since a rule with an example is followed and a bare
  limit is not.

## Object storage: MinIO to Garage

- **Storage is Garage, migrated 2026-10-03, because MinIO became unpullable.** MinIO was
  deleted from Docker Hub (its Hub API 404s) and then locked down on quay.io, where manifests,
  the tag list and the repo API all return 401 for every tag including `latest`. A control
  check against `quay.io/prometheus/busybox` and `quay.io/coreos/etcd` returned 200 from the
  same code path, so this was upstream action rather than a local network problem.
  `mirror.gcr.io` mirrors Docker Hub only and had nothing either. Backend CI broke because
  the testcontainer could no longer pull the image.
- **Garage was chosen because it is a real S3 implementation from a project that still
  publishes images**, not because it is a drop-in. Verified on `dxflrs/garage:v2.4.1`: every
  call the backend makes works, `generate_presigned_post` works with the exact argument shape
  both upload routes use, and `PutBucketLifecycleConfiguration` accepts the existing rule
  files verbatim. `mc` was unavailable too, since the MinIO client image lives on the same
  dead quay.io account.
- **The S3 wire protocol is unchanged, so `app/` barely moved.** Only `storage.py` changed,
  and only to rename `MINIO_*` to `S3_*` and stop hardcoding the internal endpoint. The
  migration cost was almost entirely Compose plumbing, which is the argument for preferring a
  protocol-compatible store over a feature-compatible one.
- **Credentials are pinned in the environment rather than captured at runtime.**
  `garage key create` mints a random secret that can never be displayed again, so handing it
  to the backend would need a shared volume and a startup race. `garage key import` accepts an
  explicit key id and secret, which lets `S3_ACCESS_KEY` and `S3_SECRET_KEY` stay static. The
  cost is that Garage refuses to reuse a key id with a different secret, so **rotating the
  secret requires a new key id**; `conf/garage-init.sh` verifies the stored secret matches and
  fails loudly rather than leaving the backend unable to authenticate.
- **Bootstrap is two services, not one.** `garage-bootstrap` runs the CLI for what only the
  CLI can do — key import, bucket creation, key grants — because an S3 `CreateBucket` needs
  permissions a freshly imported key does not yet have. `garage-config` runs the backend
  image to apply expiration and CORS rules, because there is no CLI command for either and the
  APIs need XML, which botocore generates from the JSON rule files we already keep. Merging
  them into one image would have meant either a Python dependency in the bootstrap image or
  hand-maintained XML.
- **Bucket CORS is configured, because Garage has no default and a browser always preflights**
  (2026-10-09). `handle_options_for_bucket` matches the `Origin`, the requested method and every
  requested header against the bucket's `cors_config`, and answers
  `403 This CORS request is not allowed.` when there is none or nothing matches. Since uploads
  and downloads go through presigned URLs, that broke every browser-side transfer. MinIO shipped
  a permissive default (`MINIO_API_CORS_ALLOW_ORIGIN=*`), which is why the migration looked
  clean in tests — nothing in the backend exercises a preflight. `garage bucket` has no `cors`
  subcommand, so `apply_s3_config.py` sets it over the S3 API next to the lifecycle rules;
  `S3_CORS_ORIGINS` carries the origins, separate from `get_origins()`, which governs the API and
  not the store. Allowed methods are the ones a presigned URL can produce (POST, GET, HEAD, PUT)
  and allowed headers are `*`, because the fields of a presigned POST form are not known when the
  rule is written. **A test asserting a preflight would return 200 belongs with the store, not
  with the backend suite.**
- **`S3_CORS_ORIGINS` defaults to `*`, and that is not a shortcut.** A wildcard origin cannot be
  combined with credentials, and these requests carry their authorisation in the presigned query
  string rather than in a cookie, so no bucket is readable by origin alone. It is documented as a
  production override rather than defaulting to the frontend URL, because the e2e stack moves the
  frontend to 18080 and a dev stack may use any port; listing origins would mean maintaining the
  same list in `.env`, the e2e workflow and any developer override.
- **The lifecycle rules stay in JSON and stay unchanged.** Garage implements only
  `Expiration` and `AbortIncompleteMultipartUpload`, which covers all three of our rules.
  Converting them to XML would make them harder to review for no gain.
- **`--single-node` is used in Compose**, which assigns the cluster layout at startup and
  removes the layout bootstrap entirely. `conf/garage.toml` records that a real deployment
  should drop the flag and raise `replication_factor` to 3 across 3+ nodes.
- **The registry risk is reduced, not solved.** `dxflrs/garage` is one small project on one
  registry and could in principle disappear the same way. The durable fix is to mirror the
  image into an organisation-controlled registry and pin by digest. That was not done here
  because it needs write access to a registry the project does not own yet.

## Documentation

- **`README.md` is the only README, and it is English.** The Russian translation
  (`README.ru.md`) was deleted on 2026-10-16: it described the quotation product and would
  have needed the same rewrite, and a second README to keep in step is a second thing to go
  stale. The product UI and the LLM prompt are Russian; the code, comments and docs are
  English. Keep new documentation in English unless it duplicates README content.
- **`AGENTS.md` documents *how*; `.context/` documents *why*.** Keeping them separate stops
  the harness from bloating with rationale and the context files from going stale.
- **The `responses={...}` convention is now repo-wide, not chat-only.** `AGENTS.md` still
  describes it as a chat-route pattern; as of 2026-10-03 (issue #44) every route in
  `auth.py`, `users.py` and `admin.py` follows it too. Two rules that came out of doing it:
  an entry that cannot be traced to a real `raise` or dependency is omitted rather than
  guessed, and the non-standard **450** from `TEST_INSTANCE_MODE` is documented explicitly,
  because it reads as a typo to anyone seeing the OpenAPI schema without the context above.
  `422` is deliberately left out of every dict, since FastAPI adds it for request validation.
- **`responses=` descriptions are inlined, not shared via constants.** Repeating the 401
  string is deliberate: each dict is read on its own in the generated schema, and a constant
  would hide which conditions a given route actually documents.

## Attachment filename filtering (2026-10-03)

- **The upload route validates the filename, not just the content type.** `POST
  /chats/{id}/uploads` checked `content_type` and `file_size` but accepted any `filename`, and
  that string is copied into the S3 key by `get_s3_attachment_key`, stored in
  `attachments.name`, handed back to the browser by the download route, and passed to
  GigaChat as the upload filename. Two rules close it, both as 400s beside the existing ones
  rather than as schema validation, so the failure stays a `HTTPException` the `responses`
  dict documents: the name must be a bare filename (no `/` or `\`, no control characters,
  which covers the NUL byte asyncpg refuses to write), and its suffix must be in
  `ALLOWED_CHAT_FILE_EXTENSIONS`.
- **The allowlist mirrors the frontend's `ACCEPTED_EXTENSIONS`, not a new policy.** `.pdf`,
  `.png`, `.jpg`, `.jpeg` are exactly what `drag-n-drop` offers, so nothing the UI can
  produce is refused. The value of the rule is that the extension in the stored key cannot be
  `.sh` or `.html` while the object is declared `application/pdf`, which is what
  `process_attachment` dispatches on. Extensions are compared case-insensitively because
  `get_s3_attachment_key` lowercases the suffix it writes.
- **The extension is *not* paired with the content type.** Enforcing `".pdf" implies
  application/pdf` would be a stronger guarantee, but nothing in the pipeline reads the
  extension — the Content-Type on the stored object is what selects the processor — so the
  pairing would add a rule with no consumer behind it. Deliberately left out; revisit if a
  consumer ever starts trusting the suffix.

## Chat list pagination (2026-10-03, issue #51)

- **`GET /chats/` returns an envelope, not a bare list.** The shape is
  `UserChatPageSchema` = `{items, total, page, items_per_page}`, with query parameters
  `page` (zero-based, `ge=0`), `items_per_page` (`ge=1, le=100`, default 20), `sort`
  (`date` or `number`) and `order` (`asc` or `desc`). **This is a breaking change** for
  every consumer, which is why all of them moved in the same commit: `ChatService.list()`
  and its two callers.
- **`total` is computed with a second query rather than by fetching `items_per_page + 1`
  rows.** The extra row is the cheaper trick for a "has more" flag, but a page counter
  needs the real count, and the count query is a `DISTINCT` over the same predicate the
  page query uses, so the two cannot disagree.
- **Sorting moved to the server, and that was forced by pagination, not chosen for taste.**
  The history page used to sort the whole array client-side, which is impossible once only
  one page is in memory: page 2 of "newest first" is not page 2 of "oldest first". A
  paginated list with client-side sorting is worse than either, because the visible order
  silently changes meaning between pages.
- **The ordering has a secondary key.** `sort=date` orders by `(timestamp, session_id)` and
  `sort=number` by `(session_id, timestamp)`. Without the tiebreaker two sessions whose
  last message shares a timestamp could swap between requests, and with `LIMIT`/`OFFSET`
  that means one session appears on two pages and another on none.
- **The sidebar asks for a fixed 10-item preview and the history page pages through the
  rest.** The sidebar column is a shortcut to recent work, not a second copy of the list,
  and it already had no way to show more than a screenful. It is a separate constant from
  `CHATS_PAGE_SIZE` precisely because it is a different question, not a different style of
  the same one.
- **`messages` pagination was deliberately left out of this issue.** `GET /chats/{id}`
  returns one append-only transcript, and the chat view already merges it with a
  2-second poll and with locally pending messages carrying negative ids
  (`mergeApiMessages`). Paging that list correctly needs either a cursor rather than an
  offset, or an "older messages" control that preserves scroll position; doing it as an
  offset alongside the existing merge would have been a second, subtler bug. The route is
  unchanged and still returns every message. Revisit as its own change.

## Attachment listing and deletion (2026-10-03, issue #47)

- **One listing endpoint reports upload state; no new state is invented.** `GET
  /chats/{id}/attachments` returns every `Attachment` row of the session, with the Redis
  `attachment:{id}:status` value in `status`. The statuses `upload_file`,
  `pdf_upload_cleanup` and `_redis_error` already write (`uploading`, `processing`,
  `completed`, `error`) are the whole vocabulary; a second, parallel notion of "what is
  uploading" would have to be kept in step with the workers for no gain.
- **`status` and `ready` are both in the response because they expire independently.** The
  Redis key has a 600 s TTL while the row does not, so a file can be uploaded, complete and
  still read as `unknown` later. `status` answers "what is happening now", `ready` answers
  "may this be sent", and the client falls back to `ready` when the status is unknown.
  An unrecognised Redis value maps to `unknown` instead of reaching the response schema,
  which would raise on a `Literal` it does not expect and turn a cache oddity into a 500.
- **The listing is not filtered to unattached files.** `chat_message_id` is returned instead,
  so one request can drive a whole file view; the frontend filters. Server-side filtering
  would make the endpoint useless for showing what a session holds.
- **Deleting clears the ownership cache entry, which is what makes a repeat delete fail.**
  `verify_attachment_id` caches `attachment:ownership:{session}:{id}` for 10 minutes, so
  deleting the row alone would leave the dependency answering for an id that no longer
  exists, and `GET .../attachments/{id}` would serve a presigned URL for a deleted file. The
  handler therefore drops the status, URL and ownership keys together with the row.
- **Storage deletion is best-effort and never blocks the row deletion.** `_s3_try_delete`
  from `app.tasks.files` logs and swallows a failure, which is what a delete wants: an object
  that already expired must not leave the row behind. The 7-day lifecycle rule on the
  `attachments/` prefix is the backstop for anything the best-effort path misses.
- **The delete removes derived artifacts too.** An attachment owns
  `ProcessingResultUploadable` rows, one per file the service derived from it, each pointing
  at its own object. Deleting only `attachment.s3_key` would leave those behind.
- **The delete reuses `_s3_try_delete` across modules rather than growing a second copy.**
  `app.routes.chat` imports it from `app.tasks.files` alongside `process_attachment`. The
  underscore says "not part of that module's API", which is true and is the trade being made:
  duplicating the helper would let the two drift on what a delete does about a failure.
- **Known limitation: deleting an attachment mid-processing races the worker.** A worker that
  already read the row can still write a status key for the deleted id. Nothing serves it,
  because the ownership entry is gone and `verify_attachment_id` refuses the id, and the key
  expires with its TTL. Making it airtight would mean a tombstone the workers consult; not
  worth it for a file the user has just removed.
## Admin system prompt editing (2026-10-06)

The admin settings page edits the system prompt over `GET/PUT /api/v1/admin/system-prompt`
with body `{ "prompt": string }`. **Superseded** by "Admin-editable system prompt
extension" below, which shipped the same idea on the singleton settings row instead of a
dedicated endpoint.

- **The contract reuses the admin router** (`prefix="/admin"`, `require_admin` dependency) so
  authorization is a one-line `include_router` away and cannot leak to non-admins.
- **The frontend disables the editor until the prompt loads** instead of showing a blank
  editable field: saving a blank over an unknown prompt would silently wipe the real one.

## Users form as a slide-over panel (2026-10-09)

The "Новый пользователь" / "Редактирование пользователя" card on the admin users page is no
longer a second grid column: it is an `<aside>` that slides in over a host grid animating
`1fr 0fr` to `1.5fr 1fr`, the same mechanism as the "Результаты расчёта" panel in the agent
chat (`isResultsOpen`, host class binding, opacity/visibility staging, reduced-motion opt-out).

- **A slide-over, not a modal**, because the table stays visible and interactive behind the
  form, and no overlay/focus-trap machinery is needed — the pattern already exists in the
  codebase, so it is copied rather than reinvented.
- **The panel closes on save, cancel and the X button**, all through `closeForm()` (which
  resets the form). `selectUser()`/`resetForm()` stay pure form logic so existing callers and
  specs keep working; `openCreate()`/`openEdit()` only add the open step.
- **The open panel track carries a length floor so it fits its content.** A bare `1fr`
  track is `minmax(auto, 1fr)`, and the aside's `min-width: 0` (needed for the `0fr` collapse)
  lets the track shrink past the 32px panel titles — "Редактирование пользователя" spilled
  ~176px past the card at 1200px viewport, and the chat's "Результаты расчёта" wrapped to two
  lines for the same reason. The floor makes the table/chat side absorb the squeeze (both
  scroll internally); verified by Playwright measurements on the dev stack, not by eye.
- **The floor is a length inside `minmax()`, not `max-content`.** Applied instantly,
  `min-width: max-content` kills the slide, and animating it needs `interpolate-size`, which
  Safari lacks — the panel popped to full width there. `minmax(0, 1fr) minmax(0px, 0fr)` opening
  to `minmax(0, 1.5fr) minmax(600px, 1fr)` (440px in chat) interpolates as plain lengths
  everywhere; sampled frame widths ramp smoothly in both Chromium and WebKit
  (82→109→424→540→591→595). 600px fits the longest panel title; below 900px the users grid
  stacks and the floor is dropped.
- **The aside is `overflow: visible` only when open** (hidden while closed for the animation):
  the role `app-select` renders its dropdown as inline `position: absolute`, which a permanent
  `overflow: hidden` would clip. Below 900px the host stacks to one column and the closed
panel is `display: none`, otherwise its invisible content would hold open an empty grid row.

## Admin-editable system prompt extension (2026-10-09, issue #64)

- **`SYSTEM_PROMPT` is persisted per session, so the extension is composed at chat
  creation and frozen for that conversation's life.** `create_chat` is the only place that
  reads the prompt, storing it as the session's `SYSTEM` `ChatMessage`. Composing it there, via
  the pure `build_system_prompt(extension)`, means a settings change applies to new chats and
  never rewrites one in progress. The alternative — injecting the current prompt when the
  worker builds history — would let a conversation change rules midway, which is worse than a
  prompt being stale for its duration. `SYSTEM_REMINDER`, which used to sit next to
  `SYSTEM_PROMPT` and was never imported anywhere, was deleted in the same change: a second
  prompt constant that looks load-bearing is worse than no constant.
- **Storage is a singleton row, enforced by a check constraint.** `settings` holds one row with
  `id` fixed to `SETTINGS_ROW_ID = 1` and `CheckConstraint("id = 1")`, so "singleton" is a
  database invariant rather than a convention every query has to remember. A key/value table
  was rejected because each option would need its own accessor, validation and audit handling,
  and the type would be a guess at read time; a typed column per option arrives as a migration.
  Reading the value is a single indexed primary-key lookup per chat creation, so nothing is
  cached and there is no invalidation to get wrong.
- **Both admin tiers may edit it, and it is not blocked by `TEST_INSTANCE_MODE`.** Unlike the
  account mutations, this write holds no credential and no ownership, and an empty string undoes
  it, so the 450 gate would protect nothing while making a shared demo instance unable to tune
  its prompt. The absence of a 450 on `PUT /admin/settings` is deliberate; every other
  `POST`/`PATCH`/`DELETE` under `/admin` has one.
- **The audit columns are nullable.** `last_update_by` is a FK with `ON DELETE SET NULL`, not
  `RESTRICT`, so deleting an admin keeps the configuration still in force and drops only the
  attribution; `last_update_at` is null until the first write, because "never set" and "set to
  the empty extension" are different states and only one of them has a timestamp. `GET` therefore
  reports nulls rather than 404 on an untouched instance, since there is one settings row
  conceptually and it simply holds no value yet.
- **Reset is an empty string, and an explicit `null` is rejected.** The payload fields
  are optional so the next option can be added without disturbing this one, which makes "field
  omitted" and "field is null" both reachable — and they mean opposites, so `null` is a 422, the
  same treatment `AdminEditUserSchema` gives `role` and `username`.
- **The preamble exists to separate the admin's text from the base prompt, not to constrain it.**
  It labels the block, so the prompt reads as base instructions followed by an appended
  extension rather than as one undifferentiated mass. Nothing stops an admin from writing
  anything, and that is accepted: admin-authored prompt text is a prompt-injection surface
  by design, bounded only by the admin tier — accounts that can already reset passwords.
- **`MAX_PROMPT_EXTENSION_LENGTH` is 8000, which is generous on purpose.** The bound exists to
  stop a pasted document being appended to every prompt the instance sends. The server
  `strip()`s what it stores, so trailing whitespace never becomes a dangling heading, and a
  whitespace-only value is treated as unset by `build_system_prompt`.

## Review fixes to the settings API (2026-10-09, issue #64)

- **Two concurrent first saves no longer 500.** The singleton row is created by the first
  write, so two admins saving together both read "no row" and both insert `Settings(id=1)`;
  the loser took a primary-key `IntegrityError` and lost its change to a 500. The loser now
  rolls back, adopts the row the winner inserted and re-applies its own update, so the last
  write wins — the same result as two sequential saves. If the competing transaction rolled
  back too, there is nothing to adopt and the request answers 409 rather than pretending to
  have saved.
- **`admin.id` and the timestamp are read before the rollback, not inside the recovery path.**
  A rollback expires every instance in the session, so touching `admin.id` afterwards triggers
  an implicit refresh that async SQLAlchemy refuses with `MissingGreenlet`. `_apply_settings`
  therefore takes the id and the stamp as plain values, which also means nothing in it can reach
  the database.
- **`AdminSettingsUpdate` forbids extra fields.** The likeliest mistake in this payload is
  misspelling a key. With extras allowed the key would be ignored, nothing would be set, the
  audit fields would be stamped anyway and the caller would get a 200 that changed nothing — a
  silent no-op on a settings screen. Rejecting extras turns it into a 422 naming the key.

## Frontend settings page synced to the unified settings endpoint (2026-10-10)

- **The two provisional frontend endpoints never existed on the backend.** The page was built
  against `GET/PUT /admin/system-prompt` (`{prompt}`) and `GET/PUT /admin/parameters`, while
  the backend shipped the unified `GET/PUT /admin/settings`. The service now exposes
  `getSettings()` plus `updateSettings(patch)` and each card saves a partial body, which the
  backend's `model_fields_set` handling supports. This supersedes the "backend route is still
  pending" notes in the two earlier frontend sections above; those describe a contract that was
  never implemented.
- **The old shapes would have failed, not degraded.** `extra="forbid"` rejected the unknown
  keys with 422, so the mismatch surfaced as errors rather than silent drift. No compatibility
  shim was added: both sides are owned by this repo and moved together.
- **The form refuses to save a blank prompt extension.** `canSave` requires the trimmed
  value to be non-empty, so whitespace-only input never reaches the server as a no-op
  write. The backend still treats `""` as the reset (reachable with a direct request), but
  the UI offers no "clear the extension" path: an accidental select-all plus save must not
  silently drop the configured prompt. The "don't wipe an unknown value" rule is still kept
  separately by disabling the editor until the settings load.
- **`MAX_PROMPT_EXTENSION_LENGTH` is 8000 on both sides.** The page previously capped at
  20000, which would have passed values the server rejects with 422. The counter threshold
  moved with it (7000), since the old 15000 could never be reached under an 8000 cap.

## Shared table component (`app-table`) (2026-10-10)

The admin users table and the requests history table are visually identical, so the
styling was extracted into `frontend/src/app/shared/components/table/` instead of being
duplicated per page.

- The component is deliberately **dumb about data**: it renders `TableColumn<T>[]` and
  rows, and only *reports* sort intent through the `sort` model — it never sorts itself.
  The users page sorts client-side (its data is one unpaginated list), the history page
  re-queries the server (`sort`/`order` on `GET /chats`), and both keep their comparators.
- Custom cells (avatars, badges, action buttons, links) are projected via
  `ng-template[appTableCell="<column key>"]` with the row as `$implicit`; text-only cells
  use `TableColumn.text`.
- `interactiveRows` opts a table into clickable, hover-highlighted rows (history); the
  users table stays non-interactive. Class names `th.sortable` / `.th-content` are kept
  from the pre-refactor markup because both page specs select them.

## Repurposed from a quotation engine to a chat application (2026-10-16)

The project was an AI quotation engine for sheet-metal fabrication: drawings in, a priced
commercial offer out. It is now a bare chat application with file attachments, awaiting a
purpose. Every commercial concept is gone. What is recorded here is why each piece went,
and why what replaced it is there.

- **The LLM contract is `{chat_name, message}` and nothing else.** `positions` and `gen_kp`
  existed only to carry a calculation result to the workbook generator. With no workbook,
  they were dead weight in the schema, in the prompt and in the Pydantic model. Structured
  output stays rather than dropping to free text, because the reply is stored as an
  assistant message and read back through a schema, and because `chat_name` has to be
  separable from prose.
- **The `processing` package is deleted outright** (PDF rasteriser, DXF parser, Excel
  calculator) rather than left unused. It was the only reason the backend built from the
  repository root; with it gone each package is its own Docker context, which is simpler
  and keeps test files out of the image.
- **The migration history is reset to a single `initial` revision generated from the new
  models**, rather than edited to drop the two dead pieces. This is only safe because no
  instance exists yet. The reason to prefer regeneration over a hand-written `drop table`:
  `settings.parameters` was a JSONB blob and `processing_results` was a table, and a squashed
  initial has neither to describe. **Once any instance has real data, this is no longer
  available** — from that point migrations are append-only and dropping a table means a new
  revision.
- **`processing_results` is dropped rather than kept as an artifact store.** Its only writer
  was the DXF parser. A table with no writer is not an extension point, it is dead schema
  that every future query has to know is always empty.
- **`_prepare_for_llm` in `tasks/files.py` is a deliberate pass-through.** It is the seam
  where per-content-type processing belongs, and it returns its input unchanged because
  nothing needs such a step today. It is kept rather than deleted because the pipeline is
  meant to be the example to build on: the alternative is a worker whose only job is to
  hand bytes to the provider, with nothing marking where the interesting work would go. Its
  docstring says so, and a test pins that it does not silently alter bytes.
- **`process_pdf` and `process_image` were merged into one `process_document`.** They had
  become byte-identical apart from their names once rasterising went. Keeping two would have
  meant two copies of the same body to keep in sync, and a dispatch that selected between
  them on nothing. `process_attachment` still allowlists content types, which is where a
  genuinely different preparation step would be added.
- **`PUT /admin/settings` became `PATCH`.** The body was already partial
  (`model_fields_set`), so PUT was a claim the endpoint did not honour: it replaced a
  resource it only partly accepted. PATCH says what it does, and it lets the next setting be
  added without a client ever having to learn it exists. The concurrent-first-save recovery
  is unaffected.
- **The product is named `di:gradation` and the repository `digradation`.** The rename was
  mechanical (`digraduation` -> `digradation`, `di:graduation` -> `di:gradation`) across ~35
  files. Two things are worth remembering: the e2e Compose project name moved with it
  (`digradation-e2e`), because Compose derives volume names from it and the specs hard-code
  it in several places; and the PostgreSQL role and database name moved with it, so after the
  rename Compose mounts an empty volume rather than the developer's old one. Both are written
  up in `LESSONS.md`.
- **The published PostgreSQL port moved from 5431 to 5433.** Both host-side migration
  scripts set `POSTGRES_HOST=localhost`, so 5432 would have meant autogenerating against
  whatever PostgreSQL the developer happens to be running — producing a migration for
  somebody else's schema, silently.

## Frontend design-token palette (2026-10-17)

The palette moved to indigo `#6366F1` as `--color-main`, with `--color-accent` (`#8B5CF6`,
identity/gradients), `--color-info` (`#22D3EE`, process steps/info icons) and a
`--color-surface-dark` (`#151728`) reserved for sidebars and underlays. Success, warning and
danger are `#10B981` / `#F59E0B` / `#EF4444`. All tokens live in `styles.scss`; components
reference variables only, so no component needed a hex.

- **`--color-hover` is derived, not stored.** It was two hardcoded hexes (a darker blue in
  light, a lighter one in dark). It is now `color-mix(in srgb, var(--color-main) 85%, black)`
  in light and `... 80%, white)` in dark — the only asymmetry the two themes need, and it
  keeps hover in step with whatever `--color-main` becomes.
- **Four near-duplicate tokens were removed rather than retuned.** `--color-hover-secondary`
  (a gray almost identical to `--color-secondary`), `--color-outgoing-message` (a blue tint
  that assumed the old blue), `--color-success-text` (a second green, one use) and the
  never-defined `--color-info` are gone. Usages now read `--color-secondary`, a
  `color-mix(... --color-main 12%, --color-surface)` tint, `--color-success` and the new
  `--color-info` respectively. Fewer tokens than semantics is the point: a hex that exists
  only because the primary one changed is not a token.
- **Dark theme overrides only `--color-main` (`#818CF8`) and `--color-hover`.** The accent,
  info and status hues are legible on both backgrounds, so only the primary needed a lighter
  dark-theme value; the rest inherit from `:root`.
- **`--color-info` now feeds notifications.** `notification.component.scss` had a
  `var(--color-info, var(--color-main))` fallback for a variable that was never defined, so
  info toasts silently rendered as primary. The token exists now and the `data-type='info'`
  rule uses it directly.

## Dark sidebar via a dedicated token layer (2026-10-17)

The sidebar is now always dark (`--sidebar-bg`, which maps the palette's
`--color-surface-dark` `#151728`) in both themes, instead of following the light surface.
A dark subtree cannot reuse the light-on-light globals — `--color-text` is near-black — so
it gets its own explicit `--sidebar-*` group in `styles.scss` rather than the scoped
override of the generic tokens that was tried first.

- **The first attempt overrode the generic tokens on `:host`** (`--color-text`, `--color-main`
  and friends) and relied on custom-property inheritance to re-theme the whole subtree. It
  worked but read as a lie: inside the sidebar `--color-text` meant "light text", which
  nothing in the name says. Replaced with named `--sidebar-*` tokens and direct references.
- **Shared mixins gained parameters, not forks.** `app-scrollbar` and `icon-button` now take
  their colors as arguments with the old values as defaults, and `.button--main` reads
  `--button-main-bg` / `--button-main-bg-hover` with `--color-main` / `--color-hover` as
  fallbacks. Every existing caller is untouched; the sidebar passes its own colors. The
  alternative — overriding generic values at `:host` — is what we removed.
- **The skeleton is the one place a scoped override remains.** The history skeleton is a
  shared child component that reads `--color-skeleton-base` / `--color-skeleton-shimmer`
  directly, and custom properties are the only channel into it without `::ng-deep`. `.sidebar`
  retargets just those two to `--sidebar-skeleton-*`. It is scoped to the sidebar element and
  named so the intent is obvious.
- **Only `--color-danger` stays generic inside the sidebar.** Red reads the same on light and
  dark, so the logout hover does not need a sidebar variant.
