# Build context is src/python/resolver (see docker-compose.yml).
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY resolver ./resolver
RUN pip install --no-cache-dir .

ENTRYPOINT ["resolver"]
