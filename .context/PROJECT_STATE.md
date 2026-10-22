# Project state

Rewritten in place on each update. Do not append. Keep it short and current; detail belongs
in `DECISIONS.md` (rationale) or `AGENTS.md` (procedure).

Last updated: **2026-10-22** (the chat route became a programme workspace with a live
preview, quick questions and voice input; mock mode now creates chats and opens catalogue
programmes in their chat)

---

## Current status

The project was an AI quotation engine for sheet-metal fabrication — drawings in, a priced
commercial offer out. It is now a **bare chat application with file attachments**, awaiting a
purpose. Every commercial concept is gone: no `processing` package, no DXF parser, no
`calc.xlsx`, no `positions` in the LLM contract. See "Repurposed from a quotation engine to a
chat application" in `DECISIONS.md` for why each piece went.

Everything described below is working and running via Docker Compose.

| Component   | State                                                                                                                                                     |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/`  | Complete: auth, users, admin, chat, files, TaskIQ workers, Alembic, GigaChat harness                                                                      |
| `frontend/` | Complete: guest mode, sign-in dialog, chat create, programme workspace with live preview, history, profile, admin users, admin settings, programs catalog |
| `e2e/`      | Complete: specs with bounded timeouts against the real Compose stack with the offline provider                                                            |
| Infra       | 13 Compose services, dev and memory-limit overrides, healthcheck-gated startup                                                                            |

**Backend.** FastAPI on Python 3.14. Cookie auth (argon2), admin with account expiry, chat
sessions with async generation, attachment upload / list / delete, presigned S3 results.
Alembic has the squashed `initial` revision (`d7e256a71a55`) and the programme-state column
(`f3a9c1d7b2e4`). Twelve TaskIQ tasks across the `default` and `network` queues, with hourly,
ten-minute and five-minute recovery crons.

**Frontend.** Angular 21.2 standalone with signals. The panel is browsable as a guest: the
session is resolved by a `provideAppInitializer`, `/home` and `/help` are public, every other
route renders behind an `AuthGate` prompt, and `/login` is gone in favour of a shared
`AuthModalService` dialog. Reactive forms, cookie auth, 2-second polling for generation
results, inline preview for PDF, image, docx and xlsx, light/dark theme, admin user
management, admin prompt-extension editor. Section switches animate through the browser's
View Transitions API (`withViewTransitions`), direction derived from the sidebar order.

**Not started or placeholder.** The profile page body, the user settings page body, and two
admin sections (`Журнал действий`, `О системе`) render `SectionPlaceholder`. `/home` is a
single-column program-creation landing (animated brand mark, prompt examples, and a composer
docked to the bottom of the viewport while empty; once the user types or attaches a file the
prompt examples fold away, the composer rises into the freed space at its normal height and
reveals the attach action, with drag-and-drop). A send from `/home` starts a chat: an
authenticated user goes straight to it, while a guest's text and files are held in
`PendingChatDraftService`, the sign-in dialog opens, and the same chat is created and sent
after login. The composer also dictates: a microphone button to the left of send starts
continuous Russian recognition through the Web Speech API, fills the field with interim and
final text, and turns red while recording; the button is disabled when the browser has no
engine (Firefox keeps it behind an off-by-default preference). Attached files render as
tiles inside the composer field, above the textarea: images show a small preview, other
types the same tile with a file icon. Clicking the tile opens the preview (there is no
separate preview button); the file name and remove action appear on hover and keyboard
focus, and stay visible on touch devices. `/help` is still an
intentional `SectionPlaceholder` host, `library` renders the same placeholder behind the
auth gate, and `/programs/new` uses it until the form exists. `/programs` is now
`Мои программы`, a lazy-loaded workspace backed by `GET /chats/programmes`.
`ProgramsService` fetches every page of the catalogue (the endpoint caps a page at 100),
keeps filters and the six-item paging client-side, and opens a programme through
`GET /chats/{session_id}/program`; a run whose `busy` is true is polled every two seconds.
The detail aside fills the description, goals, competencies, curriculum modules and hours
from the returned draft; type, tags and author stay in the card and panel and are read when
the catalogue starts returning them, with placeholders until then. A guest sees the page
behind the blurred overlay, but the catalogue is empty because the endpoint is
authenticated. Duplicate / archive / restore / delete still mutate the loaded list only:
there are no endpoints for them yet. The card and panel action menus use the menu-button
keyboard pattern with roving tab focus, and the detail panel remains a non-modal right-side
aside that fills the viewport below 980px. The catalogue has no create button; creation
starts from `/home` and lands on the chat workspace. Pressing «Открыть программу» in the
detail aside navigates to `/chats/{session_id}`, so the catalogue and the programme
workspace share the same chat route.

**Mock mode now covers the write path.** `start:mock` keeps a mutable chat store in
`mock-api.interceptor.ts`: `POST /chats` creates an empty session, `POST /chats/{id}`
appends the methodist's message and a deterministic reply, and history, `/result` and
`/program` resolve both seeded, newly created and catalogue-programme sessions. An
ordinary new chat reports programme status `none`; opening `mock-programme-*` serves its
transcript and draft. The waiting mock programme exposes questions and clears them when
the answers are posted. Mock uploads remain unsupported because the presigned PUT is a raw
`XMLHttpRequest`; creation from `/home` works for a text-only message.

**The chat page is a programme workspace (#45).** `/chats/:id` now renders `ChatPage`:
breadcrumb back to `/programs`, a five-step stepper, the chat card and a live
«Каркас программы» preview. The page polls `GET /chats/{id}/program` every two seconds while
the run is `busy` or a chat turn is in flight, stops while the run waits for answers, and
refreshes once a turn finishes. The preview renders only blocks the backend has actually
saved: title, kind, hours, form, goal, competencies and modules, with placeholders for what
has not arrived. The stepper maps `status` and `current_stage` onto brief / structure /
content / review / done and marks a failed stage. Progress is conveyed by the stepper and preview; there are no separate milestone cards in
the transcript. When the run is `awaiting_answers`, the
composer is replaced by a one-question-at-a-time form whose answers are submitted together
in the chat message's `answers` array; the optimistic transcript line mirrors the backend's
`render_answers()` text. The composer also dictates through the same Web Speech service as
`/home`. The files panel keeps its «Панель файлов» button but now overlays the workspace
instead of shrinking the chat. Confirming the rendered draft is still done in chat; there is
no separate approve button.

**The programme cycle (#3).** A chat can build one educational programme. A run is one
`ProgramState` in `chat_sessions.program_state` (JSONB), mirrored to Redis for an hour, and it
starts when a chat turn calls `begin_program`. The chat turn is a tool loop
(`run_chat_turn`): the model is offered `INTAKE_TOOLS` and decides whether to start a
programme, to confirm a rendered draft, or to answer in prose. One
TaskIQ task owns one stage — competences, hours, sources, summary — and each commits its block
before enqueueing the next, so a worker that dies leaves every finished block behind and
`recover_stalled_programs` restarts the stage that stopped. `finalize_program_draft` renders
the assembled draft into the chat, renames a default-named chat and waits for the methodist's
confirmation, which the model turns into an `approve_program_draft` call. `assessment`
and `materials` stay pending for #5; the source catalog behind `sources.py` is still empty
until the RAG index (#11), so a draft cites no documents today.

**The programme API.** `GET /chats/{session_id}/program` reports where a programme stands
(status, current stage, the blocks saved so far) and reads the Redis mirror before the row, so
a client that polls it does not touch PostgreSQL; `running` comes from the stage lock, so a
stalled programme is distinguishable from a busy one. `/program` and `/result` both expose
the open questions while a run is `awaiting_answers`; the workspace reads them from
`/program`, the endpoint it already polls. `POST /chats/{session_id}` takes the answers in an
`answers` array next to `content`.
`GET /chats/programmes` lists the user's programmes, paginated. A run may ask the methodist
questions: `begin_program` takes them, the run waits in `awaiting_answers`, and the answers
move it on. The catalogue and its detail aside consume both endpoints, and the chat workspace
now answers the run's questions. The draft is still confirmed conversationally: the model
turns the methodist's approval into `approve_program_draft`, and there is no separate approve
control. The chat's five-minute poll can time out before a slow run finishes; the programme
preview keeps polling on `busy` independently.

**Two dead placeholder directories** are still tracked: `frontend/src/app/shared/components/
chat-widget/` and `.../param-table/`, each holding only a `.gitkeep`. They are leftovers from
the quotation UI and nothing imports them.

**No LICENSE file exists.** The repository is treated as proprietary.

---

## Database

Two revisions. Eight tables (`users`, `sessions`, `chat_sessions`, `chat_messages`,
`attachments`, `generation_results`, `processing_result_uploadables`, `settings`) and two
enums (`userrole`, `generationresulttype`). `chat_sessions.program_state` is nullable JSONB:
`None` for an ordinary chat, the run envelope for a chat that is building a programme — there
is no separate table, because the run is a property of the chat and the row lock of the chat
is what serialises its writers. The TaskIQ dashboard keeps its own separate `taskiq_dashboard`
database on the same PostgreSQL instance, created by
`conf/postgres-init/01-taskiq-dashboard.sql`.

`processing_result_uploadables` is the artifact store a processed upload can write into. The
`artifacts` bucket exists and the model is there, but **nothing writes rows yet** — that is the
first extension point if a feature starts deriving files from an upload.

---

## Tests

| Suite             | Command                              | Needs                                                     |
| ----------------- | ------------------------------------ | --------------------------------------------------------- |
| Backend           | `poetry -C backend run pytest`       | needs a Docker daemon (testcontainers)                    |
| Backend coverage  | `poetry -C backend run pytest --cov` | gated by `fail_under` in `backend/pyproject.toml`         |
| Frontend          | `npm --prefix frontend test`         | Vitest; runs in `frontend-ci.yml`                         |
| Frontend coverage | `npx ng test --coverage`             | gated by `coverageThresholds` in `frontend/angular.json`  |
| End-to-end        | `./e2e/scripts/run.sh`               | Playwright against the Compose stack, `SBER_API_KEY=mock` |

**Both coverage gates are enforced**, each in exactly one place: `fail_under` in
`backend/pyproject.toml` and `coverageThresholds` on the `test` target in
`frontend/angular.json`. Read the figure off the `TOTAL` line or the builder's summary; neither
a pre-commit hook nor a workflow names a percentage, so they cannot drift apart. Frontend
branches are deliberately not gated.

Test counts and coverage percentages are deliberately not recorded here: they are
deterministic, the commands above print them in seconds, and a recorded copy goes stale
silently. See the rule in `AGENTS.md`.

PostgreSQL, Redis and Garage are real testcontainers in the backend suite. The LLM is faked by
`app/providers/mock.py`, selected with `SBER_API_KEY=mock` (or `SBER_API_SCOPE=mock`) and
switchable via `MOCK_PROVIDER_MODE` (`ok`, `empty`, `error`, `stage`); the real provider is
only reachable through `@pytest.mark.live` tests, which are deselected by default. Both live
provider suites carry a full model -> tool -> model round, because that is the one shape the
offline suite cannot prove the real API accepts. The `stage`
mode scripts `begin_program` when that tool is offered and the stage's own
`save_program_draft` block afterwards, so the stages are exercised end to end with no model.

`SBER_API_SCOPE` selects the provider: `PERS` / `B2B` / `CORP` use the GigaChat client,
`openai` the OpenAI-compatible one (`OPENAI_URL` is required in this mode, model via `OPENAI_MODEL`),
`mock` the offline stand-in. Tool calling is implemented for both the OpenAI-compatible and
the GigaChat v2 clients, and each turn names the tools it offers — the provider's own default
list is empty on purpose, so a stage cannot offer the intake tools and vice versa. The OpenAI
live tests
carry an extra `openai` marker and are selected with `-m 'live and openai'`; the GigaChat
tool test shares `-m live` with the rest of `test_live_gigachat.py`.

---

## CI

| Workflow          | Runs                                                                                                                  |
| ----------------- | --------------------------------------------------------------------------------------------------------------------- |
| `e2e.yml`         | Playwright against the Compose stack, `SBER_API_KEY=mock`, with the Playwright browser and Docker build-layer caches  |
| Isolation         | e2e runs under Compose project `digradation-e2e`, so its volumes are `digradation-e2e_*` and never the developer's    |
| `backend-ci.yml`  | black, ruff, `pytest --cov` (Python 3.14), uploads `coverage.json`                                                    |
| `frontend-ci.yml` | `ng lint`, `npx ng test --watch=false`, `ng build --configuration production` (Node 22), uploads the coverage summary |
| `commitlint.yml`  | Conventional Commits on push and PR                                                                                   |
| `pre-commit.yml`  | generic yaml, json, toml, eof and whitespace checks                                                                   |

Each package suite runs **once** per PR: coverage is collected in the same `pytest` and
`ng test` run rather than by a second coverage workflow. Coverage artifacts upload even
after a failing test or build, so a red run still leaves its report behind. The Docker
build cache is CI-only (`docker-compose.build-cache.yml`), see `AGENTS.md`.

There is no deploy workflow; CI only.

---

## Naming

The product is **`di:gradation`** and the repository **`digradation`**. The brand mark lives
in `frontend/src/index.html` and the sidebar, and the e2e specs assert on its text, so a
future rename has to move all of them together. The PostgreSQL role and database default to
`digradation`; changing them orphans an existing volume, see `LESSONS.md`.

---

## Roadmap

Ordered by value, not by commitment:

0. **Decide what the application is for.** Everything else is downstream. The base system
   prompt is one paragraph and the schema carries only `{chat_name, message}`, which is
   deliberate headroom rather than an oversight.
1. **Finish the programs catalog as a writable feature.** The list and detail now come from
   the API, and the chat workspace creates a programme; duplicate / archive / restore / delete
   still need endpoints, and `/programs/new` still renders a placeholder.
2. **Make `_prepare_for_llm` earn its place.** It is a deliberate pass-through and the seam
   where per-content-type preparation belongs. The first content type that needs work is the
   first thing that goes in it, offloaded with `asyncio.to_thread`.
3. **Extend the e2e suite.** It covers authentication (including the guest path and the
   sign-in dialog), the chat journey, a follow-up message, attachments, the paginated history
   list, admin roles and the admin prompt editor.
   The chat workspace shell, stepper, empty preview and microphone are covered. Not yet
   covered: a populated preview (unit tests only), the docx/xlsx preview, avatar upload, the
   theme toggle, the programs catalog, and the error paths (a generation that fails, a
   session deleted mid-flight).
4. **Fill the placeholder sections:** profile, user settings, the admin action log
   (`Журнал действий`) and system info.
5. **Delete the two tracked `.gitkeep` placeholder directories** (`chat-widget`, `param-table`).
6. **Mirror the Garage image into a registry we control** and pin it by digest. MinIO's
   deletion cost a CI outage and this migration; the same event could happen to
   `dxflrs/garage`.
7. **Add a LICENSE file**, currently absent.
8. **Document `TEST_INSTANCE_MODE` in `.env.example`.** It exists in compose, in the README
   and in the code, but not in the example env file.
9. **Add a per-test e2e user.** The suite shares one seeded user and works around the
   5-per-minute rate limit by clearing Redis between tests.

---

## Known issues

- **The repository rename orphaned the local Compose volumes.** The PostgreSQL role and
  database defaults changed with it, so an existing `digraduation_postgres_data` volume is no
  longer mounted and a developer sees an empty database. Nothing errors. Recover by pointing
  `POSTGRES_USER` / `POSTGRES_DB` at the old names; do not prune the volume.
- `TEST_INSTANCE_MODE` is absent from `.env.example`.
- The frontend dev override bind-mounts `./frontend` over `/app`. If `node_modules` inside it
  is stale or partially populated, `ng serve` fails in ways that look like source errors.
- Frontend statement coverage sits close to the gate. Any new uncovered component will fail
  CI, and branches are deliberately ungated, so they have little headroom.
- **`cleanup_orphan_attachments` in `app/tasks/files.py` iterates an already exhausted
  `ScalarResult`,** so it logs `count=0` and never deletes an S3 object. Rows are still
  deleted; the objects fall to the 7-day lifecycle rule. Reported, not fixed. See
  `LESSONS.md`.
- **The object-storage image is still a single point of failure.** `dxflrs/garage` lives in
  one small project's Docker Hub namespace; MinIO died the same way.
