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

## Storage Layer

The audiobook and e-book binaries will be stored in S3.

The metadata (title, author, etc.) will be stored in a Postgres relational database.

### Object Store

The binaries are actually stored in an S3-compatible object store hosted by Linode (now Akamai).

Accordingly some additional configuration may be necessary beyond what's done for AWS.

Authentication is through access/secret keys.

Here is the endpoint info (although this should be configured). 

object.store.bucket.name=michaeljmuller-media
object.store.bucket.endpoint=us-east-1.linodeobjects.com
object.store.bucket.region=us-east-1

### Data Model

All tables should have a numeric PK (except perhaps for a table whose exclusive function is to join other tables). 

The data model should support each book having an arbitrary number of authors.

Authors should be able to have an indefinite number of pseudonyms.  I don't care to track which name the author used
for a particular book.

Books can be stand-alone or part of a series.  The books are associated with a series an a particular order, such as
"book three of the Interstallar Ninja series".  A complicating factor is that some series have interstitial books, so
the model should be able to support "book 3.5" of a series.  This is probably best handled by having a hidden numeric
series number and an optional text-based display series number, for those cases when book 7 is really the 8th book in
a series.

Additional book metadata:
 - title
 - publication year
 - publication date (optional)
 - acquisition date
 - isbn

Books should be able to have an indefinite number of tags (a plain text string that marks a book as having a particular attribute).

Books should be able to have an indefinite number of alternate titles.  Examples:
 - "The Shape of Water" by Andrea Camilleri has an alternate title of "La Forma dell'acqua"
 - "Winner Take All" by Barry Eisler has TWO alternate titles: "Rain Storm" and "Choke Point" 

User metadata:
 - email (unique, used for login)
 - full name
 - disabled flag

Books should be able to have one review by each user.

Review metadata:
 - number of stars
 - review text
 - spoilers (review content that should only be read by people who have already read the book)
 - private notes (only visible to the user that wrote the review)
 - recommended flag
 - create time
 - modification time

Books should be able to have one or more ebooks and/or audiobooks associated with them (represented by an
S3 object id).

Audiobooks should have a list of narrators associated with them.

An amazon info table should associate additional optional metadata from amazon.  There will only be one
amazon asin associated with each book (even if there's multiple assets). 
 - asin
 - sample time (when the data was pulled from amazon)
 - rating
 - num ratings
 - publication date
 - page count

Narrator and author names should be normalized so that adjusting the spelling of one affects all the entries.

Publisher information is not captured.  Genre information is captured using tags.

## Authentication

OAuth 2.0 is used for user authentication. Multiple providers are supported (Google, Apple, etc.)
and users choose which to use at login.

The web UI (and future iOS client) authenticates users via OAuth, obtaining a JWT access token.
Clients pass the JWT to the service layer via Authorization header on each request.
The service layer validates the JWT signature and claims, agnostic to which client or provider was used.

## Service Layer

The service layer will be implemented in python.

All endpoints (except health checks) require a valid JWT in the Authorization header.

## Web User Interface

The web-based user interface will also be implemented in python.

The UI implements the OAuth login flow, stores the JWT, and includes it in service layer requests.

## Containers and Orchestration

Containerization will be provided by Docker.  

A docker compose file will orchestrate the following containers:
 - web user interface
 - service layer
 - postgres

The postgres service will have a bind mount so the metadata persists properly.

