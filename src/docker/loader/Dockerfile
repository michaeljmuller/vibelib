# Build context is src/python/loader (see docker-compose.yml).
FROM python:3.12-slim

# ffprobe reads m4b chapters; mutagen exposes tags and cover art but not chapters.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY loader ./loader
RUN pip install --no-cache-dir .

ENTRYPOINT ["loader"]
