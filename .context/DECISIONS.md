# Decisions

Architectural and design decisions, with the reasoning behind them.
Append new entries at the bottom, one `##` section per topic, chronological.

---

## Repository shape

- **`backend/` and `frontend/` are two independently installed units**: one Poetry project
  and one npm project, plus a root `package.json` that exists _only_ to provide `commitlint`
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
  _daemon_ but not the full 13-service stack.
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

  | Queue     | Nature                  | Tasks                                                                                                                                                                                    |
  | --------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `default` | local / CPU-bound       | `process_attachment`, `process_document`, `process_avatar`, `process_response`, `cleanup_old_results`, `cleanup_orphan_attachments`, `cleanup_stale_results`, `cleanup_expired_sessions` |
  | `network` | outbound GigaChat calls | `generate_chat_message`                                                                                                                                                                  |

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
- **`AGENTS.md` documents _how_; `.context/` documents _why_.** Keeping them separate stops
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
- **The extension is _not_ paired with the content type.** Enforcing `".pdf" implies
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
  rows, and only _reports_ sort intent through the `sort` model — it never sorts itself.
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
- **The skeleton scoped override was later dropped.** While the sidebar still held the
  profile card, `.sidebar` retargeted `--color-skeleton-base` / `--color-skeleton-shimmer`
  to `--sidebar-skeleton-*` for the avatar placeholder. When the card was removed (see
  below) the sidebar rendered no skeleton, so the retarget went with it.
- **Only `--color-danger` stays generic inside the sidebar.** Red reads the same on light and
  dark, so the logout hover does not need a sidebar variant.

## Sidebar no longer lists recent sessions (2026-10-17)

The "ИСТОРИЯ" block is gone: the sidebar is navigation only, and browsing past sessions
belongs entirely to the history page.

- **The block and everything that existed for it were removed together**, not just hidden:
  the template section, the `.history*` styles, the `SidebarHistoryItem` mapping, the
  `chats` / `chatsLoading` / `chatsError` signals, `activeChatId`, `openChat`, the
  `chatService` injection and its `historyVersion` effect, and the now-unused
  `SkeletonChatListComponent` plus its `skeleton-item--chat-list*` styles. Leaving the code
  but not rendering it is how a "removed" feature keeps compiling and confusing.
- **`.profile` moved to `margin-top: auto`.** It used to be pushed down by the history
  block's `flex: 1`, so the sidebar layout rule `:not(:has(.history))` became the norm rather
  than a case.
- **`ChatService.historyVersion` is gone.** It was bumped on create / send / remove / result
  and read only by the sidebar's effect, so once the sidebar stopped listing sessions the
  signal had a writer and no reader. Removed along with `bumpHistory` and the now-pointless
  `tap` pipes; the history page never read it, so no refresh behaviour changes.

## User menu rebuilt for the programme product (2026-10-17)

The user sidebar menu was replaced with the product's own vocabulary: Главная, Мои
программы and Библиотека in the main list, with Настройки and Помощь pinned at the bottom.
The list is configured in `navigation.config.ts` (formerly `sidebar.config.ts`) and the top vs bottom split is a new
`bottomNavItems` input that flows through `Layout` and route `data`.
(`USER_BOTTOM_NAV_ITEMS` later gained Настройки, which moved down from the main list.)

- **The CTA button was removed, not restyled.** A "Создать программу" CTA briefly stood
  where "Новый чат" had been; it was then deleted outright, along with its `.cta` styles,
  the `--sidebar-accent-hover` token, and the `.button--main` `--button-main-bg*` override
  indirection (reverted to reading `--color-main` / `--color-hover` directly). With no
  caller left, all of it was dead weight.
- **`/home`, `/programs`, `/library` and `/help` do not exist yet.** The menu was requested
  ahead of the pages, so those four items render but cannot navigate until the routes land.
  `/settings` is the only live target. This is deliberate, not a regression to "fix" by
  inventing pages.
- **"Тариф" is intentionally absent.** It was specified but deferred; only Помощь was added.
- **The sidebar width is fluid.** `--sidebar-width` is `clamp(340px, 25vw, 380px)` — a modest
  bump from the fixed 330px that still tracks the viewport. Collapsed stays a fixed 96px icon
  rail.

## Sidebar no longer shows the profile card (2026-10-17)

The avatar + name card was removed from the sidebar; it will be shown somewhere else. Only the
sidebar UI went — the reusable pieces stay.

- **`ProfileService`, `AuthService`, the profile page and the global `.photo` style are
  untouched.** They are the "component and services" that were explicitly kept for reuse; the
  sidebar just stopped rendering the card and calling `loadAvatar()`.
- **Logout was removed from the sidebar and has no replacement yet.** It lived inside the
  removed card, so it was first re-added to the footer, then dropped on request; the sidebar
  no longer injects `AuthService` or `Router`. There is currently no sign-out control anywhere,
  and the e2e `signs out` spec was deleted with it — restore both together when sign-out lands
  in its new home.
- **The sidebar's own profile logic went with the card**: `displayName`, `avatarSrc`,
  `avatarLoading`, `onAvatarError` and the `ngOnInit` avatar load all had exactly one reader
  (the removed template) and were deleted. `profile.user` stays, because the admin-panel link
  derives from the role.
- **The bottom anchor moved to the bottom nav.** With `.profile { margin-top: auto }` gone, the
  `Помощь` + footer group is pinned by `.sidebar__bottom-group { margin-top: auto }`, so both
  stay at the bottom for the user (who has a bottom nav) and the admin (who does not).

## Notification toasts reworked: stacking, soft tint, descriptions (2026-10-17)

The toast stack went from single to multi, the card stopped being a hard white box, and
toasts gained an optional second line.

- **Toasts now stack.** `NotificationService.add` appends instead of replacing, and
  `NotificationsComponent` renders the whole list, giving each toast its own auto-dismiss
  timer (and its own leave animation). The old replace semantics made it impossible to see,
  say, a warning and the success that followed it side by side.
- **The card background is a tint, not white.** `background` and `border` use
  `color-mix(in srgb, <type> 6%|38%, var(--color-surface))`. A pure white card with a fully
  saturated border read as too harsh in the light theme; the tint keeps the type readable
  while softening the surface. The type is still carried by the icon and the border hue.
- **`Notification` gained an optional `description`.** Meaningful action results and errors
  set one; terse field validations and pure info toasts deliberately do not, to avoid noise.
- **`errorOnce` takes an options object** — `errorOnce(message, { windowMs?, description?,
persistent? })` — instead of a bare `windowMs` number, so the deduped interceptor errors can
  carry a description like every other call.

## Page header, profile component and a shared icon-button class (2026-10-18)

A top header (inside `<main>`, above the router outlet) now hosts the notifications bell and
the profile, and the icon-button hover mechanism was extracted out of two duplicated copies.

- **The header is white/themed while the sidebar is always dark.** `HeaderComponent` is
  rendered by `Layout`, not the sidebar, so it can use the themed tokens (`--color-surface`,
  `--color-text`, `--color-gray`, `--color-outline`) and follow light/dark. Temporary
  `--header-*` tokens were tried and removed: they added a layer with no second consumer.
- **The bell is fed by the live toast state, not a separate store.** It reads
  `NotificationService.notifications`, so there is no persistence and nothing to show once the
  5s auto-dismiss has fired. A real notification centre (persistent history) is a separate
  feature; this is deliberately just a surface over the current toasts.
- **Sign-out moved into the profile dropdown.** The menu holds a `/profile` link and a red
  `Выход` action that calls `AuthService.logout()`, clears `ProfileService` and toasts.
  Sign-out had been homeless since the sidebar card went; it now lives here. It no longer
  redirects; see the guest-mode note on staying in place.
- **The profile dropdown reuses the toasts, not its own store.** It reads the same
  `NotificationService`, so a real notification centre is still a separate feature.
- **`ProfileComponent` is new, not the resurrected sidebar card.** The sidebar deleted its own
  `displayName` / `avatarSrc` / `avatarLoading` / `onAvatarError` and `ngOnInit` avatar load;
  those now live in a reusable `shared/components/profile` component that calls
  `ProfileService.loadAvatar()`. The name falls back to `username` when first/last are empty.
- **The icon-button hover mechanism is a single global `.icon-action` class** in
  `_buttons.scss`, not per-component copies. It exposes `--icon-action-color`,
  `--icon-action-hover-color`, `--icon-action-hover-bg`, `--icon-action-focus`,
  `--icon-action-size` and `--icon-action-radius`; lucide SVG recolours via `currentColor`, so
  hovering changes the glyph colour, not just the background. The sidebar only overrides the
  variables with its `--sidebar-*` tokens. Previously the sidebar copy recoloured the icon and
  the header copy only changed the background, which is why the bell "did not animate".

## Base-layer cleanup: table keyboard rows, header ARIA, theming (2026-10-18)

A review pass over `core/` + `shared/` fixed a batch of accessibility and theming defects.

- **Interactive table rows are now keyboard-operable.** When `interactiveRows` is true the
  `<tr>` gets `tabindex="0"` and an Enter/Space handler; the handler ignores events from a child
  (so the per-row link in the history table is not activated twice) and sets a focus ring via a
  `--color-main` inset on the first cell, because an `outline` on a `tr` is not reliably painted.
- **The header popups are disclosures, not menus.** The notification panel and the profile
  popup dropped `role="menu"`/`role="menuitem"`, which promise arrow-key navigation the
  component does not implement; the bell's `aria-haspopup="true"` went with them. They now use
  `aria-expanded` + `aria-controls` pointing at the panel id, and the notifications are a plain
  `<ul>`/`<li>` list. Verified against the requirement, not just lint.
- **File-preview dark theming moved into a token.** The three global
  `html[data-theme='dark'] .file-preview__sheet-tab*` rules were replaced by a single
  `--sheet-tab-indicator-bg` custom property defined per theme in `styles.scss`. The component
  keeps `ViewEncapsulation.None`, so a global theme selector would leak; the token keeps the
  colour inside the variable model. Note the old dark rule set the indicator to
  `--color-secondary`, the same colour as its own container, so the tab was invisible in dark.
- **`Layout` gained `ChangeDetectionStrategy.OnPush`.** It was the only shared component
  without it.
- **`AGENTS.md` no longer documents a `ChatService.historyVersion` signal.** No such signal
  exists; the real list-refresh idiom is a per-component local request-id counter, which the
  section now describes.

## Base-layer cleanup: icon buttons, textareas, file types (2026-10-18)

A second review pass over `core/` + `shared/` removed duplicate implementations.

- **One icon-button implementation.** The `icon-button` mixin in `_mixins.scss` is now
  parameterless and reads `--icon-button-radius`, `--icon-button-hover-bg`,
  `--icon-button-hover-color` and `--icon-button-focus`; `.icon-action` in `_buttons.scss` is
  just the mixin plus size/colour defaults, and the sidebar overrides the variables with its
  `--sidebar-*` tokens. The previous mixin took eight parameters and diverged from
  `.icon-action`, so the same visual control had two sources.
- **One textarea implementation.** `shared/components/textarea/` is deleted. `app-input`
  already owned the value accessor, error state and counters, so textarea mode
  (`asTextarea`, `rows`, `counterVisibleFrom`) moved there; `chat-create` and the admin
  settings editor now use `app-input`.
- **One extension list.** `shared/utils/file-types.ts` is the single source for accepted
  extensions, preview kind, MIME type, icon and style. `file-preview.model.ts`,
  `file-type-icons.ts` and `ACCEPTED_EXTENSIONS` in `core/models/models.ts` are gone; dropping
  the list in one place previously left `drag-n-drop` and `file-preview` able to disagree.
- **`controlErrorSignal` returns a computed signal and is called in `ngOnInit`.** It must not
  run in a field initializer: for `[formControl]`/`formControlName` the `NgControl` has no
  `control` until the directive's `ngOnChanges`/`ngOnInit`, so a field-time call captures
  `null` and freezes at `false`. `ngOnInit` is the earliest point where the control exists and
  a `computed` over its `events` is still correct.

## Checkbox and toggle merged into `app-check-control` (2026-10-18)

`CheckboxComponent` and `ToggleComponent` were two copies of one control (value accessor,
label/caption, hidden input) with divergent behaviour, and neither was used outside its spec.

- **One component, `variant: 'checkbox' | 'switch'`.** `shared/components/check-control/`
  replaces both directories. Disabled state is a `formDisabled` signal bound to `[disabled]`;
  the old toggle read `checkbox.disabled` from a static `ViewChild`, which does not update in
  an OnPush component.
- **Focus is `:focus-visible` on the hidden input, not `:focus-within` on the label.** The
  label's `:focus-within` lit up on mouse clicks too. The outline is applied to the sibling
  box/track; note that `@include focus-ring` cannot be used there because the mixin nests its
  own `&:focus-visible`, which would require focus on the span that never receives it. The old
  checkbox styles had that bug and their ring never appeared.
- **Only the variants' markup/styles differ.** The value accessor, `NgControl` error state,
  blur handling, label/caption and disabled styling are shared, so the switch now also
  supports validation errors and `aria-label`.

## Initial chat handoff is bound to a session id (2026-10-18)

`InitialChatStateService` used to hold one global message. `chat-create` set it, then navigated;
`chat` consumed it in its constructor without looking at the route. If the navigation did not
happen, the message survived and the next chat the user opened displayed it as their own.

- **`set(sessionId, message)` and `consume(sessionId)`.** `consume` always clears the pending
  value and returns it only when the id matches, so a stale handoff is discarded by the next
  chat rather than injected into it.
- **The chat consumes in `ngOnInit`, not in the constructor.** `id` is an `input()`, so it is
  not assigned yet when the constructor runs; reading it there would compare against
  `undefined` and drop every handoff.

## The error interceptor owns API error notifications (2026-10-18)

`errorInterceptor` showed a toast for every failed request while several callers
(`chat-create`, `header.logout`) also toasted the same error with their own wording, and
`errorOnce` could not dedupe them because the messages differed.

- **The interceptor is the single owner of `HttpErrorResponse` notifications.** Callers must
  not toast an API failure; they only toast errors that never reached the interceptor, such as
  `Error` from the upload pipeline (XHR, polling timeout) or a non-HTTP failure.
- **Login stays excluded by URL.** The login page owns its credential-error message, so the
  interceptor skips `/auth/login` as before; `SKIP_AUTH_ERROR_HANDLING` still opts a request
  out of both the redirect and the toast.

## The mock interceptor is innermost (2026-10-18)

`app.config.ts` listed `mockApiInterceptor` first, so it answered every request itself and
`credentialsInterceptor`/`errorInterceptor` never ran in mock mode: no 401 redirect and no
error toasts, unlike production.

- **Order is `credentials, error, mock`.** Angular runs interceptors left to right; the mock
  returns its own response instead of calling `next()`, so it must be last for the other two
  to wrap it.
- **`createInterceptors(useMocks)` is the single source of the order and is unit-tested.**
  Do not inline the arrays again; a future interceptor must be inserted before the mock.

## `ProfileService` exposes read-only signals (2026-10-18)

`ProfileComponent` wrote `profile.avatarUrl.set(null)` from its image `error` handler, bypassing
the service's own state transitions.

- **The writable signals are private; the public handles are `asReadonly()`.** Avatar
  invalidation goes through `resetAvatar()`, so the service still owns every write and the
  loading/value pair cannot be desynchronised from outside.
- **Specs seed state through the HTTP API**, not by writing signals, which also exercises
  `fetchMe`/`loadAvatar` instead of assuming their internals.

## CI caches build layers through a CI-only Compose override (2026-10-18)

- **`docker-compose.build-cache.yml` is appended as a third `-f` only when `CI` is set.**
  The `gha` backend is meaningless outside an Actions job: it authenticates with the
  `ACTIONS_*` variables and needs a builder from `docker/setup-buildx-action`. Keeping it
  out of the dev and e2e overrides means a developer's stack never carries settings it
  cannot use.
- **The workflow must re-expose the `ACTIONS_*` variables itself.** The runner hands them
  to an _action_, not to a `run` step, so a `docker compose up --build` from a shell step
  sees none of them, and Buildx drops the cache entries silently instead of failing. That
  is why `e2e.yml` runs `crazy-max/ghaction-github-runtime` before `setup.sh`. The first
  run of PR #21 had the cache file but not the action: everything rebuilt from scratch and
  the log contained no cache import or export.
- **One scope per Dockerfile, not per service.** `backend`, `migrate`, `scheduler`,
  `worker_default`, `worker_network` and `garage-config` all build `backend/Dockerfile` and
  share `scope=backend`; per-service scopes would make each miss on the others' layers.
  `garage-bootstrap` builds `conf/garage.Dockerfile` and gets its own scope.

## OpenAI-compatible provider and tool calling (2026-10-18)

- **`SBER_API_SCOPE` now doubles as the provider selector**, because the OpenAI client is
  not GigaChat and a scope of `PERS`/`B2B`/`CORP` says nothing about which SDK to use. The
  mapping is in `containers.py`: `mock` -> `MockProvider`, then `openai` -> `OpenAIProvider`,
  everything else -> `SberProvider`. The mock check comes first _and_ `SBER_API_KEY=mock`
  also wins, so the test and e2e stacks that only pin the key keep working even if a
  developer's `.env` leaves the scope at `openai`; the ordering is load-bearing, not
  cosmetic. The alternative — a separate `PROVIDER` variable — would have left two knobs
  describing one choice.
- **`OPENAI_URL` empty means the official OpenAI endpoint.** `AsyncOpenAI` already defaults
  to api.openai.com, so the provider only passes `base_url` when the variable is set. That
  keeps "point at the school hub" and "point at OpenAI" the same code path.
- **Tools are construction-time state on `AIClient`, not a per-request argument.** A tool
  set is a property of the assistant, not of one exchange, and defining it once matches how
  the callers think about it. All three providers accept `tools=`; only `OpenAIProvider`
  uses it. `mock` and `sber` ignore it deliberately rather than raising, so a caller can
  advertise tools without branching on the provider.
- **Tool calls surface on the response, nothing executes them.** `GenerationResponse`
  gains `tool_calls: list[ToolCall]`, and `Message` gains `tool_call_id` so a later turn can
  answer one. Execution is a policy decision (which tools, who runs them, with what
  authorisation) and does not belong in the provider, so it is left to the caller.
- **The provider maps only `tool_calls` whose `type == "function"`.** The OpenAI schema
  allows other tool types; the internal model has one kind. Filtering at the boundary keeps
  `ToolCall` from growing a discriminator it will not use yet.
- **The internal content-parts format flattens to a single string on the OpenAI wire.**
  GigaChat takes structured file references in a message; the OpenAI chat-completions shape
  does not, so file parts are dropped and text parts joined with a newline. Attachments are
  still uploaded through the files API (`upload`), but a message cannot cite them yet.
  This is the one place the two providers genuinely differ, and it is documented in the
  module docstring rather than hidden in a converter.
- **`ResponseFormat` maps to `json_schema` only for the schema type**, with the schema
  named `"response"` and `strict` passed through; a text request omits `response_format`
  entirely rather than sending `{"type": "text"}`. Omitting is a choice — the hub accepts
  the explicit text type too — but a text request has no schema to send, so there is nothing
  to serialise. Verified live: the hub accepts the harness schema even without
  `additionalProperties: false`.
- **`auth()` is a no-op with a century-long expiry.** The OpenAI key authenticates every
  request and there is no token endpoint or refresh, but `AIClient` has `ensure_fresh_token`
  machinery that other call paths expect; setting a far-future `token_expires_at` makes it a
  no-op without special-casing the base class.
- **`SBER_API_KEY=mock` still means no LLM call**, and the live tests for the OpenAI client
  carry a second `openai` marker so they can be selected with `-m 'live and openai'` on
  their own, with `SBER_API_SCOPE=openai`. Because the key wins over the scope, `mock` must
  accept every scope the container can hand it; `MockProvider.MOCK_SCOPES` derives from the
  `Scope` literal rather than listing them, so adding a provider cannot leave a scope that
  the mock rejects at import.

## GigaChat v2 tool calling (2026-10-18)

- **GigaChat v2 is the tool-capable endpoint, and its wire shape differs from OpenAI's.**
  `/v2/chat/completions` takes `tools: [{"functions": {"specifications": [...]}}]` and a
  separate `tool_config: {"mode": "auto"}`, not OpenAI's `tools[].function` array. The reply
  nests `function_call` inside `content[]` (with its own `id`) rather than a top-level
  `tool_calls` array. `SberProvider` translates both directions so callers keep seeing the
  internal `Tool` / `ToolCall` models.
- **`ContentFunctionCall` normalises `arguments` from an object to a JSON string.** The
  schema says "JSON wrapped in a string", but the live API returns a JSON _object_; OpenAI
  returns a string. Accepting both in a `mode="before"` validator is cheaper than two
  response paths, and it matches the internal `FunctionCall.arguments: str`.
- **`Content.text` defaults to `""` and `ToolCall.id` is optional.** A v2 function-call
  content part has no `text` key, so the shared `Content` model would reject the real
  response otherwise. GigaChat may omit `function_call.id` (it correlates a result by name
  and `tool_state_id`), so `ToolCall.id` became optional; the OpenAI provider still always
  sets it.
- **`parameters` is required on the GigaChat side**, so a tool without one is sent with an
  empty object rather than omitted. GigaChat's own examples also omit `type: "object"`, so
  the provider passes the caller's schema through untouched.
- **The GigaChat live tool test lives in `test_live_gigachat.py`.** It builds a real
  `SberProvider` from `real_provider_env` instead of reusing the container's module-level
  instance, which was built with the mock key at import time.
- **`strict` is rejected, not dropped, on GigaChat.** The v2 `CustomFunction` schema has no
  strict flag, so `SberProvider` raises `ValueError` rather than accepting a promise it
  cannot keep. The check runs at construction and again in `_tools_payload`, because tests
  and callers may replace `provider.tools` afterwards. The OpenAI client still passes
  `strict` through.
- **Missing GigaChat `arguments` become `"{}"`.** A function-call content part without
  arguments used to surface `""`, which is not JSON. `ContentFunctionCall` normalises
  `None` and the empty string to `{}` before the call reaches `FunctionCall`.
- **The OpenAI payload keeps attachments as content parts.** Text-only messages stay a
  plain string; `Content.files` become `file` parts carrying `file_id`, and `inline_data`
  becomes an `image_url` with a `data:` URL. Dropping them silently made the model answer
  without the supplied context.
- **The container's tool list is a single empty `TOOLS` placeholder.** Both real providers
  receive it, so the wiring exists before a tool registry does.

## The agentic package ships without storage (2026-10-18)

`feat/gen-cicle` differs from `main` only in `backend/app/agentic/` and `tests/test_agentic_*`,
and the package imports nothing outside itself and `app/providers/models.py`:

| File                     | Holds                                                                               |
| ------------------------ | ----------------------------------------------------------------------------------- |
| `app/agentic/models.py`  | the АЦР programme schema: the blocks of the final document, validated               |
| `app/agentic/sources.py` | the catalog seam — `SourceFragment`, `get_source`, `search` — over an empty catalog |
| `app/agentic/tools.py`   | the tool specifications and their handlers                                          |

- **The schema is the documented validation diagram, as the branch's first commit implemented
  it.** The later refinements of the draft — `requested_hours`, the hour-ceiling rule, the
  duplicate-id and declared-outcome validators — belonged to the storage and the loop, and went
  back with them. They return with the work that needs them.
- **The handlers keep no storage of their own.** `save_program_draft` validates the arguments
  against the schema, binds the cited sources to their catalog records and leaves the draft on
  `ToolContext`; `get_program_draft` reads it back for a revision. Persisting it belongs to the
  zone that owns the table: the `chat_programs` model, its migration, `DEFAULT_CHAT_NAME` in
  `app/models/chat.py` and `MOCK_DRAFT_ARGUMENTS` in `providers/mock.py` were removed, so the
  branch touches no other zone, needs no migration, and its handler tests need no database.
- **The handlers speak pydantic models, not dictionaries.** A tool's arguments model is also
  the schema advertised to the model, and a handler answers a result model, so no handler ever
  sees or builds a raw dict. Validation happens at the call boundary — `execute_tool`, where
  the JSON string of a `FunctionCall` enters — and never inside a handler, which may take its
  arguments model for granted; when the loop takes that boundary over, it can validate a call
  and invoke the handler directly.
- **Only sources the run has shown the model may be cited.** `search_sources` widens
  `ToolContext.known_source_ids`, and a citation the catalog knows but this run has not returned
  is refused as well as one the catalog does not know — the model corrects itself differently
  for each. While the catalog is empty every citation is refused as unknown, which is the
  honest answer until the RAG index lands.
- **A failed call is an answer, not an exception.** Every handler failure comes back as
  `ok: false` with an explanation, so the model can correct itself inside the same reply, and
  `execute_tool` turns malformed JSON and unknown function names into the same shape.
- **The tools adopt the types #26 merged.** `AGENT_TOOLS: list[Tool]` is built from
  `Tool(function=FunctionDefinition(...))`, which is what a provider takes at construction, and
  `execute_tool` reads `FunctionCall.arguments` as the JSON string the providers normalise to.
  The interim `FunctionCall` block that had briefly been restored into `providers/models.py` is
  gone with it.

## The agent draft contract: what waits for the loop (2026-10-18)

The loop, the storage and the hour rules are not in the tree. These are the review-driven
decisions for whoever picks them up in #26, recorded so that the round does not have to be
repeated.

- **The hour rule is a ceiling, not an equality.** Once the draft carries the volume the
  methodist stated, `total == requested_hours` when it is present; without it,
  `days x hours_per_day` is only a ceiling, because the final attestation lives outside the
  curriculum — the example document fills 66 of its 72 scheduled hours.
- **The tool-round bound counts rounds, not model turns.** After the last executed round the
  model still gets one turn to answer from the results it already has, and only a turn that
  reacts with another call is `ToolLoopExceeded`.
- **A truncated answer is a failure, not an answer.** `finish_reason == "length"` means the
  provider stopped at the token limit, so the loop raises `AnswerTruncated` rather than storing
  a half-written reply as the model's final word.
- **The chat is renamed only after the draft commits**, so a failed save cannot leave a renamed
  chat behind without the draft it was named for.
- **The loop is worth writing once, against the merged types.** It had been written against a
  `FunctionCall` of its own, and `GenerationResponse.tool_calls` (#26) retires exactly the
  helpers that scanned message contents for calls. Review points for that implementation:
  errors inherit from `Exception` rather than `RuntimeError`, `message.role` compares through
  the existing enum, and no helper scans messages for calls.
- **The source materials stayed with the RAG zone.** `app/agentic/materials/` was a byte copy
  of the root `edu_materials/` dataset; RAG (#11) owns both the corpus and the search over it,
  and `sources.py` keeps the seam with unchanged signatures.

## The draft's checks: schema bounds here, cross-block rules in #4 (2026-10-19)

The review of #24 asked for more checks on the draft. Two of them are the schema's job and were
added here; the others are listed in `docs/agentic-validation.drawio` as issue #4, and
implementing them now would have re-done a zone this branch deliberately does not touch.

- **Bounds are schema, not business logic.** An hour split is non-negative and a schedule value
  supplied is positive — `ge` and `gt` on `Hours` and `Schedule`, block E of the diagram. The
  dispatcher refuses such a draft, so a handler still has nothing to validate.
- **Uniqueness of ids and `topic.outcome_ids` resolving to declared outcomes are #4's.** The
  diagram's block I names both, next to the hour totals, and calls them ordinary functions that
  return per-element errors over the whole programme, not pydantic validators of one draft.
- **The hour totals have nothing to compare against in the draft.** The volume the methodist
  asked for arrives in the chat message, not in the schema, so `days x hours_per_day` would be
  the only available bound — and it is a ceiling, since the final attestation sits outside the
  curriculum. #4 owns the rule.
- **A competence cites a professional standard.** `_resolve_sources` refuses an id that resolves
  to another kind, because the field says where the labour function comes from.
- **The handler owns both what it keeps and what it hands out.** `frozen=True` on the schema does
  not freeze the lists inside a model, so `get_program_draft` answers a deep copy and the stored
  draft is the handler's own: editing a returned draft cannot reach the state already bound to
  the catalog and about to be stored.
- **A save needs no idempotency key inside one context.** The context lives in memory for one
  worker run and calls execute sequentially, so an older save cannot land after a newer one;
  ordering is the loop's business (#26) and the upsert is the worker's (block F3).

## Guest mode: the panel is browsable, protected sections ask for a login (2026-10-18, #17)

The panel used to be unreachable without a session: `authGuard` on the whole user tree, a
`publicGuard`-protected `/login`, and the error interceptor bouncing an expired session to it.
A visitor could see nothing before signing in. Guest mode replaces all of that.

- **The session is resolved once at startup, not per navigation.**
  `provideAppInitializer(() => inject(ProfileService).ensureSession())` resolves `GET /users/me`
  before the first render and never errors, so bootstrap is never blocked by a failed fetch.
  A 401 resolves to `null` silently — that is the legitimate "no session" answer. Any other
  failure (network, 5xx) also resolves to `null` because the app must still render, but it is
  not the same thing as being a guest, so `ensureSession` raises a "Не удалось проверить
  сессию" toast rather than passing the failure off as a guest shell. This is also what lets
  `adminGuard` take a plain synchronous path: once `sessionChecked()` is true the guard only
  reads `user()`. It keeps an async `fetchMe()` fallback for the case where the session is
  still unresolved, so the guard does not depend on the initializer staying blocking.
  The cost is one request on every reload, guest or not.
- **A guest is a first-class state, not an error path.** There is no registration anywhere in
  the product (accounts are created by an admin), so the only action a guest has is signing in.
  `ProfileService.user() === null` after `sessionChecked()` is the whole definition.
- **`/login` became a dialog.** `AuthModalService.open(returnUrl?)` drives one native `<dialog>`
  mounted in the root shell; it remembers a sanitised return url (leading `/`, not `//`), and
  after a successful login refreshes the profile, closes, toasts and restores the url. The
  header, the gate prompt and future surfaces share it instead of navigating the user away.
  The dialog is rendered under `@if (isOpen())` exactly to keep its form controls out of the
  document while closed — see `LESSONS.md`. Escape closes it through the native `cancel` event
  and the "Войти" button drives `ngSubmit`; Enter in either field is the browser's implicit
  submission, so there is deliberately no `keydown.enter` handler. Both are proven end-to-end in
  `auth.spec.ts`, not in jsdom, which cannot emulate them (see `LESSONS.md`).
- **The guest chip opens the dialog directly, without a menu.** A guest's profile menu held a
  single "Войти" item — a dropdown that exists to be clicked once. `toggleProfile()` routes a
  guest straight to `AuthModalService.open()`; the chip's accessible name becomes "Войти в
  аккаунт" and it drops `aria-expanded`/`aria-controls` (there is no popup to control),
  keeping only `aria-haspopup="dialog"`. The profile menu itself (Профиль, Выход) renders
  only for a signed-in user.
- **Protected routes are wrapped, not guarded.** A pathless `AuthGate` renders the child routes
  for a signed-in user and, for a guest, an inline prompt in the page content whose "Войти"
  button opens the shared sign-in dialog. The url is preserved, and the prompt is the only
  thing a guest sees there: the gate itself never raises a modal. Deliberate — the product
  asked for exactly one popup (the sign-in window) and for the "Требуется вход" message to
  live in the content. `/home` and `/help` are public with real routes, and `**` falls back to `/home`, so
  every sidebar link resolves.
- **A 401 no longer navigates.** The error interceptor reports the rejection on
  `SessionEventsService.unauthorized$` instead of reaching into `ProfileService` directly —
  that would close the `ProfileService -> HttpClient -> interceptor -> ProfileService` loop,
  which only resolves today because the interceptor's inject is lazy. `ProfileService`
  subscribes and calls `clear()`, the interceptor shows the toast once, and the gate or the
  header offer the dialog. `clear()` marks the session checked, so the shell re-renders as a
  guest instead of waiting on a fetch that will fail again.
- **Sign-out stays in place.** `HeaderComponent.logout()` clears the profile and toasts but does
  not navigate. The page a user logs out from keeps its url; if it was protected the `AuthGate`
  swaps in the "Требуется вход" prompt, the same thing a guest sees on arrival. Sending them to
  `/home` threw away their location and made the gate's url-preserving behaviour pointless.
- **The sidebar is deliberately untouched.** Items stay enabled and clickable; `programs` and
  `library` render `SectionPlaceholder` behind the gate so a guest discovers the sections'
  existence. Locks or disabled items were explicitly rejected: the prompt explains, a lock does
  not.
- **The guest profile chip never fires the avatar request.** `ProfileService.loadAvatar()` and
  `resetAvatar()` short-circuit for a guest, and `ProfileComponent` drives them from an effect
  on `user()` rather than `ngOnInit`, so a login or logout mid-session re-loads or clears the
  avatar without a remount.

## Route transitions are CSS view transitions on `main` (2026-10-18)

Switching sidebar sections should read as a move, not a replace. The work rides on the
browser's View Transitions API through `withViewTransitions()` in `app.config.ts` — no new
dependency and no wrapper element.

- **`skipInitialTransition: true`, direction decided in `onViewTransitionCreated`.** The
  callback runs before the DOM swap, so the old page is `router.url` and the target is
  `router.getCurrentNavigation()?.finalUrl?.toString()`. The direction is the sign of the
  index delta between the two sections, with the order derived from the shared item lists
  (`USER_NAV_ITEMS`, `USER_BOTTOM_NAV_ITEMS`, `ADMIN_NAV_ITEMS`), so a down-move
  animates down and an up-move animates up without a second source of truth. The lists live
  in `shared/navigation/navigation.config.ts`, not in the sidebar component folder, so a
  `shared/utils` module does not import from `shared/components`; the sidebar, Layout and the
  router all read the same config instead.
- **The named element is `main` in the layout, not a wrapper.** `view-transition-name:
router-content` sits on the layout's `<main>`. A wrapper element was rejected because the
  chat page relies on `:host { height: 100% }` and collapses inside an auto-height wrapper.
- **The root group is deliberately not animated.** `::view-transition-old(root)` /
  `new(root)` get `animation: none`; the default cross-fade would animate the whole page,
  sidebar and header included, on every navigation.
- **The trigger appends nothing to the DOM.** Direction travels as
  `document.documentElement.dataset['navDirection']`, cleaned up in `transition.finished` —
  guarded so a newer direction set mid-flight is not wiped by the previous transition.
- **Query-only navigations and reduced motion skip the animation.** Search/filter changes
  are not section switches; `prefers-reduced-motion` disables the keyframes wholesale.
- **Sections the sidebar cannot rank cross-fade, they do not slide.** `/create`, `/chats` and
  `/profile` are outside the item order, so the delta is undefined and the direction is
  `'none'`. The base `::view-transition-*(router-content)` rules carry the neutral fade and
  only `[data-nav-direction='forward'|'backward']` swap in the sliding keyframes — otherwise
  `'none'` would inherit the forward slide and read as a move that never happened.

## The layout shell is a column flex container (2026-10-18)

The auth gate prompt wanted the free space under the header and asked for it with
`min-height: calc(100dvh - 80px)`. The header is 83px tall (12px padding, 1px border, a
46px avatar), so the prompt overshot the viewport by exactly 3px and every gated route
showed a scrollbar with nothing to scroll to.

- **`main` is `display: flex; flex-direction: column`.** The prompt is
  `flex: 1 1 auto` with `min-height: 0` and the header is `flex-shrink: 0`, so the browser
  computes "the rest of the viewport" from the header's real height. No arithmetic, no
  constant to keep in sync with the avatar size.
- **Every routed page is pinned with `flex: 0 0 auto`.** A routed host defaults to
  `flex-shrink: 1`, so the chat page (720px) and the long admin tables would have been
  squashed inside a 720px `main`. The rule lives in `styles.scss` as
  `main router-outlet + *` because the routed host is not part of the layout's template and
  emulated encapsulation cannot reach it.
- **The chat page's own 83px overflow is acknowledged, not fixed.** `:host { height: 100% }`
  is 100% of `main`, header included, so that page still scrolls by the header height. It
  predates this change and needs its internal `calc(100dvh - 40px)` reworked, which is a
  separate piece of work.

## The landing logo is a separate animated component (2026-10-19)

`/home` became the program-creation landing: a big brand mark, a greeting, prompt-example
cards and a markup-only composer (local signals, no API). The design brief asked for an
animated brand mark, but the shipped mark is `public/assets/svgs/logo.svg`, which is an SVG
wrapper around a base64 PNG. It carries no vector paths, so it can be neither recoloured nor
morphed, and it is shared with the sidebar.

- **`app-brand-logo-animated` keeps the real asset and animates around it.** The `<img>` is
  the untouched `logo.svg`; a breathing transform, a blurred aura and a light wave clipped to
  the mark's silhouette with `mask-image: url(logo.svg)` provide the motion. A first attempt
  re-drew the mark as generic vector blobs and read as "just a blob", so brand fidelity won
  over a fully vector mark.
- **The static `logo.svg` is untouched.** The sidebar keeps the raster mark unanimated; only
  the landing mounts the animated component, which is why it is a new component rather than
  an override of the existing asset.
- **Only `transform` / `opacity` / `background-position` are animated.** `prefers-reduced-motion`
  disables every keyframe, and the active state (field focus / typing) only shortens the
  durations and scales the mark up.
- **The composer is presentational.** `/home` is public and this was a markup task, so the
  attach button fills local file chips and the send button only toggles on non-empty text —
  nothing is uploaded or submitted. The real flow stays on `/create`.
- **The landing opts out of the routed-page pin.** It sets `:host { flex: 1 1 auto }` to fill
  `main` and centre its content; `:host` has higher specificity than the `main router-outlet
  + *` rule documented above.

## A guest's home-page send survives the sign-in dialog (2026-10-20)

`/home` is public and the composer is the first thing a visitor meets, so sending as a guest
must not lose what was typed. The draft is kept in `PendingChatDraftService` (the trimmed
text and the original `File`s) and consumed once `ProfileService.user()` becomes non-null.

- **Why not `InitialChatStateService`:** it is keyed by a session id that does not exist yet.
  The draft is what the session is created for; the initial-chat state still hands the sent
  message to the chat page after creation.
- **One starter for both entry points.** `ChatStarterService` owns create → upload files →
  send, so `/create` and `/home` cannot drift apart. Home also uses it for an authenticated
  user, which is what makes the send button meaningful without a sign-in detour.
- **The sign-in dialog keeps returning to `/home`.** The resume effect lives in `HomePage`,
  and a same-URL navigation keeps that component alive. A different return target would
  destroy the only consumer of the draft.
- **The draft is consumed before the request starts,** so retrying after a failure cannot
  create two chats. The composer still holds the text and files for a manual retry.

## Voice input is the Web Speech API, wrapped in a service (2026-10-20)

The home composer gained a microphone button beside send. Dictation uses the browser's
native `SpeechRecognition` (`webkitSpeechRecognition` fallback) rather than an upload flow.

- **Native, not `getUserMedia` + a speech backend.** This is a text-entry convenience, not
  transcription as a product feature; a backend would add a service, credentials and latency
  for the same text. Chromium and Safari ship an engine, and Firefox users get a disabled
  button instead of a button that fails after the click.
- **Continuous Russian with interim results.** `lang = 'ru-RU'`, `continuous = true`,
  `interimResults = true`; each result recomposes `base + final + interim`, so the field
  fills while the user speaks and the final text replaces the interim.
- **The service owns recognition, the component owns the text policy.** The service normalizes
  chunks into `{final, interim}` and reports errors; HomePage decides that dictation appends
  to the existing text and that the transcript survives stopping.
- **Errors become Russian notifications.** Microphone denial, a missing device, no network and
  an unsupported language are errors; `no-speech` is information, because silence is not a
  failure of the app.
- **The engine is cloud-hosted and may be unreachable.** Chrome sends audio to Google and
  fails with `network` where that service is blocked; Safari's Apple-backed engine recognised
  the same speech on the same machine. The app keeps the native API and explains the failure
  instead of shipping server-side STT. A Chrome-first audience would need a backend
  recogniser (SaluteSpeech or a local Whisper), which is a separate decision with credentials
  or a model to host.

## Attachment tiles own their thumbnail and preview action (2026-10-20)

The home composer renders each pending file as an `app-file-tile` above the textarea.

- **The tile owns the object URL.** It creates a thumbnail with `URL.createObjectURL` for
  image extensions in an effect and revokes it when the file changes or the tile is
  destroyed. The parent only keeps the `File`, so removing an attachment cannot leak a blob
  URL.
- **Preview reuses `FilePreviewComponent`.** The composer passes the pending `File` to the
  existing dialog, so PDFs and images preview before upload, and the heavy docx/xlsx
  renderers stay lazy.
- **The image area is the preview trigger.** There is no separate eye button: the media is a
  button that opens the preview, and it is disabled when `previewKindFor` returns null. A
  file with an unknown extension still gets the same tile, name and remove action, just no
  preview.
- **The tile lives in `shared/components/file-tile` partly for the style budget.** The home
  stylesheet was at the 10 kB `anyComponentStyle` error line; a tile's worth of CSS belonged
  in its own component rather than pushing the page over.

## Failed chat starts clean up after themselves

`ChatStarterService.start` deletes a session it created as soon as uploading or sending
fails, together with every attachment the backend has already registered. Without this a
failed attempt left an empty or half-filled chat in the user's history while the UI claimed
nothing was created. Cleanup is best effort: deletions run with a short timeout, their own
errors are swallowed, and the original failure is rethrown so the user still sees the real
reason. Attachment ids are collected through `onAttachmentId`, which fires before the
storage POST, so a file whose bytes never arrived is still deleted.

## A guest draft belongs to one login attempt

Dismissing the auth dialog clears `PendingChatDraftService`; a successful login does not.
The draft is created only when a guest submits the home composer, and it is meant for the
login flow that dialog was opened for. Keeping it after dismissal let a later sign-in in the
same application instance send the previous guest's text and files under another account
without confirmation. Cleanup therefore lives in `AuthModalService.close()`, which every
cancellation path reaches, rather than in the dialog component, where an external
`modal.close()` could bypass it. The success path calls the separate `complete()`, which
closes the dialog and keeps the draft for the authenticated start.

## Chat starts upload through the same three-slot queue

`ChatStarterService` uploads with `mergeMap(..., MAX_CONCURRENT_UPLOADS)` rather than a
`forkJoin` over every file. Attaching ten files used to open ten storage uploads at once,
bypassing the limit the drag-and-drop queue already enforced and inviting rate limits and
network failures. The shared constant keeps both paths in sync; the per-file upload deadline
still starts only when its slot is taken, so queued files are not timed out while waiting.

## One message-length limit for both creation forms

`MAX_MESSAGE_LENGTH = 5000` lives in `core/models/models.ts` and is used by both the
`/create` form and the home composer. The home composer previously had no `maxlength` and no
submit check, so it could hand the API text the other client-side path already rejected.
`ChatCreateComponent.MAX_SYMBOLS` now aliases the shared constant so its existing tests and
template keep working while the number has a single source.

## The programs catalog is mock-first behind a service (2026-10-21)

`/programs` ships as a working catalogue before there is a programs API.

- **The page talks to `ProgramsService`, not to fixtures.** The service owns an in-memory
  signal and the duplicate / archive / delete mutations, so the page contains no mock data
  and replacing the service body with HTTP calls later is the only integration point.
- **`draft` is a status, not a filter.** Drafts appear only under "Все". A draft is a
  transient authoring state rather than a section users browse, and the filter row stays at
  five predictable choices.
- **Editing and the primary CTA lead to `/programs/new`.** The route renders
  `SectionPlaceholder` inside the full gate until the form exists.
- **The card is a separate component partly for the style budget.** `anyComponentStyle`
  warns at 6 kB, so a page-sized stylesheet would cross it. Theme accents and status colours
  are driven by `data-theme` / `data-status` attributes, and status colours live in global
  tokens so the card does not hardcode them.

## `/programs` previews behind a guest gate instead of hiding the page (2026-10-21)

The programs catalogue is the first route that is useful to see before signing in.

- **The route stays inside `AuthGate` and opts into a preview mode.** Its parent route
  carries `data: { authGateMode: 'preview' }`; `AuthGate` reads that flag and renders the
  outlet plus an overlay for a guest. Other protected routes still render only the inline
  prompt, so the gate remains the single place that decides what a guest may see.
- **The preview is inert, blurred and hidden from assistive technology.** The outlet sits in
  a wrapper with `filter: blur(6px)`, `pointer-events: none`, `inert` and `aria-hidden`;
  only the `LoginPrompt` in the overlay is interactive.
- **The overlay is fixed to the main column, below the sticky header.** Its
  `left: var(--sidebar-width)` keeps the navigation sharp, and `z-index: 10` sits under the
  header's `z-index: 20`. That blurs the catalogue rather than the whole application shell.
- **The outlet is not recreated on login.** The preview wrapper stays mounted and only loses
  its locked class; a guest who signs in keeps the same page instance instead of resetting
  filters and local mock state.
