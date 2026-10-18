# Project state

Rewritten in place on each update. Do not append. Keep it short and current; detail belongs
in `DECISIONS.md` (rationale) or `AGENTS.md` (procedure).

Last updated: **2026-10-18** (OpenAI-compatible provider and tool calling)

---

## Current status

The project was an AI quotation engine for sheet-metal fabrication — drawings in, a priced
commercial offer out. It is now a **bare chat application with file attachments**, awaiting a
purpose. Every commercial concept is gone: no `processing` package, no DXF parser, no
`calc.xlsx`, no `positions` in the LLM contract. See "Repurposed from a quotation engine to a
chat application" in `DECISIONS.md` for why each piece went.

Everything described below is working and running via Docker Compose.

| Component | State |
|---|---|
| `backend/` | Complete: auth, users, admin, chat, files, TaskIQ workers, Alembic, GigaChat harness |
| `frontend/` | Complete: login, chat create, chat with polling and file panel, history, profile, admin users, admin settings |
| `e2e/` | Complete: Playwright specs against the real Compose stack with the offline provider |
| Infra | 13 Compose services, dev and memory-limit overrides, healthcheck-gated startup |

**Backend.** FastAPI on Python 3.14. Cookie auth (argon2), admin with account expiry, chat
sessions with async generation, attachment upload / list / delete, presigned S3 results.
Alembic is a **single squashed `initial` revision** (`d7e256a71a55`) generated from the new
models. Nine TaskIQ tasks across the `default` and `network` queues, with hourly and
10-minute cleanup crons.

**Frontend.** Angular 21.2 standalone with signals. Reactive forms, cookie auth, 2-second
polling for generation results, inline preview for PDF, image, docx and xlsx, light/dark
theme, admin user management, admin prompt-extension editor. Specs colocated with the code.

**Not started or placeholder.** The profile page body, the user settings page body, and two
admin sections (`Журнал действий`, `О системе`) render `SectionPlaceholder`.

**In flight: tool calling.** The provider layer can advertise tools at construction and the
OpenAI-compatible client surfaces the calls the model requests on
`GenerationResponse.tool_calls`; nothing executes a call yet, and GigaChat has no tool
support. The next step is a tool registry and the loop that answers or runs a call.

**Two dead placeholder directories** are still tracked: `frontend/src/app/shared/components/
chat-widget/` and `.../param-table/`, each holding only a `.gitkeep`. They are leftovers from
the quotation UI and nothing imports them.

**No LICENSE file exists.** The repository is treated as proprietary.

---

## Database

One revision, eight tables (`users`, `sessions`, `chat_sessions`, `chat_messages`,
`attachments`, `generation_results`, `processing_result_uploadables`, `settings`) and two
enums (`userrole`, `generationresulttype`). The TaskIQ dashboard keeps its own separate
`taskiq_dashboard` database on the same PostgreSQL instance, created by
`conf/postgres-init/01-taskiq-dashboard.sql`.

`processing_result_uploadables` is the artifact store a processed upload can write into. The
`artifacts` bucket exists and the model is there, but **nothing writes rows yet** — that is the
first extension point if a feature starts deriving files from an upload.

---

## Tests

| Suite | Command | Needs |
|---|---|---|
| Backend | `poetry -C backend run pytest` | a Docker daemon |
| Backend coverage | `poetry -C backend run pytest --cov` | a Docker daemon |
| Frontend | `npm --prefix frontend test` | nothing |
| Frontend coverage | `npx ng test --coverage --watch=false` | nothing |
| End-to-end | `./e2e/scripts/run.sh` | the Compose stack |

**Both coverage gates are enforced at 90%**, each in exactly one place:
`fail_under` in `backend/pyproject.toml` and `coverageThresholds` on the `test` target in
`frontend/angular.json`. Neither a pre-commit hook nor a workflow names a percentage, so
they cannot drift apart. Frontend branches are deliberately not gated.

Test counts and coverage percentages are deliberately not recorded here: they are
deterministic, the commands above print them in seconds, and a recorded copy goes stale
silently. See the rule in `AGENTS.md`.

PostgreSQL, Redis and Garage are real testcontainers in the backend suite. The LLM is faked by
`app/providers/mock.py`, selected with `SBER_API_KEY=mock` (or `SBER_API_SCOPE=mock`) and
switchable via `MOCK_PROVIDER_MODE` (`ok`, `empty`, `error`); the real GigaChat is only
reachable through `@pytest.mark.live` tests, which are deselected by default.

`SBER_API_SCOPE` selects the provider: `PERS` / `B2B` / `CORP` use the GigaChat client,
`openai` the OpenAI-compatible one (any endpoint via `OPENAI_URL`, model via `OPENAI_MODEL`),
`mock` the offline stand-in. Tool calling is implemented for the OpenAI-compatible client
only: tools are defined at provider construction and requested calls come back on
`GenerationResponse.tool_calls`. Nothing executes a call yet; the OpenAI live tests carry an
extra `openai` marker and are selected with `-m 'live and openai'`.

---

## CI

| Workflow | Runs |
|---|---|
| `e2e.yml` | Playwright against the Compose stack, `SBER_API_KEY=mock`, with the Playwright browser and Docker build-layer caches |
| Isolation | e2e runs under Compose project `digradation-e2e`, so its volumes are `digradation-e2e_*` and never the developer's |
| `backend-ci.yml` | black, ruff, `pytest --cov` (Python 3.14), uploads `coverage.json` |
| `frontend-ci.yml` | `ng lint`, `npx ng test --watch=false`, `ng build --configuration production` (Node 22), uploads the coverage summary |
| `commitlint.yml` | Conventional Commits on push and PR |
| `pre-commit.yml` | generic yaml, json, toml, eof and whitespace checks |

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
2. **Extend the e2e suite.** It covers authentication, the chat journey, a follow-up
   message, attachments, the paginated history list, admin roles and the admin prompt editor.
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
- **Frontend statement coverage has little headroom over the 90% gate**, and branches are not
  gated at all. Any new uncovered component can therefore fail CI on its own. Run
  `npx ng test --coverage` before pushing rather than discovering it in CI.
- **`cleanup_orphan_attachments` in `app/tasks/files.py` iterates an already exhausted
  `ScalarResult`,** so it logs `count=0` and never deletes an S3 object. Rows are still
  deleted; the objects fall to the 7-day lifecycle rule. Reported, not fixed. See
  `LESSONS.md`.
- **The object-storage image is still a single point of failure.** `dxflrs/garage` lives in
  one small project's Docker Hub namespace; MinIO died the same way.
