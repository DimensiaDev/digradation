# Project state

Rewritten in place on each update. Do not append. Keep it short and current; detail belongs
in `DECISIONS.md` (rationale) or `AGENTS.md` (procedure).

Last updated: **2026-10-23** (the remaining programme criteria are closed: 16–500 hours,
actualization/adaptation, JSON and LMS exports, mandatory conditions and staffing)

---

## Current status

The product is a **Russian-language chat application for methodists that builds educational
programmes from a brief**. A chat holds one programme run, asks the methodist clarifying
questions, generates the programme stage by stage, renders a draft for approval, writes the
content after approval, and lets the methodist export the result. File attachments and the
admin tooling remain part of the application.

Everything described below runs through Docker Compose. There is no supported host-side run.

| Package     | State                                                                                                                                          |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/`  | Auth, users, admin, chat, files, TaskIQ workers, Alembic, providers, the programme cycle, source retrieval, DOCX/PPTX/JSON/Moodle XML exports |
| `frontend/` | Guest mode, sign-in dialog, home composer, chat with polling and programme aside, profile, admin, programmes catalogue, approval/revision panel, four export downloads |
| `rag/`      | Chunk models, embeddings, in-memory and pgvector stores, indexer, retrieval policy, backend CLI                                                |
| `e2e/`      | Bounded Playwright specs against the real Compose stack with the offline provider                                                              |
| Infra       | 13 Compose services, dev and memory-limit overrides, healthcheck-gated startup, `pgvector/pgvector:pg18`                                        |

## The programme cycle

A run is one `ProgramState` in `chat_sessions.program_state` (JSONB), mirrored to Redis for an
hour; PostgreSQL is canonical. A chat turn is a tool loop (`run_chat_turn`): the model is
offered `INTAKE_TOOLS` and may begin a programme, approve a rendered draft, revise one block,
or answer in prose. One TaskIQ task owns one stage, and each stage commits its block before it
enqueues the next.

- **The volume is 16–500 academic hours.** `MIN_PROGRAM_HOURS` and `MAX_PROGRAM_HOURS` in
  `app/agentic/models.py` are used by the intake tool and by the state. A request outside the
  range is refused before a run exists; prose answers that name a limit are read back by
  `app/agentic/intake.py` only when the structured field is still unset.
- **The skeleton is written before the content.** `competences → hours → sources → summary`
  ends in `pending_approval`; `finalize_program_draft` renders the draft. The content —
  `assessment → materials` — is released only by the methodist's approval. A revision reruns
  one stage and rewrites the blocks that depended on it.
- **The methodist can decide without the model.** `POST /chats/{session_id}/program/approve`
  and `POST /chats/{session_id}/program/revision` share the rules with the intake tools in
  `app/agentic/actions.py`. The chat shows a decision panel while a rendered draft waits.
- **Actualization and adaptation are a mode.** `ProgramMode` (`new`, `actualization`,
  `adaptation`) travels through the state, the stage brief, the DOCX and the API; the client
  labels a non-new programme in the aside.
- **Every programme has conditions and staff.** The materials block carries equipment,
  teaching materials, staffing requirements and staff slots. Lecture plans and slide decks
  are required only where the curriculum has lecture hours, so a practice-only internship is
  valid without inventing lectures.

## Sources

`backend/res/rag-sources.json` is the curated catalogue of the ten documents a programme may
cite, and `app/agentic/sources.py` reads both it and the corpus descriptions in
`edu_materials/`. The backend decides `confirmed`, not the model: a document the catalogue does
not hold is kept, marked unconfirmed, and shown that way in the report and the DOCX.

`STRICT_SOURCES` is **on unless explicitly set to false**. The corpus is the only confirmed
set, so a deployment that has not chosen a trusted search service does not reach the public
web. The encyclopedic fallback in `app/agentic/websearch.py` is off on the mock provider, so
the e2e suite never needs the internet. A professional standard that a competence rests on
still refuses the programme when it is unconfirmed.

Source retrieval (`search_sources`) is offered by the source-citing programme stages, never by
the chat intake. It needs `RAG_ENABLED=true` and a built index; without them the corpus
catalogue still answers.

## Exports and API

A finished programme is registered as four attachments of its chat, fetched through the same
attachment route as an uploaded file:

| Name | Content |
|---|---|
| `programme.docx` | the programme in the АЦР section order |
| `programme-slides.pptx` | the slides of the materials stage |
| `programme.json` | the whole document, including the report |
| `programme-lms.xml` | a Moodle XML question bank with keys and seminar criteria |

`GET /chats/{session_id}/program` answers with the state, the saved blocks, the open questions,
the self-check report and the export ids. It reads the Redis mirror first and falls back to the
row. `GET /chats/programmes` is the paginated catalogue.

The self-check has six groups (hours, outcome coverage, sources, sections, the limits the
methodist named, materials); `notes` carries the things that are not failures, such as a slow
run, an expectation the volume cannot meet, or a broken number in the sample programme.

## Providers

`LLM_PROVIDER` selects `sber`, `openai`, `cloudru` or `mock`; without it, `SBER_API_SCOPE`
decides. `SBER_API_KEY=mock` always selects the offline provider. The `cloudru` preset points
the OpenAI-compatible client at Cloud.ru Foundation Models and defaults to
`ai-sage/GigaChat3.5-432B-A28B`, a documented model with function calling and structured
output. `STRICT_PROVIDER` refuses a foreign host when it is on.

The mock provider has modes `ok`, `empty`, `error`, `stage`, `revise` and `approve`. The `stage`
mode scripts `begin_program` and the stage's own save call, which is what makes the pipeline
runnable without a model.

## Database

Three revisions, nine tables and two enums. The programme run is the nullable
`chat_sessions.program_state` JSONB column, not a separate table: the row lock of the chat is
what serialises its writers. `rag_chunks` is the pgvector table, with a `HALFVEC(2048)` column
and an HNSW index; the extension is created by the migration and by the test fixture and is not
dropped on downgrade.

## Tests and gates

| Suite | Command | Needs |
|---|---|---|
| Backend | `poetry -C backend run pytest` | a Docker daemon (testcontainers) |
| Backend coverage | `poetry -C backend run pytest --cov` | `fail_under` in `backend/pyproject.toml` |
| Rag | `poetry -C rag run pytest` | Docker for the pgvector store tests |
| Rag coverage | `poetry -C rag run pytest --cov` | `fail_under` in `rag/pyproject.toml` |
| Frontend | `npm --prefix frontend test` | Vitest |
| Frontend coverage | `npx ng test --coverage` | `coverageThresholds` in `frontend/angular.json` |
| End-to-end | `./e2e/scripts/run.sh` | Playwright and the Compose stack |

Run one frontend test command at a time: parallel `ng test` runs share the coverage and output
directories. Test counts and coverage percentages are deliberately not recorded; the commands
print them.

## CI

`backend-ci.yml` runs black, ruff and `pytest --cov` on Python 3.14; `frontend-ci.yml` runs
`ng lint`, `ng test --watch=false` and a production build on Node 22; `e2e.yml` runs Playwright
with `SBER_API_KEY=mock` under the isolated `digradation-e2e` Compose project. Coverage is
collected in the same run as the tests — there is no second coverage workflow. CI has no deploy
step.

## Known issues and next steps

- **RAG has no data in a fresh checkout.** `RAG_ENABLED=false`, the parser output is gitignored,
  and the default `.env.example` uses the mock provider. Run the parser, `backend/index_rag.py`,
  then set `RAG_ENABLED=true` and a real embedding scope.
- **The programmes catalogue is read-only.** There are no endpoints yet for duplicate, archive,
  restore or delete, and `/programs/new` waits for a create form.
- **A content revision after approval is reached through the model.** Asking the agent to redo
  a block works, but there is no dedicated endpoint or panel for it; the approval/revision panel
  is for the rendered skeleton.
- **Placeholders remain** on the profile page, user settings, `Журнал действий` and
  `О системе`; `/help` and `library` are intentional placeholders.
- **`cleanup_orphan_attachments` iterates an exhausted `ScalarResult`**, so it logs `count=0`
  and leaves S3 objects to the lifecycle rule. Rows are deleted. Reported, not fixed.
- **Garage is a single registry namespace.** MinIO's deletion caused a migration; mirror and
  pin the image before this becomes urgent.
- **`TEST_INSTANCE_MODE` is absent from `.env.example`**; it exists in Compose, the README and
  the code.
- **The repository has no LICENSE file** and is treated as proprietary.
