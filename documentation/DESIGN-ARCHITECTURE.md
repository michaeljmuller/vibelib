# Library Database Design and Architecture

This document captures the key elements of the design and architecure of the Library Database project.

## Operating Environment

This project is intended to run on MacOS or on Unix.  Windows is not a target platform.

The project will run in a containerized environment; Dockerfiles and docker compose configuration will be
part of the project implementation.

## Scale

This is a personal project, so it does not need to accomodate very large volumes. 

The library currently has about 1200 e-books and 350 audiobooks.

There is an S3 bucket with 2788 objects (EPUBs and M4Bs) taking up 252 GB.

The new project should be able to handle 10x this size easily. 

There is only one primary user.  The system should be multi-user to support access by friends and family,
but the number of users will be very small.

## Storage

The audiobook and e-book binaries will be stored in S3.

The metadata (title, author, etc.) will be stored in a Postgres relational database. 