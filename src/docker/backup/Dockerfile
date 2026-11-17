# Build context is src/python/backup (see docker-compose.yml).
#
# Built on the postgres image, not on python, so that pg_dump is exactly the
# version of the server it dumps. pg_dump refuses a server newer than itself,
# and the client in Debian's own repository lags the postgres:16 server.
# Keep this tag the same as the db service's.
FROM postgres:16

# Python and boto3 from Debian, not pip: this image's system Python refuses
# pip installs (PEP 668), and Debian's boto3 is all this needs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-boto3 tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backup ./backup
COPY tests ./tests

# Not the postgres image's entrypoint: this container never runs a server.
ENTRYPOINT []
CMD ["python3", "-m", "backup"]
