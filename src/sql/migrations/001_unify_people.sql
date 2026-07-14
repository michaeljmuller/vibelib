-- 001_unify_people.sql
-- Merge the disjoint `authors` and `narrators` tables into a single `people`
-- identity, and repoint the existing foreign keys at it. Authors keep their ids,
-- so book_authors and author_pseudonyms values stay valid without remapping;
-- only narrators are remapped (matched to an existing person by sort_name, or
-- given a fresh people id). Adds the `disambiguator` column to people.
--
-- Idempotent-ish only in that it is guarded against re-running: it aborts if
-- `people` already exists. Run once against a database created from the pre-merge
-- schema.


-- Guard: refuse to run if already migrated.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'people') THEN
        RAISE EXCEPTION 'people table already exists; migration 001 appears to have run';
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 1. people table; authors keep their ids.
-- ---------------------------------------------------------------------------
CREATE TABLE people (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    sort_name     TEXT NOT NULL,
    disambiguator TEXT
);

INSERT INTO people (id, name, sort_name)
SELECT id, name, sort_name FROM authors;

-- advance the sequence past the highest author id
SELECT setval('people_id_seq', (SELECT COALESCE(MAX(id), 1) FROM people));

-- ---------------------------------------------------------------------------
-- 2. map narrators -> people. Match an existing person by lower(sort_name)
--    (Baldree folds into his author row); otherwise mint a new people row.
--    All EXISTS/matches below run while `people` still holds only authors.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE narrator_map (narrator_id INT PRIMARY KEY, person_id INT NOT NULL);

-- matched narrators -> existing (author) person
INSERT INTO narrator_map (narrator_id, person_id)
SELECT n.id,
       (SELECT MIN(p.id) FROM people p WHERE lower(p.sort_name) = lower(n.sort_name))
FROM narrators n
WHERE EXISTS (SELECT 1 FROM people p WHERE lower(p.sort_name) = lower(n.sort_name));

-- unmatched narrators -> fresh people ids
CREATE TEMP TABLE narrator_new AS
SELECT n.id                    AS narrator_id,
       nextval('people_id_seq') AS person_id,
       n.name,
       n.sort_name
FROM narrators n
WHERE NOT EXISTS (SELECT 1 FROM people p WHERE lower(p.sort_name) = lower(n.sort_name));

INSERT INTO people (id, name, sort_name)
SELECT person_id, name, sort_name FROM narrator_new;

INSERT INTO narrator_map (narrator_id, person_id)
SELECT narrator_id, person_id FROM narrator_new;

-- ---------------------------------------------------------------------------
-- 3. repoint m4b_narrators.narrator_id at people via the map.
-- ---------------------------------------------------------------------------
ALTER TABLE m4b_narrators DROP CONSTRAINT m4b_narrators_narrator_id_fkey;

UPDATE m4b_narrators mn
SET narrator_id = nm.person_id
FROM narrator_map nm
WHERE mn.narrator_id = nm.narrator_id;

ALTER TABLE m4b_narrators
    ADD CONSTRAINT m4b_narrators_narrator_id_fkey
    FOREIGN KEY (narrator_id) REFERENCES people(id) ON DELETE CASCADE;

-- ---------------------------------------------------------------------------
-- 4. retarget the author-side FKs from authors -> people
--    (values already valid because authors kept their ids).
-- ---------------------------------------------------------------------------
ALTER TABLE book_authors DROP CONSTRAINT book_authors_author_id_fkey;
ALTER TABLE book_authors
    ADD CONSTRAINT book_authors_author_id_fkey
    FOREIGN KEY (author_id) REFERENCES people(id) ON DELETE CASCADE;

ALTER TABLE author_pseudonyms DROP CONSTRAINT author_pseudonyms_pseudonym_id_fkey;
ALTER TABLE author_pseudonyms DROP CONSTRAINT author_pseudonyms_author_id_fkey;
ALTER TABLE author_pseudonyms
    ADD CONSTRAINT author_pseudonyms_pseudonym_id_fkey
    FOREIGN KEY (pseudonym_id) REFERENCES people(id) ON DELETE CASCADE;
ALTER TABLE author_pseudonyms
    ADD CONSTRAINT author_pseudonyms_author_id_fkey
    FOREIGN KEY (author_id) REFERENCES people(id) ON DELETE CASCADE;

-- ---------------------------------------------------------------------------
-- 5. drop the old tables (no longer referenced) and index the new one.
-- ---------------------------------------------------------------------------
DROP TABLE narrators;
DROP TABLE authors;

CREATE INDEX idx_people_sort_name ON people(sort_name);

