FROM python:3.14-alpine
RUN apk add --no-cache libstdc++
RUN pip install poetry==2.4.3 uvicorn==0.52.4

# The build context is the repository root, not backend/: the edu-rag path
# dependency in pyproject.toml is `../rag`, so rag/ must live at /app/rag
# alongside backend/ at /app/backend for poetry to resolve it.
WORKDIR /app/backend

# Dependencies first: this layer stays cached across application-only changes.
# rag/ is copied before `poetry install` because poetry reads rag/pyproject.toml
# to resolve the edu-rag dependency tree.
COPY backend/pyproject.toml backend/poetry.lock /app/backend/
COPY rag /app/rag
RUN poetry install --only main --no-root

COPY backend/ /app/backend/
RUN chmod +x entrypoint.sh
CMD ["./entrypoint.sh"]
