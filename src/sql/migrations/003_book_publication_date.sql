-- 003: abstract books get a first-publication date (used e.g. to order
-- interstitial series entries). Convention: when only the year is known the
-- date is stored as YYYY-01-01; NULL = unknown.

ALTER TABLE books ADD COLUMN publication_date DATE;
