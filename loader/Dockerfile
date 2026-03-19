FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir boto3 mutagen psycopg2-binary
WORKDIR /app
COPY loader/loader.py .
COPY sql/schema.sql .
CMD ["python", "loader.py"]
