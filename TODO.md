# TODO

## Author deduplication tool (`tools/group_authors.py`)

- [x] Redesigned as incremental populator: processes epubs then m4bs in order,
      canonicalizes each author string (uninvert Last/First, fix ALL CAPS, strip
      parentheticals/years) and deduplicates against the growing authors table
      using normalize → squish → fuzzy → LLM tiers. Writes author_map.json.
      Use --lookup NAME to check a single name against the library.

## Curation workflow

- [ ] Decide on tooling for curation (admin UI vs. SQL scripts)
- [ ] Build out the workflow for linking `book_epubs`/`book_m4bs` to
      abstract `books`, and assigning canonical `authors`

## Web service

- [ ] Decide on stack for the REST API
- [ ] Implement Sign in with Apple:
      - Apple Developer Portal: create App ID, enable Sign in with Apple
      - Create a Services ID (for web) and configure domain + redirect URL
      - Generate a Sign in with Apple key, save the Key ID and private key
      - Backend: verify identity tokens, maintain allowlist of permitted accounts
- [ ] Design and implement JWT issuance after Apple identity token verification
- [ ] iOS app and web app both use the same token exchange flow
