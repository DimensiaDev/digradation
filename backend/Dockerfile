FROM python:3.14-alpine
RUN apk add --no-cache libstdc++
RUN pip install poetry==2.4.3 uvicorn==0.52.4
COPY backend/pyproject.toml backend/poetry.lock /app/
WORKDIR /app

COPY rag/ /rag/
# The corpus of documents a programme may cite: the catalog the agent searches
# and the place a source is confirmed in.
COPY edu_materials/ /edu_materials/
ENV EDU_MATERIALS_PATH=/edu_materials
RUN poetry install --only main --no-root
COPY backend/ .
RUN chmod +x entrypoint.sh
CMD ["./entrypoint.sh"]
