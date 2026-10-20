# Project state

Rewritten in place on each update. Do not append. Keep it short and current; detail belongs
in `DECISIONS.md` (rationale) or `AGENTS.md` (procedure).

Last updated: **2026-10-20** (the sequential programme cycle runs end to end, #3)

---

## Current status

The project was an AI quotation engine for sheet-metal fabrication — drawings in, a priced
commercial offer out. It is now a **bare chat application with file attachments**, awaiting a
purpose. Every commercial concept is gone: no `processing` package, no DXF parser, no
`calc.xlsx`, no `positions` in the LLM contract. See "Repurposed from a quotation engine to a
chat application" in `DECISIONS.md` for why each piece went.

Everything described below is working and running via Docker Compose.

| Component   | State                                                                                                                              |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `backend/`  | Complete: auth, users, admin, chat, files, TaskIQ workers, Alembic, providers, the programme cycle (#3)                             |
| `frontend/` | Complete: guest mode, sign-in dialog, chat create, chat with polling and file panel, history, profile, admin users, admin settings |
| `e2e/`      | Complete: specs against the real Compose stack with the offline provider                                                           |
| Infra       | 13 Compose services, dev and memory-limit overrides, healthcheck-gated startup                                                     |

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
admin sections (`Журнал действий`, `О системе`) render `SectionPlaceholder`. The public
`/home` and `/help` pages are intentional `SectionPlaceholder` hosts, and `programs` /
`library` render the same placeholder behind the auth gate.

**The programme cycle (#3).** A chat can build one educational programme. A run is one
`ProgramState` in `chat_sessions.program_state` (JSONB), mirrored to Redis for an hour, and it
starts when a chat turn calls `begin_program` — that turn lives in `app/tasks/api.py`, which is
owned elsewhere and stays as `main` has it, so this branch delivers the stages and the seam
they need rather than the chat interaction. One
TaskIQ task owns one stage — competences, hours, sources, summary — and each commits its block
before enqueueing the next, so a worker that dies leaves every finished block behind and
`recover_stalled_programs` restarts the stage that stopped. `finalize_program_draft` renders
the assembled draft into the chat, renames a default-named chat and waits for the methodist's
confirmation, which the chat worker is to turn into `approve_program_draft`. `assessment`
and `materials` stay pending for #5; the source catalog behind `sources.py` is still empty
until the RAG index (#11), so a draft cites no documents today. There is no endpoint and no
programme UI: the cycle is driven by ordinary chat messages and the frontend needs nothing new
to see the result, though its five-minute poll can time out before a slow run finishes.

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
only reachable through `@pytest.mark.live` tests, which are deselected by default. The `stage`
mode scripts `begin_program` when that tool is offered and the stage's own
`save_program_draft` block afterwards, so the stages are exercised end to end with no model.

`SBER_API_SCOPE` selects the provider: `PERS` / `B2B` / `CORP` use the GigaChat client,
`openai` the OpenAI-compatible one (any endpoint via `OPENAI_URL`, model via `OPENAI_MODEL`),
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
1. **Make `_prepare_for_llm` earn its place.** It is a deliberate pass-through and the seam
   where per-content-type preparation belongs. The first content type that needs work is the
   first thing that goes in it, offloaded with `asyncio.to_thread`.
2. **Extend the e2e suite.** It covers authentication (including the guest path and the
   sign-in dialog), the chat journey, a follow-up message, attachments, the paginated history
   list, admin roles and the admin prompt editor.
   Not yet covered: the docx/xlsx preview, avatar upload, the theme toggle, and the error
   paths (a generation that fails, a session deleted mid-flight).
3. **Fill the placeholder sections:** profile, user settings, the admin action log
   (`Журнал действий`) and system info.
4. **Delete the two tracked `.gitkeep` placeholder directories** (`chat-widget`, `param-table`).
5. **Mirror the Garage image into a registry we control** and pin it by digest. MinIO's
   deletion cost a CI outage and this migration; the same event could happen to
   `dxflrs/garage`.
6. **Add a LICENSE file**, currently absent.
7. **Document `TEST_INSTANCE_MODE` in `.env.example`.** It exists in compose, in the README
   and in the code, but not in the example env file.
8. **Add a per-test e2e user.** The suite shares one seeded user and works around the
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
