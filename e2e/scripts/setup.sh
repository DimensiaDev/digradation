#!/usr/bin/env bash
#
# Bring up the stack for end-to-end tests and make it ready to drive.
#
#   ./e2e/scripts/run.sh          # setup, seed and run the suite
#   ./e2e/scripts/setup.sh        # setup and seed only
#
# The stack runs with SBER_API_KEY=mock, so no GigaChat call is made and no
# credentials are needed. Only the LLM is faked: PostgreSQL, Redis, RabbitMQ,
# Garage, the FastAPI app and the TaskIQ workers are all the real thing.
set -euo pipefail

E2E_USER="${E2E_USERNAME:-e2e}"
E2E_PASSWORD="${E2E_PASSWORD:-e2epassword}"
E2E_FRONTEND_PORT="${E2E_FRONTEND_PORT:-18080}"

# A SEPARATE COMPOSE PROJECT IS LOAD-BEARING, not cosmetic. Compose prefixes
# named volumes with the project name, so without this the e2e stack resolves to
# `digradation_postgres_data` -- the developer's own database -- and the suite writes
# its seeded users and chats straight into it. Volume names cannot be overridden
# per service, so the project name is the only lever.
#
# The override is passed with `-f` rather than copied over
# docker-compose.override.yml. Copying it meant any later `docker compose` command
# in the repo -- including a developer's -- silently picked up the e2e ports and
# recreated their containers with them. Naming the file leaves the developer's own
# override untouched.
#
# Specs that shell out to docker must use the same flags; E2E_PROJECT is exported
# below so tests read it instead of hardcoding.
E2E_PROJECT="${E2E_PROJECT:-digradation-e2e}"
export E2E_PROJECT
DC=(docker compose -p "$E2E_PROJECT" -f docker-compose.yaml -f docker-compose.override.yml.e2e)

# In CI, add the build-layer cache. It is a third `-f` file rather than part of
# the e2e override because the `gha` cache backend needs Buildx plus the
# `ACTIONS_*` variables GitHub injects into a workflow step; both are absent
# locally, so including it unconditionally would break this script for anyone
# not running in CI. `.github/workflows/e2e.yml` runs `docker/setup-buildx-action`
# first, which is what makes `type=gha` resolvable.
if [ "${CI:-false}" = "true" ]; then
    DC+=(-f docker-compose.build-cache.yml)
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

cd "$REPO_ROOT"

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example and fill in the required values first." >&2
  exit 1
fi

echo "==> Starting the stack"
E2E_FRONTEND_PORT="$E2E_FRONTEND_PORT" "${DC[@]}" up -d --build

# One deadline for both probes: the frontend and backend start together, and
# separate loops would let setup wait twice as long as the message promises.
READINESS_TIMEOUT=120
readiness_deadline=$((SECONDS + READINESS_TIMEOUT))

echo "==> Waiting for the frontend"
frontend_ready=false
while [ "$SECONDS" -lt "$readiness_deadline" ]; do
  if curl -fsS --max-time 5 -o /dev/null "http://localhost:${E2E_FRONTEND_PORT}/" 2>/dev/null; then
    frontend_ready=true
    break
  fi
  sleep 2
done
if [ "$frontend_ready" != "true" ]; then
  echo "Frontend did not answer on port ${E2E_FRONTEND_PORT} within the ${READINESS_TIMEOUT}s readiness deadline." >&2
  exit 1
fi

# nginx answering `/` only proves the bundle was served. The first spec still
# needs the API: it logs in, and a login against a backend that is still starting
# fails in a way that surfaces as "the app stayed on the login page". An
# unauthenticated 401 is the correct answer here and means the routes are loaded.
echo "==> Waiting for the backend API"
backend_ready=false
while [ "$SECONDS" -lt "$readiness_deadline" ]; do
  code=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' \
    "http://localhost:${E2E_FRONTEND_PORT}/api/v1/users/me" 2>/dev/null)
  if [ "$code" = "401" ]; then
    backend_ready=true
    break
  fi
  sleep 2
done
if [ "$backend_ready" != "true" ]; then
  echo "Backend API did not answer with 401 on port ${E2E_FRONTEND_PORT} within the ${READINESS_TIMEOUT}s readiness deadline." >&2
  exit 1
fi

echo "==> Seeding the e2e superuser (${E2E_USER})"
# `exec -e` is required: compose does not forward host variables into the
# container on its own.
"${DC[@]}" exec -T \
  -e "E2E_USERNAME=${E2E_USER}" \
  -e "E2E_PASSWORD=${E2E_PASSWORD}" \
  backend poetry run python - <<'PY'
import asyncio
import os

from app.auth import hash_password
from app.database import get_session_maker
from app.models.auth import AccountRole, User
from sqlalchemy import select

USERNAME = os.environ["E2E_USERNAME"]
PASSWORD = os.environ["E2E_PASSWORD"]


async def main():
    async with get_session_maker()() as db:
        existing = (
            await db.execute(select(User).where(User.username == USERNAME))
        ).scalar_one_or_none()
        if existing is not None:
            existing.password_hash = hash_password(PASSWORD)
            existing.role = AccountRole.SUPERUSER
            print(f"reset superuser {USERNAME}")
        else:
            db.add(
                User(
                    username=USERNAME,
                    password_hash=hash_password(PASSWORD),
                    role=AccountRole.SUPERUSER,
                )
            )
            print(f"created superuser {USERNAME}")
        await db.commit()


asyncio.run(main())
PY

echo "==> Seeding e2e programmes"
# The catalogue is read from `chat_sessions.program_state`, so the pagination and
# detail specs need rows to look at. The run envelope is deliberately minimal: the
# list endpoint reads its title, status and `updated_at`, and the detail endpoint
# accepts the same defaults the model gives an empty run.
"${DC[@]}" exec -T \
  -e "E2E_USERNAME=${E2E_USER}" \
  backend poetry run python - <<'PY'
import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

from app.database import get_session_maker
from app.models.auth import User
from app.models.chat import Attachment, ChatSession
from app.storage import get_s3_attachment_key, storage
from sqlalchemy import delete, select

USERNAME = os.environ["E2E_USERNAME"]

# title, state status, failed, days ago
PROGRAMMES = [
    ("Python для анализа данных", "draft", False, 1),
    ("Кибербезопасность для сотрудников", "generating", False, 2),
    ("Основы машинного обучения", "pending_approval", False, 3),
    ("Agile-трансформация команд", "approved", False, 4),
    ("Управление проектами", "draft", False, 5),
    ("Soft skills для руководителей", "awaiting_answers", False, 6),
    ("Веб-разработка: полный цикл", "draft", True, 7),
    ("Продуктовая аналитика", "verified", False, 8),
]


async def main():
    async with get_session_maker()() as db:
        user = (await db.execute(select(User).where(User.username == USERNAME))).scalar_one()
        await db.execute(
            delete(ChatSession).where(
                ChatSession.user_id == user.id,
                ChatSession.program_state.is_not(None),
            )
        )

        now = datetime.now(UTC)
        for title, status, failed, days_ago in PROGRAMMES:
            session_id = uuid.uuid4()
            state = {
                "session_id": str(session_id),
                "run_id": str(uuid.uuid4()),
                "status": status,
                "title": title,
                "kind": "professional_development",
                "failed": failed,
                "version": 1,
                "updated_at": (now - timedelta(days=days_ago)).isoformat(),
                "questions": [],
                "answers": [],
            }
            if failed:
                state["error"] = "e2e seeded failure"
            if status == "pending_approval":
                # The one programme a decision waits on, so the decision panel has
                # a state to render: a draft that was shown is `finalized`, and its
                # skeleton is committed, which is what fills the picker with the
                # four blocks. `finalized` also keeps the row where it is — the
                # recovery cron fails any non-terminal run it finds untouched for
                # ten minutes, and this envelope is days old on purpose, because
                # the catalogue orders by `updated_at`.
                state["finalized"] = True
                state["stages"] = {
                    "competences": "completed",
                    "hours": "completed",
                    "sources": "completed",
                    "summary": "completed",
                    "assessment": "pending",
                    "materials": "pending",
                }
            session = ChatSession(
                session_id=session_id,
                user_id=user.id,
                name=title,
                program_state=state,
            )
            db.add(session)

            if status == "approved":
                # The finished programme is the one with the two export forms, and
                # the client fetches them through the attachment route it uses for
                # any file — so the row, the stored object and the state's ids all
                # have to exist, or the presigned URL 404s and the download button
                # looks broken. `approved` is terminal, so the recovery cron leaves
                # the run alone.
                await db.flush()
                exports = {}
                for name, state_field in (
                    ("programme.docx", "document_attachment_id"),
                    ("programme-slides.pptx", "slides_attachment_id"),
                ):
                    s3_key = get_s3_attachment_key(session_id, name)
                    attachment = Attachment(
                        session_id=session_id,
                        name=name,
                        s3_key=s3_key,
                        ready=True,
                    )
                    db.add(attachment)
                    await db.flush()
                    async with storage.internal_client() as client:
                        await client.put_object(
                            Bucket="uploads",
                            Key=s3_key,
                            Body=f"e2e {name}".encode(),
                        )
                    exports[state_field] = attachment.id
                session.program_state = {**state, "exports": exports}
        await db.commit()
        print(f"seeded {len(PROGRAMMES)} programmes for {USERNAME}")


asyncio.run(main())
PY

echo "==> Clearing rate-limit keys"
# Both chat creation and message sending are limited to 5 per minute per user
# (`chats:post` in app/routes/chat.py). A single e2e run creates several chats
# and sends several messages, and consecutive runs land inside the same window,
# so the counters are reset here. Only `ratelimit:*` is touched.
"${DC[@]}" exec -T redis sh -c '
  redis-cli --scan --pattern "ratelimit:*" | xargs -r redis-cli DEL
' >/dev/null

echo "==> Stack is ready on http://localhost:${E2E_FRONTEND_PORT}"
echo "    compose project: ${E2E_PROJECT} (volumes are ${E2E_PROJECT}_*, isolated from dev)"
