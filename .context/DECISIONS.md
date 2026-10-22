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
- **Each package is also its own Docker build context** (`frontend/`, `conf/`). The backend
  is the exception since the RAG integration: it builds from the repository root because the
  image installs the sibling `rag/` package, so the root `.dockerignore` excludes the
  unrelated trees and `backend/Dockerfile` copies both `backend/` and `rag/`. This only
  became possible when `processing/` went — the backend used to need a
  `COPY processing/ /processing/` from the root context.
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
  | `network` | outbound GigaChat calls | `run_chat_turn`                                                                                                                                                                          |

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

- **`app/harness.py` owns the prompt**: `SYSTEM_PROMPT` (what the model is told) and
  `PROMPT_EXTENSION_PREAMBLE` (the heading the admin's text is appended under). The reply
  used to be a structured answer validated against a Pydantic model; the chat turn is a
  tool loop now, so there is no output schema any more — the tools' specifications in
  `app/agentic/tools.py` are the contract the model answers against.
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
- **`OPENAI_URL` used to default to the official OpenAI endpoint.** `AsyncOpenAI` does that
  on its own, so the provider only passed `base_url` when the variable was set. Reversed on
  2026-10-21: see "OpenAI endpoint is mandatory" below.
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

## OpenAI endpoint is mandatory (2026-10-21)

- **`OPENAI_URL` must be set and non-blank when `SBER_API_SCOPE=openai`; otherwise
  `OpenAIProvider` raises `ValueError`.** The deployment is only meant to talk to a custom
  hub, so the SDK's implicit public endpoint is a failure, not a default. An empty value in
  an otherwise configured environment is far more often a missing setting than a deliberate
  choice, and `containers.py` constructs the provider at import time, so the failure is loud
  and immediate.
- **`base_url` is always passed explicitly.** No code path remains in which `AsyncOpenAI`
  can supply its own default endpoint.

## E2E waits are explicitly bounded (2026-10-21)

- **The global test timeout is 240s, and every potentially blocking wait also carries its
  own bound.** The autouse fixture may spend 45s waiting for an in-flight generation plus
  15s clearing rate limits, and `chat-flow` makes two sequential 45s generation assertions,
  so a 120s ceiling could expire before the second one. `waitForRequest`, `waitForResponse`
  and `waitForEvent` calls pass an explicit 15s.
- **Docker subprocesses get a timeout because Playwright cannot interrupt a synchronous
  `execFileSync`.** A stuck `docker compose exec` would otherwise outlive the test timeout
  and hang the worker: the pagination seed is bounded at 60s, each rate-limit attempt at 5s
  within a 15s retry budget, and each idle-generation poll at 10s within a 45s retry budget.
  A failure to clear the counters or confirm the lock fails the fixture instead of letting
  the next test report a misleading 429 or 409. Retry waits use `setTimeout`, not
  `Atomics.wait`, so the event loop stays free for Playwright's timeout.
- **`setup.sh` shares one 120s readiness deadline between the frontend and backend probes.**
  Each `curl` attempt is capped at 5s, so a hung connection cannot stretch the deadline; the
  script exits when the deadline passes instead of seeding an unready stack.

## Embedding provider lives in backend/app/providers

The embedding client is a backend concern, not an edu_rag one. edu_rag
stays dependency-light (no openai SDK) and declares only a structural
protocol (`model`, `dimension`, `async embed()`). `OpenAIEmbeddingProvider`
and `MockEmbeddingProvider` are backend classes, matching the
SberProvider/MockProvider pattern. Scope selects which one is used; the
mock is preferred whenever `SBER_API_KEY=mock`, mirroring the chat-provider
rule.

`OPENAI_EMBEDDING_MODEL` and `OPENAI_EMBEDDING_DIMENSION` have no default
in code. A deployment that reaches the embedding path with either unset
gets a hard KeyError at container construction instead of a silent wrong-
endpoint fallback.

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
- **Only `transform` / `opacity` are animated.** `prefers-reduced-motion`
  disables every keyframe, and the active state (field focus / typing) only shortens the
  durations and scales the mark up.
- **The composer is presentational.** `/home` is public and this was a markup task, so the
  attach button fills local file chips and the send button only toggles on non-empty text —
  nothing is uploaded or submitted. The real flow stays on `/create`.
- **The landing opts out of the routed-page pin.** It sets `:host { flex: 1 1 auto }` to fill
  `main` and centre its content; `:host` has higher specificity than the `main router-outlet
  - \*` rule documented above.

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
- **The filter row is type + update month + technology tag, not status.** Status stays a
  card badge and a draft is not a separate browsing section. Type options come from `type`
  (split from `direction` when the detail panel landed; see the later section), date
  options are the months present in `updatedAt`, and tag options are the
  distinct `tags` values, all derived from `ProgramsService` so a duplicated row appears
  under its new month immediately.
- **Technology filtering is multi-select and OR-based.** The shared `Select` gains a
  `multiple` mode that keeps the listbox open while options are toggled and treats the
  `all` sentinel as a clear action. A program matches when it has _any_ of the selected
  tags, which is what a filter chip row is expected to do; selecting Python and Agile
  widens the result rather than narrowing it to programs tagged with both.
- **Pagination is part of the service seam.** `ProgramsService.query` takes the active
  filters plus `page` / `itemsPerPage` and returns the existing `PaginatedResponse` shape,
  even though it currently slices an in-memory signal. The page owns the page number,
  resets it on any filter change, and clamps it after a deletion or archive shrinks the
  filtered result.
- **The catalog has no create CTA.** Program creation has no entry point yet, and
  `/programs/new` renders `SectionPlaceholder` inside the full gate until the form exists.
  No card action navigates there.
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

## The program detail panel is a side aside with demo content (2026-10-21)

The catalog now opens a right-side detail view from a card, without leaving `/programs`.

- **`type` is split from `direction`.** `Program.type` is the filterable programme kind
  ("Повышение квалификации", "Корпоративное обучение", ...), while `direction` is the
  subject area shown in the panel's metadata. The earlier catalog note that type options
  come from `direction` is superseded; `ProgramsService.query` filters on `type`.
- **The panel is page state, not a route.** `ProgramsPage` owns `selectedProgramId` and
  renders the panel beside the grid, so filters, pagination and the scroll position survive
  opening and closing it. The URL is unchanged, and the card that opened the panel is the
  focus-restoration target.
- **The detail view is a floating aside, not a modal and not a grid column.** The panel is
  `position: fixed` above the page, so its alignment is relative to the viewport and it stays
  in place while the catalog scrolls. The page does not reflow. There is no backdrop, focus
  trap or scroll lock: the page stays a normal document. The component host is always mounted,
  but its content lives behind `@if (program(); as program)` so `animate.leave` can finish
  before Angular removes it. Closing is available from the arrow and Escape; focus returns to
  the triggering card.
- **The panel is a card set off the viewport edges.** It uses `top: 20px`, `right: 5px`,
  `width: min(640px, calc(100vw - 10px))`, `height: calc(100dvh - 40px)` and a full
  `--color-outline` border with a 20px radius, mirroring the existing side panels instead of
  touching the screen edge with a left border-only drawer.
- **The panel's header menu and footer actions are deliberately visual-only.** Per the product
  decision, "Дублировать", "Архивировать" and "Удалить" in the header menu only close the
  menu, and "Открыть программу" / "Редактировать" are styled buttons with no handlers. The
  card's context menu remains the functional path for duplicate / archive / delete.
- **Demo detail content is derived, not placeholder text.** Modules split the programme hours,
  competencies are built from tags and direction, and materials come from the existing file
  type styles. This keeps the panel presentable until the programs API and editor exist.
- **The card opens through a full-card button.** The button sits under the card's content;
  the content has `pointer-events: none` and the context menu opts back in with
  `pointer-events: auto`, so the whole card is one target without swallowing the menu.

## The programs catalog is lazy, responsive and caller-configured (2026-10-21)

- **`/programs` is lazy-loaded.** The initial bundle was within a few kilobytes of its 1 MB
  budget, so the page and its detail panel now load through `loadComponent` instead of
  entering the initial chunk. The next catalogue-sized feature would otherwise cross an
  error threshold, not just a warning.
- **Below 980px the panel fills the viewport and the guest overlay drops the sidebar
  offset.** The layout keeps a fixed 340px sidebar, so a 640px floating panel would cover
  the navigation on a narrow window. At 980px and below the panel becomes full-screen and
  the overlay uses `inset: 0`; wide viewports keep the floating card.
- **The section is labelled `Мои программы`, and its tag filter is `Теги`.** The page is
  the signed-in user's own workspace: every mock program is authored by `Админ Демо`, so the
  nav label and heading match the list. Guests still see the page behind the preview gate.
  `Технология` was too narrow for tags such as soft skills and management.
- **`Select` no longer hardcodes the `'all'` sentinel.** A multiple select treats a value as
  the clear action only when the caller supplies `allValue`; `/programs` passes `allValue="all"`.
  This keeps a page-specific convention out of the shared control.

## Programs results are announced, and pagination returns to them (2026-10-21)

- **The range summary and the empty state are polite live regions.** Filter and page changes
  therefore reach a screen reader without a separate status component.
- **The grid and empty state sit in a focusable `role="region"` labelled "Результаты".**
  Pagination and reset move focus there and scroll it to the top, because leaving focus on
  the old page control would keep the new cards above the viewport.
- **Single-select filters only scroll to the results and keep focus on the select.** Moving
  focus after a filter change would break keyboard adjustment. The tag multi-select does
  neither while it stays open: scrolling would pull the open dropdown away from the pointer.
- **`scroll-margin-top: 72px` clears the sticky application header.** Focus scrolling uses
  the same offset as `scrollIntoView`, so neither lands under the header.

## Program action menus implement the menu button keyboard pattern (2026-10-21)

- **Both card and panel action menus keep `role="menu"` and add the missing keyboard
  behaviour.** Opening the menu focuses its first item; ArrowDown/ArrowUp wrap, Home/End
  jump, and Escape still closes and restores focus to the trigger.
- **The items use a roving `tabindex`.** Only the active item is in the tab order, so Tab
  does not walk through every command before leaving the menu.
- **Navigation lives in `shared/utils/menu-keyboard.ts`.** The card and panel menus have the
  same command set and would otherwise drift; the helper returns the next index and leaves
  unrelated keys alone.

## Filters keep a selected value that no longer exists (2026-10-21)

- **The catalogue options are derived from the surviving programs, but a selected value is
  appended back when its last program is deleted.** Otherwise `Select` lost the label and
  fell back to its placeholder while the filter signal stayed set, so the page showed an
  empty grid with no visible active filter.
- **The filter is not auto-cleared.** Clearing it in an effect would silently widen the
  results after a destructive action. Keeping the value visible makes the empty state
  explainable and leaves the user in control of resetting it.
- **The same rule covers type, month and tags.** The helper appends every missing selected
  value, with the month label formatter reused for dates.

## Card actions return focus to the grid (2026-10-21)

- **Closing a card menu removes the focused menu item, so the browser drops focus to the
  body.** The card now emits its trigger explicitly with the open and action events instead
  of the page reading `document.activeElement`; the page can restore focus after the DOM has
  settled.
- **A surviving card gets its menu trigger back; a deleted card hands focus to the next card
  at the same position, or to the results region when the grid becomes empty.** Duplicate and
  archive keep the same card, while delete has no trigger left to focus.
- **Restoration runs in `afterNextRender`, not in the click handler.** The deleted card is
  still in the DOM while the action is handled, so focusing synchronously would lose focus
  again when Angular removes it.

## Deleting a program is confirmed, and archiving is reversible (2026-10-21)

- **Delete asks for confirmation through `window.confirm`.** The admin user list already
  uses the same native confirmation for a destructive action; a bespoke modal would add a
  component and focus trap before the catalogue needs one. Cancelling keeps the program and
  leaves focus on the card trigger.
- **Archived cards offer `Восстановить` instead of `Архивировать`.** The action republishes
  the program, because the mock service keeps no pre-archive status. A real API should return
  the previous status rather than assume `published`.
- **The card menu stays the functional action surface.** The detail-panel menu is still
  visual-only, so it does not advertise an unarchive action it cannot run.

## The programme cycle: one row, one task per stage, three layers of safety (2026-10-20, #3)

The cycle was re-done from `main` after the earlier attempt's infrastructure was rejected.
These are the choices that the code cannot explain on its own.

- **The run lives in `chat_sessions.program_state`, not in its own table.** A programme is a
  property of exactly one chat, and the chat row is already the thing every writer has to
  take before touching it: `SELECT ... FOR UPDATE` on that row is the cheapest mutex there is,
  and a separate table would have needed its own lock and its own cascade. JSONB also lets the
  envelope grow with the schema without a migration per block; the alternative, a column per
  block, would have made "is this stage done" a set of null checks spread over nine columns.
- **PostgreSQL is canonical and Redis is a mirror with a one-hour TTL.** A stage reads the row,
  never the mirror, so a Redis flush cannot lose a run; the mirror exists so an operator can
  watch a stage work without querying the database, and it is refreshed on every load and save
  rather than written on transitions only.
- **One task per stage, and the next task is enqueued after the commit.** This is what makes a
  worker death survivable: the run stops on a boundary that is already durable, and the blocks
  the dead worker saved are still there. A `while not ready` loop inside one task would have
  kept the whole transcript in one process's memory, which is exactly the state that vanishes.
- **The attempt is spent when the stage is enqueued, not when it starts.** A worker that dies
  before its first line has still consumed a delivery, and counting at the start would let a
  stage that reliably kills its worker be re-delivered forever. `MAX_STAGE_ATTEMPTS` = 3 is the
  budget for the whole lifetime of one stage, re-deliveries included.
- **Three different guards, and each catches a different failure.** The PostgreSQL row lock
  orders two writers that save at the same instant; the version on the envelope refuses a
  writer that loaded the row ten minutes ago; the Redis stage lock keeps a slow stage from
  overlapping the recovery run. The version check alone would still let two workers _execute_
  a stage — the lock is what stops the duplicate work, the version is what stops the duplicate
  write when the lock has expired.
- **The lock is released with a Lua compare-and-delete.** `redis-py`'s `lock()` generates its
  own token, so a task that wants to release a lock from another connection has to own the
  token itself. A plain `DEL` would remove a lock that expired and was taken by someone else.
- **A stage that saves nothing is a failed stage.** The model answering "готово" without a
  `save_program_draft` call is a provider that misunderstood the turn, not a finished stage, so
  `ToolContext.saved` gates the completion and the stage is retried with the reason attached.
- **Recovery skips a stage whose lock is alive and spends an attempt when it re-enqueues.** The
  cron would otherwise double every stage slower than its threshold; and without spending an
  attempt, a stage that kills its worker would cycle through the queue indefinitely. `exists`
  failing on a Redis outage counts as "held", because a recovery stampede is worse than a
  delayed retry.
- **One tool name for four blocks.** `save_program_draft` is offered by every stage with the
  argument schema of that stage's block (`STAGE_BLOCK`), rather than four tools with four
  names. The model already knows which block its stage is for from the prompt; four names would
  have made "call the wrong one" possible and would have needed four handlers.
- **A citation must have been seen.** `search_sources` records the ids it returned on the
  context, and a save resolves every cited id against that set: an invented id and a plausible
  but unseen id are refused the same way. The catalog is empty until the RAG index (#11), so
  the prompts and the refusal hints say to leave citations empty rather than invent ids, and
  the sources stage stores an empty list — which the draft accepts, because a programme
  without citations is a weaker document, not an invalid one.
- **The schedule check is one-sided.** `days x hours_per_day >= total` is a ceiling rather than
  an equality, because the final attestation sits outside the curriculum: the canonical example
  schedules 72 hours for 66 hours of topics. The equality against `requested_hours` is checked
  separately and only when the methodist named a volume.
- **Finalisation renames the chat, and only once.** The programme title is a better chat name
  than "Новый чат", and the rename happens in the same transaction as the rendered message so a
  failed save cannot leave a renamed chat without the draft it was named for.
- **Confirmation is the `approve_program_draft` tool call, not an endpoint and not a phrase.**
  #3 allows no new route, and a phrase matcher would be dead code until the chat worker is
  wired anyway. The model sees the rendered draft and the tool, and calls it when the
  methodist confirms; the tool is only accepted once `finalized` is true, which keeps a
  confirmation from overtaking the transaction that renders the draft. An approved programme
  then continues as an ordinary chat: the next stage (#5) discusses the document.
- **The mock has a `stage` mode.** Every other cycle test scripts the provider, and the wire
  format of a tool call is exactly the part that a scripted provider would not catch. The mode
  scripts one valid call per stage, so the Docker and e2e stacks can run the whole cycle with
  no model, and a single test proves the fabricated blocks pass the cross-block checks.

## Refusals the model can act on, and the flags a run ends with (2026-10-20, review of #3)

The review of the cycle found five places where the pipeline was correct but the person or the
model on the other end was not served. These are the rules that came out of it.

- **A block is validated when it is saved, not only when the draft is assembled.** The hours
  stage already holds the requested volume and the declared outcomes, and the schedule is in
  its own block, so `_save_hours` runs the same checks `to_draft` would and refuses the block
  in the reply that produced it. `to_draft` stays the final gate — a summary can still cite a
  source that a later change removed — but a fully built four-stage programme is no longer
  discarded over one number the model could have fixed in the same turn.
- **The intake dispatches through `call_executor`, not `execute_tool`.** `execute_tool` runs
  whatever the registry knows; only `call_executor` refuses a tool the turn did not advertise.
  Without it, a model that remembered `save_program_draft` could rewrite the blocks of an
  approved programme, because an approved run is deliberately handed back to the model as an
  ordinary chat.
- **The rendered draft does not invite an edit it cannot accept.** The only way out of
  `PENDING_APPROVAL` is the `approve_program_draft` call (`begin_program` refuses a live run),
  so the closing line asks for confirmation and says the remarks can be discussed after
  approval.
- **`failed` is the only terminal flag; `status` is left as it was.** There is no
  `ProgramStatus.FAILED`, and inventing one would have made every `status` reader a two-value
  switch. The envelope therefore reads `generating` with `failed: true` and the reason in
  `error` — worth knowing before reading the Redis mirror an operator inspects.
- **Finalisation renames only a default-named chat.** The intake may already have named the
  chat from the model's reply, and that name is the user's; the programme title replaces only
  `DEFAULT_CHAT_NAME`, exactly as `process_response` does.
- **Recovery ages the row before re-enqueueing a finalize, and skips a stale row.** Finalising
  writes no state of its own, so without a touch it would be re-enqueued every five minutes
  forever; and a row that moved between the scan and the save must not abort the sweep for
  every other chat. The stage lock, not `STALLED_AFTER`, is what sets the recovery latency —
  the cron refuses to touch a locked stage, so a killed worker's run resumes when the lock
  expires, and both constants say so.

## The pipeline stops at the chat worker (2026-10-20)

`app/tasks/api.py` belongs to someone else, so this branch carries the pipeline and nothing
that lives in the chat worker's turn.

- **`generate_chat_message` stays exactly as `main` has it.** The intake half of #3 — offering
  `INTAKE_TOOLS`, committing the state a `begin_program` call started and answering a chat that
  already holds a run — is not in this change, and neither is its test block.
  *Superseded on 2026-10-21, when the chat turn became a tool loop — see "The chat turn became
  a tool loop, and the programme got an API".*
- **The seam is what this branch owes that worker.** `INTAKE_TOOLS`, `ToolContext`,
  `execute_tool`/`call_executor`, `store.save` and `program_stage.kiq(session_id, stage, run_id)`
  are usable from `api.py` as written; the worker needs no new module, only the wiring.
- **The `stage` mock mode's intake branch is therefore unexercised.** It still scripts
  `begin_program` when that tool is offered, and each stage's block when `save_program_draft`
  is offered, which is what the Docker run exercised: a run seeded directly into
  `program_state`.
- **#3 stays open.** A merge delivers stages, finalisation and recovery, not "the agent asks
  for a course and gets a draft", which is the issue's acceptance criterion.

## One error base, and names instead of literals (2026-10-21, review of #32)

The review of the cycle asked for a single hierarchy, fewer comments and enums instead of
hard-coded strings.

- **Every pipeline failure derives from `app.agentic.errors.ProgramError`.** `reason` on each
  subclass is the sentence the methodist is told, so `tasks/program.py` catches one class and
  writes `str(error)`; the `match` that mapped exception types to Russian text is gone.
- **Wire values the pipeline compares are enums.** `Role` and `FinishReason` live in
  `app.providers.models`; a stage's attempts are keyed by `Stage`, not by its value, so
  `state.attempts[Stage.HOURS]` reads the same before and after the JSON round trip.
- **Comments describe behaviour, not the history of the feature.** The rationale and the
  review notes go here; files the change only passes through keep the comments `main` had.

## The GigaChat contract, one system prompt, and a lock that stays alive (2026-10-21, review of #32)

The second review of #32 found two contract blockers (the tool round could not work against the
real API), a missing live round test, and several places where a comment or a safety net was
weaker than it looked. These are the rules that came out of it.

- **`tool_state_id`, not `tools_state_id`.** The GigaChat v2 schema names the field
  `tool_state_id` on a message and on the messages in a response. The old spelling went out as
  an unknown key, so the round id was never sent, and was never read back, so the next round
  could not be tied to the previous one. The internal `Message` and `GenerationResponse` use the
  contract spelling verbatim; there is no alias to keep in sync. `docs/gigachat.yml` is
  gitignored, so the excerpt this branch relies on lives here:

  ```yaml
  Message:
    properties:
      tool_state_id:
        type: string
        nullable: true
  Content:
    properties:
      function_result:
        type: object
        properties:
          name:
            type: string
          result:
            type: string
  ```

  `function_result` has no `id`, and `result` is a JSON-encoded **string**, not an object.

- **`FunctionResult.result` is typed `str`.** The loop serialises the payload once with
  `json.dumps(..., ensure_ascii=False)` and the OpenAI `tool` message sends the same string as
  its text. A dict can no longer reach the wire by accident.
- **The wire conversion is one pure function.** `_to_gigachat_message()` drops the OpenAI-only
  message keys and parses `arguments` from a string into an object. Invalid JSON is logged as
  `request_message_invalid` and returns `None` — the provider's failure convention — instead of
  sending a body GigaChat would reject.
- **The tool-result round has two rules the published schema omits.** Live probing of GigaChat
  v2 shows the only accepted result shape is a bare
  `content: [{"function_result": {"name": ..., "result": "<json-string>"}}]`:
  `tool_state_id` belongs to the assistant message and must not be repeated on the `tool`
  message, or the API answers `every assistant function call must have a result in history`;
  and a `text` sibling next to `function_result` is rejected with `function content must
contain function result or files entries only`. Both are stripped in
  `_to_gigachat_message()`, which leaves the shared model and the OpenAI client carrying the
  text and the state id they need. The schema excerpt above describes the first half of the
  contract only; these two rules are the other half.
- **One system message per request.** GigaChat rejects a second system message, so a stage's
  conversation is exactly `STAGE_SYSTEM_PROMPT` plus the brief; the chat worker must not append
  the session system prompt to a stage history. The admin `prompt_extension` from
  `harness.build_system_prompt` therefore does not reach programme stages. Applying it would
  mean carrying the extension in `ProgramState` from `begin_program`, which is the chat
  worker's turn (`app/tasks/api.py`); the stage prompt is the tool contract, not the session
  persona. This is deliberate, not an oversight.
- **The worker session lives next to the engine.** `app.database.db_session()` is the one
  session context manager for task bodies and reusable modules; `tsq_db` in
  `app/tasks/conf/broker.py` is now an alias so existing task imports stay untouched.
  `app.agentic.store` imports the neutral name instead of reaching into `app.tasks`, and the
  test fixture patches only the session maker.
- **`FOR UPDATE` stays, and the transaction is explicit.** `store.locked()` is
  `async with db_session() as db, db.begin():` and commits on a clean exit. The row lock orders
  two writers; the version on the envelope refuses one that loaded the row long ago.
- **The stage lock is renewed before every provider turn.** The Lua compare-and-expire only
  extends a lock still owned by this worker; losing it raises `StageSuperseded`, the stage
  returns quietly, and recovery re-enqueues it later. No duplicate provider call is made and
  the run is not marked failed. `PROGRAM_STAGE_LOCK_TTL` (900 s) is therefore safe for a stage
  that runs longer than it, and recovery is not delayed by a dead worker. A Redis outage during
  the refresh counts as "still held", matching the recovery policy.
- **`StaleState` is handled at both saves.** Marking the stage started and completing it are
  inside the protected block, as are the failure saves, so a worker that lost the version
  between reading the row and starting stops silently instead of failing the task.
- **`Role` and `UserRole` stay separate.** The provider vocabulary is wider (`tool`) and a chat
  row can never be one; a test pins the overlap so a rename on either side fails loudly.
- **Live tests cover the whole round.** The offline suite cannot prove that GigaChat accepts a
  `function_result` part; `test_live_gigachat_completes_a_tool_round` and its OpenAI twin run
  model -> tool -> model through `run_tool_loop`, so the exact shape that blocked the merge is
  exercised by the real API. They stay deselected by default and need a real key.

## The chat turn became a tool loop, and the programme got an API (2026-10-21)

The programme cycle was merged with a seam and no caller: the tools existed, the stages
existed, and the chat turn was still a single structured completion. This change wires the
two together and gives the client something to poll.

- **The chat turn is a tool loop.** `generate_chat_message` is now `run_chat_turn`: it offers
  `INTAKE_TOOLS` to the model and runs `run_tool_loop`, so the model itself decides whether
  to start a programme, to confirm a rendered draft, or to answer in prose. A structured
  completion could not express any of that — the model had no way to say "start a programme".
  The reply is prose now, and `HARNESS_STRUCTURED_SCHEMA` and `HarnessStructuredOutput` are
  gone with it; the tools' specifications are the contract the model answers against.
- **The model no longer names the chat.** Naming a chat from a model's own words was a
  structured-output side effect, and structured output is gone. `app/naming.py` holds the
  rule instead — *a programme's title wins, otherwise the first thing the methodist said* —
  and the intake, the finalise and the failure path all go through it, so a chat is named
  once and the frontend sees one name. The title may replace the name the first message
  gave, because a programme's own title is the better name.
- **A run may wait for answers.** `begin_program` takes an optional `questions` list. A run
  with questions is created in `AWAITING_ANSWERS` and does not generate: the questions live
  on the envelope, so the client can show them one at a time and send the answers back. The
  alternative — a second endpoint for the answers — would have split one user action across
  two contracts, and the chat turn would have had to answer a question it was not asked.
- **The answers travel with the message.** `POST /chats/{session_id}` takes an `answers`
  array next to `content`, and the answers are checked against the questions the run is
  actually waiting for: every open question needs exactly one answer, and an answer to a
  question nobody asked is refused. The answers are repeated in the message next to the
  question each one answers, so the transcript reads as a conversation and the model sees
  what the methodist said.
- **`GET /chats/{session_id}/program` is the polled endpoint, and it reads Redis first.** The
  state is already mirrored under `program:state:{session_id}`, so a client that polls the
  programme's state does not touch PostgreSQL; a miss falls back to the row and re-mirrors
  it. `running` comes from the stage lock, so a client can tell a programme that is being
  worked on from one that stalled — the status alone cannot, because a run keeps its status
  after the worker dies.
- **The view a client sees is the programme's, and it lives beside the programme.** The
  status projection (a run that died reads as failed), the blocks that are already saved and
  the open questions are rules about a run, not about the transport, so they are built in
  `app/agentic/view.py` and the route only reports them. A route module that grows helper
  functions is a route module that has become a utility module.
- **A tool's effect is committed when it happens, not when the turn ends.** The stage loop
  checkpoints after every call; the intake turn does the same, so a model that starts a
  programme and then answers nothing still starts the programme. Without the checkpoint the
  run existed only in memory and the turn reported an error over it.
- **One writer for the result row.** `generation_results.chat_session_id` is the primary
  key, and two writers can reach it: a turn publishing its reply and the programme
  finalising a draft. The writer is `app.models.chat.write_result`, which updates the row
  when it is already there, so the second writer cannot fail on the key.
- **A chat is named once, and the title wins when it exists.** Until a programme has a
  title the chat takes the name of the first thing the methodist said — a running
  programme must not be "Новый чат" in the chat list — and the title replaces that name
  when the draft is rendered. One rule, one place (`app/naming.py`), three callers.
- **A client polls on `busy`.** `running` is a live hint: it is true only while a worker
  holds the current stage's lock, so it is false while a stage waits in the queue and false
  during the finalisation, which has no lock. `busy` is true for as long as the programme is
  being worked on, the finalisation included, and it is the field a client polls on.
- **`GET /chats/programmes` lists what the user has.** A chat counts as a programme once it
  holds a run, whatever the run's state, and the title is the programme's own title with
  the chat's name as the fallback. The list is paginated the way the chat list is, because
  it will grow the same way.
- **The admin's prompt extension still does not reach the stages.** The intake is a chat
  turn, so the extension reaches the model that decides to start a programme, but the
  stages write the document and build their own prompt. Carrying the extension there would
  mean storing it in `ProgramState` from the intake, which is a product decision, not a
  mechanical one.

## The programme catalogue reads the API, and the UI keeps the fields it cannot show yet (2026-10-21)

`GET /chats/programmes` and `GET /chats/{session_id}/program` exist, so the mock catalogue
in `ProgramsService` was replaced by them. The client maps the API onto the existing view
model instead of redesigning the page: the card and the detail aside stay, and fields the
catalogue does not return yet keep their elements.

- **The whole catalogue is fetched, then filtered and paged in the client.** The endpoint
  paginates but does not filter, and the page has type, month and tag filters. Fetching one
  page at a time would filter only the page, so `ProgramsService` walks the API in
  `items_per_page=100` pages and applies the existing filters to the full list. The
  catalogue is small; a server-side filter contract should replace this when it exists.
- **The list and the detail return different amounts.** The catalogue has title, status and
  `updated_at`; type, hours, description, goals, competencies and modules come from the
  draft only when a programme is opened. The service merges a detail response into the list
  item, so the card and panel update without a second source of truth.
- **Fields the backend may add later are optional in the API model.** `type`, `direction`,
  `hours`, `tags`, `author`, `description`, `goals` and `theme` are accepted when the
  catalogue returns them and fall back to placeholders otherwise. The elements stay in the
  template, because removing them would have to be undone when the contract grows.
- **A guest gets no catalogue.** `/programs` is an auth-gate preview, but
  `/chats/programmes` is authenticated. The page loads only when `ProfileService.user()` is
  set and clears when it is not; the overlay remains, the list is empty. Serving mock rows
  to a guest would be the old mock by another name.
- **Duplicate, archive, restore and delete are still local.** There are no write endpoints
  for a programme, so these actions change the loaded list and are lost on reload. They stay
  because their menu items are part of the page; the service is where they will call the
  API once it exists.
- **The page polls `busy`, not `running`.** The detail request repeats every two seconds
  while `busy` is true, and stops on close, on deletion of the selected row, or when the
  run stops. This follows the backend contract: `running` is only a stage-lock hint.
- **The e2e stack seeds programme rows.** `/programs` is now empty without data, so
  `e2e/scripts/setup.sh` writes eight minimal `chat_sessions.program_state` envelopes for
  the e2e user. The list endpoint needs only title, status and `updated_at`; the detail
  endpoint accepts the model's defaults for the rest.

## The programme is written in two halves (2026-10-22)

The criteria ask the methodist to approve the goal, the competences and the hours *before* the
agent writes any content. The pipeline therefore has two groups of stages, not one.

- **`SKELETON_ORDER` is what the methodist approves.** Competences, hours, sources and the
  passport: the goal, the competences and the hours. When the last of them is done the run
  waits in `pending_approval` and `finalize_program_draft` renders it into the chat.
- **`CONTENT_ORDER` is what is written from the approved skeleton.** The assessment and the
  materials. They are not enqueued by the stage that finished the skeleton: the approval is
  what puts the first of them on the queue, so nothing is generated from a skeleton the
  methodist has not seen.
- **The approval is the intake's business.** `approve_program_draft` moves the run back to
  `generating` — the content is being written, so the run is working, not finished — and the
  intake task, which is the only place that can enqueue, starts the content. The pipeline
  itself never enqueues a content stage on its own: `_advance` stops there.
- **A finished programme is `approved`.** The content's last stage completes the run; the
  skeleton waiting for the methodist is `pending_approval`, and a run that is being written is
  `generating`. The status vocabulary the frontend knows is unchanged.

## The assessment is checked, not trusted (2026-10-22)

The criteria ask for every learning outcome to be checked by at least two means. That is a
check on the block, not a wish in the prompt.

- **Every means names what exists.** A question, a seminar task and a form of control have to
  name outcomes the competences declare and topics and forms of control the programme
  actually has. The final attestation has to name declared outcomes too, but it is the
  programme's conclusion and not one of the means, so it does not count towards the two.
- **Every outcome is checked at least twice**, and the count is over the questions, the
  seminar tasks and the forms of control.
- **The attestation's hours are the plan's hours.** The plan sets them aside
  (`Curriculum.attestation_hours`) and the assessment has to match, which is what makes a
  256-hour internship with 254 hours of self-study add up.
- **The materials belong to the plan.** A lecture plan or a slide deck that names a topic the
  plan does not have is refused.

## The report the methodist reads (2026-10-22)

The criteria ask for a self-check report on the hours, the coverage, the sources and the
completeness of the sections. The rules already exist — they are what the stages are held
to — so the report is the same rules, run once and shown.

- **`ProgramState.report()`** answers with the four checks and the numbers behind them:
  the hours (asked, total, topics, attestation, contact, self-study), the coverage (how
  many outcomes, how many are taught, how many are checked twice), the sources (how many,
  how many confirmed) and the sections that are filled.
- **The report works on a half-finished run.** A check that cannot be made yet says so
  instead of raising, so a client can poll it while the programme is still being written.
- **It is part of `GET /chats/{session_id}/program`**, next to the blocks: the client
  reads the state and the verdict from one place.

## The programme leaves the system as a document and as slides (2026-10-22)

The criteria ask for the programme as a DOCX by the АЦР template and for the slides of at
least one module as a PPTX. The renderer came from the `feat/backend-docx-gen` branch and
now renders the whole programme rather than the skeleton alone.

- **The document is the programme, not the draft.** `render_program_document` takes the
  `ProgramDocument` — the approved draft and the content written from it — and renders the
  assessment and the materials as real sections instead of the "to be filled in" block. The
  draft alone still renders, for a programme that has not reached its content yet.
- **The slides are the decks the materials stage wrote.** `render_slides` builds a PPTX with
  a title slide and one slide per bullet. A caller can ask for one module's decks; the
  decks are written per topic, so the whole set is what there is to hand out.
- **The exports read the canonical row**, through the request's own session, because a
  stale mirror would hand out a document that is not the programme.
- **A programme that is not finished cannot be assembled**, and that is a conflict rather
  than a missing resource: the run is still going. The endpoint says so with 409.
- **A seminar task has criteria.** The schema had none, and the criteria ask for them, so
  `SeminarTask.criteria` was added.

## The references reach the model as files (2026-10-22)

The criteria ask the agent to take a request with references in .docx, .pdf and text, and
case 3 asks it to follow the structure of the programme that was attached — with new content
rather than the old text.

- **The references are files the model reads.** The provider accepts .docx the same way it
  accepts a PDF, so the attachments go up with the message and the model sees them; the
  backend does not have to extract the text to pass it on. The app accepts .docx uploads
  for that reason.
- **The prompt says what to do with them**: use the attached files as an example of the
  structure and the volume, but do not carry their text over — the content has to be new.
  That is a prohibition on the solution, so it belongs in the instructions the model
  follows, not in a check that could only catch the obvious.
- **The text of the request is the requirements** the run already carries, so a request in
  plain text needs nothing else.

## The catalogue is the corpus, and the corpus marks its trust (2026-10-22)

The criteria ask for two things about the sources: every document a programme cites has to
exist, and a source nobody confirmed has to be marked. The corpus in `edu_materials/` answers
both, and it is the corpus itself rather than a hand-written list.

- **The catalogue is read from the corpus descriptions.** Each corpus directory carries a
  description of its documents — id, name, type, the order that approved it, where it came from
  and how far it may be trusted — and `app/agentic/sources.py` parses those into the records the
  programme cites. A document that is not in the corpus cannot be confirmed, and the corpus is
  the only authority on whether it exists.
- **The backend decides whether a source is confirmed, not the model.** `Source.confirmed` is
  set when the source enters the programme: a document the catalogue holds is cited with the
  catalogue's record, anything else keeps what the agent wrote about it and is marked as
  unconfirmed. The mark shows in the chat reply, in the report and in the DOCX.
- **A refusal was the wrong answer for a bibliography entry.** The stage used to refuse a
  source the catalogue did not hold, which threw the document away and told the agent nothing
  about it. The criteria ask for the document to be marked instead, so that the methodist sees
  what nobody verified. A professional standard a competence rests on is still a refusal: a
  competence has to stand on a standard that exists.
- **The passport may only rest on documents the programme already lists.** The check moved from
  the catalogue to the programme's own source list, because the bibliography and the passport
  have to agree.
- **The search is lexical for now.** A keyword match over the descriptions, with the title
  counting double, answers the agent offline and in the tests. The semantic index over the
  corpus arrives with the vector store; `search()` is the seam, and both answers are the same
  shape, so nothing above it changes.

## One part of the programme can be redone on its own (2026-10-22)

The criteria ask the methodist to be able to change one part — replace a topic, make a test
harder — and to have the agent rebuild only that, without breaking the rest. A revision is
therefore a stage that runs again, not a new run.

- **The methodist asks for it in the chat.** The intake offers `revise_program` with the stage
  to redo and the notes; the tool records the revision on the envelope and nothing else. The
  chat is the only entry point, so the frontend needs no change to offer the capability.
- **The revision reruns one stage and leaves the rest alone.** The stage goes back to pending,
  the run starts working again, and the stage's brief carries the methodist's notes. The
  blocks that are already committed stay committed, and the checks that tie the blocks
  together are what keeps them consistent.
- **The run does not walk the pipeline again.** A revised stage goes straight back to where the
  methodist left the programme: the skeleton waits for the methodist again, the content is
  complete. The stages that were already done are not run a second time.
- **A revision is not a failed attempt.** The budget of three attempts per stage is there for
  the agent's own failures, so a revision spends none of it.
- **The draft is rendered again.** The revision clears the "rendered" flag, so the methodist
  sees the changed draft and confirms it again.

## The online search reaches the web, and the web is not the publisher (2026-10-22)

The criteria ask the agent to look for actual sources, and the corpus alone cannot answer that:
it holds the documents the organisation already trusts, not the current versions of everything
else. So the search has to reach outside it.

- **The search has two sources and one shape.** `app/agentic/websearch.py` asks the
  encyclopedia by default — it needs no credentials, and it covers the federal educational
  standards and the professional standards by their names and numbers — and a deployment with
  a search service of its own points `WEB_SEARCH_URL` at it. Both answer the same question in
  the same shape, so nothing above the search changes.
- **What the web finds is not what the corpus confirms.** A document the encyclopedia holds is
  an internet resource and it is marked as unconfirmed: the encyclopedia is not the publisher of
  an act. The catalogue alone confirms a document. That is the marking the criteria ask for,
  and it is now reachable from outside the corpus rather than being a rule the model has to
  remember.
- **The search is off on the mock provider.** The stack runs on the mock, the end-to-end suite
  among it, and a search that reached the network would make the suite depend on the internet.
- **The GigaChat built-in search was not used.** The built-in function's result shape is not in
  the API schema, the key that reaches `api.giga.chat` is not available on the development
  machine, and the router in front of it exposes no built-ins. Shipping a protocol guess that
  cannot be run would be worse than a search that is known to work.

## A revision drops the parts that were written from the revised one (2026-10-22)

The first version of the revision kept the dependent blocks, and that was wrong: a curriculum
whose topics name outcomes the competences no longer declare, or an assessment whose forms of
control name topics the hours no longer have, is a block that points at something that is
gone. The finalisation then refuses the programme and the methodist is told that a document
they never touched is broken.

- **What a revision invalidates is written down**, in `REVISION_INVALIDATES`: the competences
  invalidate the curriculum and the content, the hours invalidate the content, the sources
  invalidate the passport. What was dropped is written again — the stages that write it go
  back to pending.
- **The run continues with what is missing.** A revision of the skeleton leaves the run
  working on the skeleton; the content waits for the methodist's approval of the changed
  skeleton, as it did the first time.
- **A revision gives the stage its budget again.** The attempts of the stages that run again
  start at zero: the budget of three is there for the agent's own failures, and a change the
  methodist asked for is not one.
- **The approval points at the work that is missing.** The run does not remember which part
  of the content is still missing, and an approval that enqueued the stage the run happened
  to be on left the content unwritten until the attempt budget was spent.

## The attestation is contact time (2026-10-22)

The plan has to add up. The attestation's hours were counted in the volume and left out of
the contact hours, so the rendered table showed a column that added up to more than its own
total, and the chat line said that 38 contact hours and 28 hours of self-study make 72. The
attestation is now contact time — nobody sits an exam alone at home — and the totals add up
in both the document and the chat.

## The remote share and the daily cap are shown (2026-10-22)

A programme that is очно-заочная says how many of its hours are remote, and a programme
with a daily cap says what the cap is. Both were collected and checked and then appeared
nowhere: the passport said "очно-заочная" while the plan said nothing about how the hours
are delivered. They are in the document's schedule section and in the chat's line now.

## The exports are files the client fetches by id (2026-10-22)

The exports were endpoints that answered with the document itself. That is a second contract
for the client to learn, and the client already has one: an attachment is fetched by its id
through the attachment route, and the frontend's download service speaks exactly that.

- **The exports are attachments.** A task writes the document and the slides into the
  attachments bucket, registers them as attachments of the chat and records their ids on the
  programme's state. `GET /chats/{session_id}/program` carries them in `exports`, and the
  client asks the attachment route for the link — no new endpoint, no new client code.
- **The export is written when the programme is complete,** and again whenever a revision
  changes it: the exports are the programme, so they are as stale as the state they were made
  from. One file per programme — a second export replaces the first rather than adding
  another object and another row.
- **The files reach the stages, not only the intake.** The attachments are on the chat
  messages, so the chat turn could read them and the stages could not: a stage saw the
  intake's description of the reference rather than the reference. The stage task now passes
  the files of the request into the brief's message, so a stage reads the template or the
  approved programme the request came with.

## Programme sources use the school hub and a pgvector index (2026-10-22)

The programme stages need real documents behind `search_sources`, and the parser already
produces text chunks and tables. The integration is deliberately split into an offline
ingestion step and a feature-flagged query seam, so the pipeline works unchanged until an
index has been built.

- **One hub for chat and embeddings.** The OpenAI-compatible hub is the only endpoint with
  an embeddings API; `PERS`/`B2B`/`CORP` have none. `SBER_API_SCOPE=openai` therefore selects
  both the chat provider and the embedding provider, and the mock provider remains the
  offline path for tests and e2e.
- **The vector is fixed at 2048 dimensions.** The embedding model is
  `ai-sage/Giga-Embeddings-instruct-3B`; `OPENAI_EMBEDDING_DIMENSION` is checked against
  `EMBEDDING_DIMENSION` in `app/rag/config.py` before the provider is returned. A deployment
  that changes the model needs a migration, not just an environment edit.
- **The column is `halfvec(2048)`, not `vector(2048)`.** pgvector's HNSW index refuses
  `vector` above 2000 dimensions; `halfvec` supports 4000 and has `halfvec_cosine_ops`. The
  store, the model and the migration all use the same `HALFVEC` type, so the index and the
  `cosine_distance` query stay on one representation. The precision loss is acceptable for
  an educational corpus and is the price of an HNSW index without a second column.
- **Citations use catalog ids, not parser ids.** The parser's `doc_id` is a content hash,
  while `ProgramState` requires `src-NN`. `backend/res/rag-sources.json` is the curated
  catalog that assigns those ids and maps each to a `source_path`; ingestion joins parser
  output to it, and the same catalog is the only thing `search_sources` can return. Ids are
  canonicalised in the tool seam so `src-1` and `src-01` cannot become two citations.
- **A scanned source may legitimately have no chunks.** FGO PDFs are in the corpus now, OCR
  is not. `allow_empty: true` lets a scanned document pass ingestion with zero chunks instead
  of failing the other nine sources; `--strict` still reports it as a warning, and the index
  does not invent text.
- **Ingestion is a one-off CLI first.** `backend/index_rag.py` is an operator command, not an
  HTTP endpoint: the corpus and the hub are not yet proven in a deployment, and indexing is
  a privileged, expensive operation. It is idempotent by fingerprint, supports `--dry-run`,
  `--prune` and `--strict`, and stays the only writer of `rag_chunks` until the flow is
  proven.
- **Search is stage-only and failure-tolerant.** `search_sources` is offered by
  `stage_tools(stage)` for the stages that cite documents, never by the chat intake, because
  an intake turn that searched could start a second run mid-document. A provider or database
  failure becomes `RagUnavailable`, which the tool turns into a model-visible note and
  `found=false`; a stage continues without citations rather than dying. An empty result is a
  normal answer, not an error.
- **The backend image builds from the repository root.** It needs both `backend/` and the
  sibling `rag/` package, so the Compose backend context is `.`, and the root
  `.dockerignore` keeps the frontend, e2e, parser and test trees out.

## The RAG index and the corpus catalogue answer the same question (2026-10-22)

The RAG integration (PR #46) and this branch both wrote the source catalogue, and they were
merged rather than kept apart. The two catalogues describe the same ten documents, and a
document has one id.

- **The curated catalogue is the citation authority.** `backend/res/rag-sources.json` holds
  the `src-NN` ids the programme cites and the paths the ingestion joins the parsed corpus to.
  A source the catalogue holds is confirmed.
- **The corpus descriptions are the offline path.** The descriptions in `edu_materials/` are
  the same documents in prose. They need neither an index nor an embedding provider, so a
  deployment that has not built the index, the test suite and the end-to-end stack still find
  the documents. A corpus entry is filed under the catalogue's id, so a programme cites
  `src-04` and not `profstd-06.001` for the same standard.
- **A source nobody confirmed is kept and marked, not refused.** The RAG integration refused
  an unknown citation, which is the opposite of what the criteria ask for: the methodist has to
  see which documents nobody verified. The catalogue's record wins where there is one, and a
  document without one keeps what the agent wrote and is marked as unconfirmed. A professional
  standard a competence rests on is still a refusal, because a competence has to stand on a
  standard that exists.
- **The search asks the index first and the catalogue second.** An index that is down is a
  note to the model, and the catalogue still answers.
- **The score floor is 0.30, measured.** On this corpus an unrelated query scores about 0.18
  and a related one about 0.44, and the two distributions do not overlap. The default of 0.15
  sat below the junk the measurement excludes, so it was raised.

## A revision drops what was written from the part that changed (2026-10-22)

The first version of the revision kept the dependent blocks, and the programme then failed
its own checks: the assessment still named a topic the new hours no longer had, and the
finalisation reported a broken document over a part the methodist never touched.

- **What a revision invalidates is written down**, in `REVISION_INVALIDATES`: the competences
  invalidate the curriculum and the content, the hours invalidate the content, the sources
  invalidate the passport. The block is dropped and the stage that writes it goes back to
  pending, so it is written again from the part that changed.
- **The run continues with what is missing.** A revised skeleton waits for the methodist
  again; a revised part of the content leaves the programme complete, and the exports are
  written again so the document and the slides match the change. The chat is told, because
  the transcript still shows the draft the methodist approved before the change.
- **A revision gives the stage its budget again.** The three attempts of a stage are there
  for the agent's own failures, and a change the methodist asked for is not one.

## A section is filled when it holds something (2026-10-22)

The self-check asked whether a section existed, and an assessment without a question and a
set of materials without a slide passed it. The report now looks at what a section holds,
and the stages refuse a block that is empty: a curriculum with no lecture plan and no
slides is a curriculum with nothing in it, and the criterion asks for the slides of at
least one module.

## The methodist's decisions are endpoints, and the rules are shared (2026-10-22)

The frontend asked for endpoints to approve a draft and to ask for a revision, because the
model's tool calls are a guess: the methodist has to be able to press a button.

- **The two surfaces share one set of rules.** `app/agentic/actions.py` holds the conditions
  under which a decision is allowed, and both the model's tool and the HTTP endpoint call
  it. A rule written twice is a rule that will drift, and the two surfaces have to refuse
  the same programmes for the same reasons.
- **The approval is the methodist's own decision, so it is recorded.** The state keeps who
  approved and when, and the revision keeps who asked for it and when. Without the record an
  explicit approval endpoint is indistinguishable from the model confirming the methodist's
  words, and nobody can say who let the programme be finished.
- **The endpoint answers with the programme's state,** in the shape `GET /program` answers
  with, so a client renders the answer without a second request. The pending revision comes
  with it, because the methodist's own notes are otherwise nowhere to be seen.
- **An approval spends an attempt and a revision spends none,** exactly as the model's path
  does: the release of the content is a stage like any other, and a change the methodist
  asked for is not a failed attempt.
- **The endpoint writes the state through `app.agentic.store`,** on its own connection, so
  the state is committed before the stage is enqueued. A worker that picks the task up
  immediately therefore reads a state that already says what it is doing.
