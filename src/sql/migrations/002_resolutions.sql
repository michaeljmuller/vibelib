-- 002: resolver support — resolution log/review-queue table and trigram indexes
-- for candidate retrieval. Run once, after 001_unify_people.sql.


CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- One row per raw asset the resolver has examined. Doubles as the audit log
-- (status 'auto') and the review queue (status 'pending').
CREATE TABLE resolutions (
    id          SERIAL PRIMARY KEY,
    asset_type  TEXT NOT NULL CHECK (asset_type IN ('epub', 'm4b')),
    asset_id    INT  NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('auto', 'pending', 'approved', 'rejected')),
    method      TEXT NOT NULL CHECK (method IN ('exact', 'llm', 'llm_cover')),
    confidence  REAL,
    proposal    JSONB NOT NULL,   -- action list; for 'auto'/'approved' rows these are the applied actions
    notes       TEXT,             -- LLM rationale, shown by the review CLI
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at TIMESTAMPTZ,
    UNIQUE (asset_type, asset_id)
);

CREATE INDEX idx_resolutions_status ON resolutions(status);

-- Trigram indexes backing candidate retrieval.
CREATE INDEX idx_books_title_trgm ON books  USING gin (lower(title) gin_trgm_ops);
CREATE INDEX idx_people_name_trgm ON people USING gin (lower(name)  gin_trgm_ops);
CREATE INDEX idx_series_name_trgm ON series USING gin (lower(name)  gin_trgm_ops);

