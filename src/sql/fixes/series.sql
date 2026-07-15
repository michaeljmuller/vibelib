-- Series-membership corrections, found with util/series-check.sh and decided by
-- hand. This is a transcript, not a migration: each statement below was run and
-- verified against the local database, then recorded here so it can be replayed
-- once on production:
--
--     util/psql.sh < src/sql/fixes/series.sql
--
-- Statements are keyed on series names and book titles, never surrogate ids, so
-- they are correct on production even though its id values need not match local's.
-- Read top-to-bottom as the history of what was decided and why.

BEGIN;

-- (fixes are appended here as they are made)

COMMIT;
