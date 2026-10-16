FROM python:3.14-alpine
RUN apk add --no-cache libstdc++
RUN pip install poetry==2.4.3 uvicorn==0.52.4
WORKDIR /app

# The build context is this directory alone, so nothing outside it is copied in.
# Dependencies are installed from the lockfile before the source, which keeps this
# layer cached across application-only changes.
COPY pyproject.toml poetry.lock /app/
RUN poetry install --only main --no-root

COPY . .
RUN chmod +x entrypoint.sh
CMD ["./entrypoint.sh"]