# Project state

Rewritten in place on each update. Do not append. Keep it short and current; detail belongs
in `DECISIONS.md` (rationale) or `AGENTS.md` (procedure).

Last updated: **2026-10-22** (the programmes catalogue shows only backend-backed data; the
local-only duplicate/archive/delete actions, unsupported type/tag filters and the fabricated
materials tab are gone, and the chat preview's blocks are an accordion with pinned downloads)

---

## Current status

The product is a **Russian-language chat application for methodists that builds educational
programmes from a brief**. A chat can hold one programme run, ask the methodist clarifying
questions, generate the programme stage by stage, render the draft, and confirm it. File
attachments and the admin tooling remain part of the application.

Everything described below is working via Docker Compose.

| Package     | State                                                                                                                                                                             |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/`  | Complete: auth, users, admin, chat, files, TaskIQ workers, Alembic, providers, the programme cycle, pgvector source retrieval                                                     |
| `frontend/` | Complete: guest mode, sign-in dialog, the home composer that starts a chat, chat with polling and the programme aside, profile, admin users, admin settings, programmes catalogue |
| `rag/`      | Complete: chunk models, mock embeddings, in-memory and pgvector stores, indexer, retrieval policy, one-off backend CLI                                                            |
| `e2e/`      | Complete: bounded specs against the real Compose stack with the offline provider                                                                                                  |
| Infra       | 13 Compose services, dev and memory-limit overrides, healthcheck-gated startup, `pgvector/pgvector:pg18` database                                                                 |

**Backend.** FastAPI on Python 3.14. Cookie auth (argon2), admin with account expiry, chat
sessions with async generation, attachment upload / list / delete, presigned S3 results.
Alembic has the squashed `initial` revision (`d7e256a71a55`), the programme-state column
(`f3a9c1d7b2e4`) and the RAG chunk table (`b7c1d2e3f4a5`). Twelve TaskIQ tasks across the
`default` and `network` queues, with hourly, ten-minute and five-minute recovery crons.

**Frontend.** Angular 21.2 standalone with signals. The panel is browsable as a guest: the
session is resolved by a `provideAppInitializer`, `/home` and `/help` are public, every other
route renders behind an `AuthGate` prompt, and `/login` is gone in favour of a shared
`AuthModalService` dialog. Reactive forms, cookie auth, 2-second polling for generation
results, inline preview for PDF, image, docx and xlsx, light/dark theme, admin user
management, admin prompt-extension editor. Chat uploads accept exactly the backend's
formats — PDF, PNG/JPEG and DOCX — and programme exports are excluded from the staged-files
list. Section switches animate through the browser's View Transitions API
(`withViewTransitions`), direction derived from the sidebar order.

`/home` is a single-column programme-creation landing with an animated brand mark, prompt
examples and a composer docked to the bottom while empty. A send from `/home` starts a chat:
an authenticated user goes straight to it, while a guest's text and files are held in
`PendingChatDraftService`, the sign-in dialog opens, and the same chat is created and sent
after login. It is the only place a chat is created — the standalone `/create` page and the
paginated `/chats` list are gone, and the programmes catalogue carries the entry into a
finished chat. The composer also dictates Russian through the Web Speech API. `/programs` is
`Мои программы`: a lazy-loaded catalogue backed by `GET /chats/programmes` and
`GET /chats/{session_id}/program`, with filtering by update month, paging, and two-second
polling while a run is busy. Cards and the detail panel show only what those responses carry;
the catalogue is read-only until the programme-mutation endpoints exist. The chat workspace's
aside shows the saved blocks, the cited sources with their catalogue confirmation, and the
self-check in a single-open accordion; the report opens with a score and a progress bar, then
lists failures first and passed checks second. Session files and staged uploads are a `Файлы`
accordion in the same aside, and the chat has no separate files panel. A block that a stage
has just written is flashed once with a translucent backdrop, and the top stepper spins a ring
around the stage a busy run is working on. The finished programme's DOCX and PPTX downloads
stay pinned to the aside footer, outside the collapsible content. Program creation has no entry
point yet.

**Not started or placeholder.** The profile page body, user settings, and two admin sections
(`Журнал действий`, `О системе`) render `SectionPlaceholder`. `/help` and `library` are
intentional placeholders, and `/programs/new` waits for a create form. The programmes page has
no mutation actions because no endpoints exist for duplicate, archive, restore or delete.

**The programme cycle.** A chat can build one educational programme. A run is one
`ProgramState` in `chat_sessions.program_state` (JSONB), mirrored to Redis for an hour, and it
starts when a chat turn calls `begin_program`. The chat turn is a tool loop
(`run_chat_turn`): the model is offered `INTAKE_TOOLS` and decides whether to start a
programme, to confirm a rendered draft, to ask for one part to be redone
(`revise_program`), or to answer in prose. One TaskIQ task owns one stage — competences, hours,
sources, summary, assessment, materials — and each commits its block before enqueueing the
next, so a worker that dies leaves every finished block behind and `recover_stalled_programs`
restarts the stage that stopped. A stage does not take a text answer for a saved block: the
tool loop is told whether the block is committed and sends the model back to the tool while it
is not, because a refused call is often answered with "I fixed it" and no second call. A retry
is told what the last refused call asked to fix, because the pipeline keeps no transcript and
would otherwise start from the same plan. The skeleton is rendered into the chat and waits for
the methodist's confirmation, which the model turns into an `approve_program_draft` call; the
content is written only after that approval, and a `revise_program` call reruns one part of
the programme on request.

**The programme is written in two halves.** The skeleton — competences, hours, sources, the
passport — is what the methodist approves: `finalize_program_draft` renders it into the chat
and the run waits in `pending_approval`. The content — the assessment (questions with their
keys, seminar tasks, the current control, the final attestation) and the materials (lecture
plans and slide decks) — is written only after that approval, and the approval is what puts
the first content stage on the queue. Nothing is generated from a skeleton the methodist has
not seen. The hours add up at every level: the final attestation is counted inside the volume,
a module's volume is the sum of its topics rather than a number of its own, every outcome is
taught by a topic and checked by at least two means, and the schedule's capacity and daily load
are checked against the volume.

**The sources come from two catalogues and the web.** `backend/res/rag-sources.json` is the
curated catalogue: it holds the `src-NN` ids a programme cites and the paths the ingestion
joins the parsed corpus to. The corpus descriptions in `edu_materials/` are the same ten
documents in prose, and `app/agentic/sources.py` reads them so that a deployment without an
index still finds its documents. A source is confirmed when the catalogue holds it, and the
backend decides that, not the model: a document nobody verified is kept and marked as
unconfirmed — in the chat reply, in the report and in the document. The online search
(`app/agentic/websearch.py`) reaches the web as well, and what it finds is unconfirmed too.

**The programme leaves the system as a document and as slides.** A finished programme is
written to the attachments bucket as a DOCX by the АЦР template and as a PPTX of its slides,
registered as attachments of the chat, and named by id in `GET /chats/{session_id}/program`.
The client fetches them through the attachment route it already uses for a file the methodist
uploaded. The same response carries the self-check report: the hours, the coverage of the
outcomes, the sources and the sections that are filled.

**Source retrieval.** `search_sources` is offered by the source-citing stages only, never by
the chat intake. It embeds the query through the school hub and searches `rag_chunks` with
pgvector; results carry stable `src-NN` catalog ids, a section title, a page number, a trust
score and a cosine score. A missing index, a provider failure or an unreachable database
becomes `RagUnavailable`, which the tool turns into a `found=false` note so the stage
continues without citations instead of failing. With `RAG_ENABLED=false` (the default) the
index is not consulted and the corpus descriptions answer instead.

**The programme API.** `GET /chats/{session_id}/program` reports where a programme stands
(status, current stage, the blocks saved so far, the self-check report) and reads the Redis
mirror before the row, so a client that polls it does not touch PostgreSQL; `running` comes
from the stage lock, so a stalled programme is distinguishable from a busy one, and `busy`
says whether the programme is being worked on at all. `/program` and `/result` both expose
the open questions while a run is `awaiting_answers`; the workspace reads them from
`/program`, the endpoint it already polls. `POST /chats/{session_id}` takes the answers in an
`answers` array next to `content`.
`GET /chats/programmes` lists the user's programmes, paginated. A run may ask the methodist
questions: `begin_program` takes them, the run waits in `awaiting_answers`, and the answers
move it on.

**The methodist decides through endpoints.** `POST /chats/{session_id}/program/approve` is
the approval of a rendered draft: it releases the content stages, spends the attempt of the
first one and records who approved and when. `POST /chats/{session_id}/program/revision` asks
for one part to be redone: the stage runs again with the methodist's notes, the parts that
were written from it are written again, and the stage starts a fresh budget of three. Both
answer with the programme's state in the shape `GET /program` answers with, and both refuse
what the model's own tools refuse — the rules live in `app/agentic/actions.py` and are shared.
The client-side flow (the buttons, the answer form and the five-minute poll surviving a slow
run) is still not built.

**RAG ingestion.** `backend/index_rag.py` is a one-off operator CLI, not an HTTP endpoint. It
joins parser output under `edu_materials/parsed` to `backend/res/rag-sources.json` by
`source_path`, converts text chunks and tables into `ChunkRecord`s, embeds them through the
hub and upserts them into `rag_chunks`. The command is idempotent by fingerprint and supports
`--dry-run`, `--prune`, `--strict` and `--batch-size`. Ten sources are curated: three FGO
standards, four professional standards and three reference education programmes. The FGO
entries are `allow_empty: true` because OCR is not part of this iteration; a scanned PDF may
yield zero chunks without failing the run. `edu_materials/parsed` is **not committed**, so a
real index cannot be built from a fresh checkout until the parser has run.

**Two dead placeholder directories** are still tracked: `frontend/src/app/shared/components/
chat-widget/` and `.../param-table/`, each holding only a `.gitkeep`. They are leftovers from
the quotation UI and nothing imports them.

**No LICENSE file exists.** The repository is treated as proprietary.

---

## Database

Three revisions. Nine tables (`users`, `sessions`, `chat_sessions`, `chat_messages`,
`attachments`, `generation_results`, `processing_result_uploadables`, `settings`, `rag_chunks`)
and two enums (`userrole`, `generationresulttype`). `chat_sessions.program_state` is nullable
JSONB: `None` for an ordinary chat, the run envelope for a chat that is building a programme
— there is no separate table, because the run is a property of the chat and the row lock of
the chat is what serialises its writers.

`rag_chunks` is the pgvector table: `(doc_id, ordinal, kind)` is the primary key,
`source_id`/`source_kind` are the catalog identity denormalised for filtering, `metadata` is
JSONB, and `embedding` is `HALFVEC(2048)` with an HNSW `halfvec_cosine_ops` index. The
extension is created by the migration and by the backend test fixture; it is deliberately
not dropped on downgrade because it is shared by any other consumer of the database.

The TaskIQ dashboard keeps its own separate `taskiq_dashboard` database on the same
PostgreSQL instance, created by `conf/postgres-init/01-taskiq-dashboard.sql`.

`processing_result_uploadables` is the artifact store a processed upload can write into. The
`artifacts` bucket exists and the model is there, but **nothing writes rows yet** — that is the
first extension point if a feature starts deriving files from an upload.

---

## Tests

| Suite             | Command                              | Needs                                                     |
| ----------------- | ------------------------------------ | --------------------------------------------------------- |
| Backend           | `poetry -C backend run pytest`       | needs a Docker daemon (testcontainers)                    |
| Backend coverage  | `poetry -C backend run pytest --cov` | gated by `fail_under` in `backend/pyproject.toml`         |
| Rag               | `poetry -C rag run pytest`           | needs a Docker daemon for the pgvector store tests        |
| Rag coverage      | `poetry -C rag run pytest --cov`     | gated by `fail_under` in `rag/pyproject.toml`             |
| Frontend          | `npm --prefix frontend test`         | Vitest; runs in `frontend-ci.yml`                         |
| Frontend coverage | `npx ng test --coverage`             | gated by `coverageThresholds` in `frontend/angular.json`  |
| End-to-end        | `./e2e/scripts/run.sh`               | Playwright against the Compose stack, `SBER_API_KEY=mock` |

**Both coverage gates are enforced**, each in exactly one place: `fail_under` in
`backend/pyproject.toml` and `rag/pyproject.toml`, and `coverageThresholds` on the `test`
target in `frontend/angular.json`. Read the figure off the `TOTAL` line or the builder's
summary; neither a pre-commit hook nor a workflow names a percentage. Frontend branches are
deliberately not gated.

Test counts and coverage percentages are deliberately not recorded here: they are
deterministic, the commands above print them in seconds, and a recorded copy goes stale
silently. See the rule in `AGENTS.md`.

PostgreSQL, Redis and Garage are real testcontainers in the backend suite. The LLM is faked by
`app/providers/mock.py`, selected with `SBER_API_KEY=mock` (or `SBER_API_SCOPE=mock`) and
switchable via `MOCK_PROVIDER_MODE` (`ok`, `empty`, `error`, `stage`, `revise`, `approve`); the
real provider is only reachable through `@pytest.mark.live` tests, which are deselected by
default. Both live provider suites carry a full model -> tool -> model round, because that is
the one shape the offline suite cannot prove the real API accepts. The `stage`
mode scripts `begin_program` when that tool is offered and the stage's own
`save_program_draft` block afterwards, so the stages are exercised end to end with no model;
`revise` and `approve` script the two intake calls that come after the draft is on the table.

PostgreSQL, Redis and Garage are real testcontainers in the backend suite; the PostgreSQL
image is `pgvector/pgvector:pg18` and the fixture installs the `vector` extension before
`create_all`. The LLM is faked by `app/providers/mock.py`, selected with `SBER_API_KEY=mock`
(or `SBER_API_SCOPE=mock`) and switchable via `MOCK_PROVIDER_MODE` (`ok`, `empty`, `error`,
`stage`); the real provider is only reachable through `@pytest.mark.live` tests, which are
deselected by default. Both live provider suites carry a full model -> tool -> model round,
because that is the one shape the offline suite cannot prove the real API accepts. The `stage`
mode scripts `begin_program` when that tool is offered and the stage's own
`save_program_draft` block afterwards, so the stages are exercised end to end with no model.
The mock embedding provider also uses 2048 dimensions, so the tests exercise the real column
shape without a socket.

`SBER_API_SCOPE` selects the provider: `PERS` / `B2B` / `CORP` use the GigaChat client,
`openai` the OpenAI-compatible one (`OPENAI_URL` is required in this mode, model via
`OPENAI_MODEL`), `mock` the offline stand-in. The hub's plain chat model is the default and is
enough for the easier stages; `OPENAI_MODEL=ai-sage/GigaChat3.5-432B-A28B-Reasoning` is the
smarter, more expensive choice for a run whose arithmetic keeps being refused. Tool calling is
implemented for both the OpenAI-compatible and the GigaChat v2 clients, and each turn names the
tools it offers — the provider's own default list is empty on purpose, so a stage cannot offer
the intake tools and vice versa. The OpenAI-compatible client also recovers GigaChat's native
`<|function_call|>` payload when the hub delivers it as message text instead of `tool_calls`.
The OpenAI live tests carry an extra `openai` marker and are selected with
`-m 'live and openai'`; the GigaChat tool test shares `-m live` with the rest of
`test_live_gigachat.py`.

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
build cache is CI-only (`docker-compose.build-cache.yml`), see `AGENTS.md`. There is no
deploy workflow; CI only.

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
1. **Build the chat-side programme flow.** The backend can now write a programme in two
   halves, report on it, export it and redo one part of it; the client can list the
   programmes, read the state, answer the questions of a run, show the self-check report and
   download the DOCX/PPTX exports, but it cannot yet approve a rendered draft or ask for a
   change. That is the gap that keeps the feature out of the user's hands.
2. **Finish the programs catalog.** It is API-backed and shows only real programme state, but
   it is still read-only: add the mutation endpoints and the create/edit form behind
   `/programs/new`.
3. **Make `_prepare_for_llm` earn its place.** It is a deliberate pass-through and the seam
   where per-content-type preparation belongs. The first content type that needs work is the
   first thing that goes in it, offloaded with `asyncio.to_thread`.
4. **Extend the e2e suite.** It covers authentication (the guest path, the sign-in dialog and
   the gate), the chat journey, a follow-up message, attachments, the home file preview, the
   programmes catalogue (guest preview, paging, the detail panel), admin roles and the admin
   prompt editor.
   Not yet covered: the docx and xlsx preview, avatar upload, the theme toggle, and the error
   paths (a generation that fails, a session deleted mid-flight).
5. **Fill the placeholder sections:** profile, user settings, the admin action log
   (`Журнал действий`) and system info.
6. **Delete the two tracked `.gitkeep` placeholder directories** (`chat-widget`, `param-table`).
7. **Mirror the Garage image into a registry we control** and pin it by digest. MinIO's
   deletion cost a CI outage and this migration; the same event could happen to
   `dxflrs/garage`.
8. **Add a LICENSE file**, currently absent.
9. **Document `TEST_INSTANCE_MODE` in `.env.example`.** It exists in compose, in the README
   and in the code, but not in the example env file.
10. **Add a per-test e2e user.** The suite shares one seeded user and works around the

11. **Build a real index and turn RAG on.** Run the parser over the corpus, then
    `index_rag.py`, then set `RAG_ENABLED=true`. The blocker is data, not code:
    `edu_materials/parsed` is not committed, and the default `.env` scope is `PERS`, which has
    no embeddings endpoint. Add the deployment setup when the hub is proven.
12. **Add HTTP indexing and search endpoints** once the one-off CLI has been exercised in a
    deployment. They need authorization, progress reporting and a way to cancel a run; the CLI
    deliberately has none of that.
13. **OCR for the scanned FGO PDFs.** The catalog already marks them `allow_empty`, so the
    rest of the corpus can be indexed first; OCR is a parser change, not an ingestion change.
14. **Finish the chat-side programme flow:** approve a rendered draft, ask for one part to be
    redone, and make the five-minute poll survive a slow run.
15. **Make `/programs` writable.** There are no endpoints yet for duplicate, archive, restore or
    delete, so the page deliberately offers no such actions.
16. **Fill the placeholder sections:** profile, user settings, the admin action log
    (`Журнал действий`) and system info.
17. **Extend the e2e suite:** the docx/xlsx preview, avatar upload, the theme toggle, the
    programmes catalogue, and the error paths (a failed generation, a session deleted mid-flight).
18. **Delete the two tracked `.gitkeep` placeholder directories** (`chat-widget`, `param-table`).
19. **Mirror the Garage image into a registry we control** and pin it by digest. MinIO's
    deletion cost a CI outage and a migration; the same event could happen to
    `dxflrs/garage`.
20. **Add a LICENSE file**, currently absent.
21. **Document `TEST_INSTANCE_MODE` in `.env.example`.** It exists in compose, in the README
    and in the code, but not in the example env file.
22. **Add a per-test e2e user.** The suite shares one seeded user and works around the
    5-per-minute rate limit by clearing Redis between tests.

---

## Known issues

- **The repository rename orphaned the local Compose volumes.** The PostgreSQL role and
  database defaults changed with it, so an existing `digraduation_postgres_data` volume is no
  longer mounted and a developer sees an empty database. Nothing errors. Recover by pointing
  `POSTGRES_USER` / `POSTGRES_DB` at the old names; do not prune the volume.
- **RAG is off by default and has no data in a fresh checkout.** `RAG_ENABLED=false`, the
  parser output is gitignored, and the default `.env` scope is `PERS`. That is deliberate:
  the app must start without a hub or an index.
- **`edu_materials/parsed` is not in the repository.** The indexer needs parser output; the
  corpus itself lives outside the tracked tree.
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
