# Build context is src/python/web (see docker-compose.yml).
FROM python:3.12-slim

# ffprobe reads m4b chapters when a new audiobook is added. mutagen exposes the
# tags and cover art but not chapters, which this library carries in both the
# Nero and QuickTime conventions -- ffprobe is the only reader that handles both.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY web ./web
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
