---
id: TASK-20
title: Title and author normalization with fuzzy matching
status: To Do
assignee:
  - '@claude'
created_date: '2026-03-03 21:44'
labels:
  - bootstrap
dependencies:
  - TASK-17
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement the matching logic that will be used throughout the ingestion pipeline to deduplicate books and authors.\n\nnormalize_title(title): lowercase, strip punctuation, strip leading articles (the/a/an), collapse whitespace.\n\nnormalize_author(name): detect and convert Last, First to First Last; strip punctuation from initials (P.G. -> PG); lowercase.\n\nmatch_title(candidate, existing_titles) -> (best_match, score): returns the best normalized match using rapidfuzz, or None if nothing clears FUZZY_MATCH_THRESHOLD.\n\nmatch_author(candidate, existing_authors) -> (best_match, score): same, using normalized author names.\n\nWhen two author name forms match, prefer the longer one as primary_name (e.g. Stephen King over King S).\n\nAll functions live in common/matching.py.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 normalize_title() correctly strips articles, punctuation, and collapses whitespace
- [ ] #2 normalize_author() correctly handles Last, First order and initial punctuation
- [ ] #3 match_title() and match_author() return the correct best match above threshold and None below it
- [ ] #4 Unit tests cover the cases described in the spec: P.G. vs P. G. Wodehouse, Last/First order, leading articles, near-identical titles
<!-- AC:END -->
