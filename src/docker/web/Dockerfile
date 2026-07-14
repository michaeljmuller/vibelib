# Build context is src/python/web (see docker-compose.yml).
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY web ./web
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
