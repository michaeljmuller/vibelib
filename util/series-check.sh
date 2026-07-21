#!/bin/bash
# Read-only sweep for series-membership problems. Curation is done by hand, and
# hand-curation drifts: a series entered under two names, two books in one slot, a
# position that contradicts the epub. This finds those; it changes nothing.
#
#   util/series-check.sh          run every check
#   util/series-check.sh 0.6      tighten the name-similarity threshold (default 0.4)
#
# Fixes are decided by a human and captured in src/sql/fixes/series.sql. Work the
# checks roughly top-to-bottom: the name duplicates in the first section create
# false holes and false singletons further down, so merging names first makes the
# rest of the report tell the truth.
set -e
cd "$(dirname "$0")/../src/docker"

THRESHOLD="${1:-0.4}"

# SQL on stdin, not -c: psql only interpolates :'vars' when it lexes the input
# itself (same reason as util/users.sh). Every query here is a SELECT.
psql() { docker compose exec -T db psql -U vibelib vibelib -v ON_ERROR_STOP=1 -v t="$THRESHOLD" "$@"; }

section() { printf '\n\033[1m── %s ──\033[0m\n' "$1"; }

section "1. NEAR-DUPLICATE SERIES NAMES  (likely the same series entered twice)"
echo "   Merge the real ones FIRST — they manufacture false holes/singletons below."
echo "   Not every pair is a dup (Children of Time / Titan are different books)."
psql <<'SQL'
SELECT a.id AS id_a, a.name AS name_a, b.id AS id_b, b.name AS name_b,
       round(similarity(a.name, b.name)::numeric, 2) AS sim
FROM series a JOIN series b ON a.id < b.id
WHERE similarity(a.name, b.name) > :'t'::real
ORDER BY a.name, b.name;
SQL

section "2. DUPLICATE BOOK ROWS  (same title, same series — a genuine dup row)"
psql <<'SQL'
SELECT sr.id AS series_id, sr.name AS series, b.title, count(*) AS rows,
       string_agg(b.id::text, ', ' ORDER BY b.id) AS book_ids
FROM books b JOIN series sr ON sr.id = b.series_id
GROUP BY sr.id, sr.name, lower(b.title), b.title
HAVING count(*) > 1
ORDER BY sr.name, b.title;
SQL

section "3. POSITION COLLISIONS  (different books claiming one slot — pick an order)"
psql <<'SQL'
SELECT sr.id AS series_id, sr.name AS series, b.series_position AS pos, count(*) AS books,
       string_agg(b.id || '=' || b.title, ' | ' ORDER BY b.title) AS book_id_and_title
FROM books b JOIN series sr ON sr.id = b.series_id
WHERE b.series_position IS NOT NULL
GROUP BY sr.id, sr.name, b.series_position
HAVING count(*) > 1
ORDER BY count(*) DESC, sr.name;
SQL

section "4. CURATED POSITION ≠ THE EPUB'S OWN POSITION  (one of them is wrong)"
psql <<'SQL'
SELECT b.id AS book_id, sr.name AS series, b.title,
       b.series_position AS curated, e.series_position AS epub_says
FROM books b
JOIN series sr ON sr.id = b.series_id
JOIN book_epubs be ON be.book_id = b.id
JOIN epubs e ON e.id = be.epub_id
WHERE e.series_position IS NOT NULL
  AND b.series_position IS NOT NULL
  AND b.series_position <> floor(e.series_position)::int
ORDER BY sr.name, b.title;
SQL

section "5. MISSING POSITIONS  (gaps between what you have — often just unowned books)"
echo "   Most meaningful AFTER section 1 is cleaned up. A shopping list, not an error."
psql <<'SQL'
WITH present AS (
    SELECT b.series_id, sr.name,
           array_agg(DISTINCT b.series_position ORDER BY b.series_position) AS have,
           min(b.series_position) AS lo, max(b.series_position) AS hi
    FROM books b JOIN series sr ON sr.id = b.series_id
    WHERE b.series_position IS NOT NULL AND b.series_position > 0
    GROUP BY b.series_id, sr.name
)
SELECT series_id, name, have,
       ARRAY(SELECT generate_series(lo, hi)
             EXCEPT SELECT unnest(have) ORDER BY 1) AS missing
FROM present
WHERE hi > lo
  AND EXISTS (SELECT generate_series(lo, hi) EXCEPT SELECT unnest(have))
ORDER BY cardinality(ARRAY(SELECT generate_series(lo, hi)
                           EXCEPT SELECT unnest(have))) DESC;
SQL

section "6. SINGLETON MIS-LINK CANDIDATES  (lone book whose name echoes a bigger series)"
psql <<'SQL'
WITH counts AS (
    SELECT series_id, count(*) AS n FROM books WHERE series_id IS NOT NULL GROUP BY series_id
)
SELECT one.id AS singleton_id, one.name AS singleton,
       many.id AS bigger_id, many.name AS bigger_series,
       mc.n AS bigger_has,
       round(similarity(one.name, many.name)::numeric, 2) AS sim
FROM counts oc
JOIN series one ON one.id = oc.series_id AND oc.n = 1
JOIN counts mc ON mc.n > 1
JOIN series many ON many.id = mc.series_id
WHERE similarity(one.name, many.name) > :'t'::real
ORDER BY sim DESC;
SQL

section "7. UNUSUAL POSITIONS  (zero or negative — confirm it's an intentional prequel)"
psql <<'SQL'
SELECT b.id AS book_id, sr.name AS series, b.title, b.series_position AS pos
FROM books b JOIN series sr ON sr.id = b.series_id
WHERE b.series_position <= 0
ORDER BY b.series_position, sr.name;
SQL

section "8. DROPPED INTERSTITIALS  (epub had a .5 position; the integer column lost it)"
psql <<'SQL'
SELECT b.id AS book_id, sr.name AS series, b.title, e.series_position AS epub_says
FROM books b
JOIN series sr ON sr.id = b.series_id
JOIN book_epubs be ON be.book_id = b.id
JOIN epubs e ON e.id = be.epub_id
WHERE e.series_position <> floor(e.series_position)
  AND b.series_position IS NULL
ORDER BY sr.name;
SQL

section "9. MIXED AUTHORSHIP  (3+ authors in a series — usually co-authors, sometimes a mis-link)"
echo "   Also surfaces un-split author strings ('X; Y' stored as one person)."
psql <<'SQL'
SELECT sr.id AS series_id, sr.name AS series, count(DISTINCT ba.author_id) AS authors,
       string_agg(DISTINCT ba.author_id || '=' || p.name, ', ' ORDER BY ba.author_id || '=' || p.name) AS author_id_and_name
FROM series sr
JOIN books b ON b.series_id = sr.id
JOIN book_authors ba ON ba.book_id = b.id
JOIN people p ON p.id = ba.author_id
GROUP BY sr.id, sr.name
HAVING count(DISTINCT ba.author_id) >= 3
ORDER BY count(DISTINCT ba.author_id) DESC, sr.name;
SQL

echo
