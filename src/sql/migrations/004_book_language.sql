-- 004: abstract books get a language (BCP-47 / ISO 639-1 code, matching the
-- convention of epubs.language, e.g. 'en', 'pt', 'pt-PT'). NULL = unknown.

ALTER TABLE books ADD COLUMN language TEXT;
