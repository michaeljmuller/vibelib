This is a database containing metadata about e-books and audiobooks in an S3 bucket.

The database runs in a docker container with a mount so the infomation persists to the host.

Everything done for this project should run in a container; do not install anything directly on the host.
The containers should be orchestrated in a docker compose file.
