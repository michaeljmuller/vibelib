#!/bin/bash
# AWS CLI wired to the vibelib object store (credentials, endpoint, and bucket
# from src/docker/.env). With no arguments, lists the bucket root. Otherwise
# passes arguments straight to `aws` with the endpoint already set, e.g.:
#   util/s3.sh
#   util/s3.sh s3 ls "s3://<bucket>/epubs/"
#   util/s3.sh s3 cp "s3://<bucket>/some/key.epub" ./
set -e
cd "$(dirname "$0")/../src/docker"

# .env is docker-compose format (values may be unquoted with spaces), so pull
# out just the keys we need instead of sourcing the whole file.
env_val() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }

export AWS_ACCESS_KEY_ID="$(env_val OBJECT_STORE_ACCESS_KEY_ID)"
export AWS_SECRET_ACCESS_KEY="$(env_val OBJECT_STORE_SECRET_ACCESS_KEY)"
export AWS_DEFAULT_REGION="$(env_val OBJECT_STORE_BUCKET_REGION)"
OBJECT_STORE_BUCKET_NAME="$(env_val OBJECT_STORE_BUCKET_NAME)"

ENDPOINT="$(env_val OBJECT_STORE_BUCKET_ENDPOINT)"
case "$ENDPOINT" in
  http://*|https://*) ;;
  *) ENDPOINT="https://$ENDPOINT" ;;
esac

if [ $# -eq 0 ]; then
  echo "bucket: s3://$OBJECT_STORE_BUCKET_NAME/"
  exec aws --endpoint-url "$ENDPOINT" s3 ls "s3://$OBJECT_STORE_BUCKET_NAME/"
fi
exec aws --endpoint-url "$ENDPOINT" "$@"
