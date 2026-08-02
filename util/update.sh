#!/bin/bash

# update the application in the production environment

# stop script if any command fails
set -e

# find the dir where this script is executing
root=$(cd "$(dirname "$0")/.." && pwd)
docker_dir="${root}/src/docker"

# function to execute docker compose commands without having to cd to ${docker_dir}
do_docker() { docker compose -f "${docker_dir}"/docker-compose.yml --project-directory "${docker_dir}" "$@"; }

# stop the web app
if [ -n "$(do_docker ps -q web)" ]; then
  echo "web site is running; stopping before update"
  do_docker stop web
fi

# start the database
if [ -z "$(do_docker ps -q db)" ]; then
  echo "database is not running; starting it so we can back up"
  do_docker up -d db
fi

# back up the database
echo "backing up the database"
"${root}"/util/dump.sh

# pull the latest code
echo "pulling the latest code from git origin"
(cd "${root}" && git pull)

# rebuild the image
echo "building the docker image"
do_docker build

# (re)start the app
echo "restart the application in the background"
do_docker up -d

# tail the logs
echo "tailing the logs (ctrl-c won't terminate the app)"
do_docker logs -f 

