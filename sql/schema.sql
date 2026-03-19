-- ebooks metadata schema
-- Raw data as extracted from the epub OPF; normalization happens externally.

CREATE TABLE IF NOT EXISTS epubs (
    id              SERIAL PRIMARY KEY,
    s3_key          TEXT        NOT NULL UNIQUE, -- full object key in the S3 bucket
    asin            TEXT,
    isbn            TEXT,

    title           TEXT        NOT NULL,
    publisher       TEXT,
    published_date  TEXT,                       -- raw string from dc:date (formats vary)
    language        TEXT,                       -- BCP-47 / ISO 639-1 code (e.g. "en", "pt")
    description     TEXT,
    series          TEXT,
    series_position NUMERIC(6,2),               -- allows half-steps like 1.5
    identifier      TEXT,                       -- raw dc:identifier (UUID, URN, etc.)
    subject         TEXT,                       -- raw dc:subject tag
    cover_path      TEXT,                       -- path to cover image within the epub zip
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS epub_authors (
    id          SERIAL PRIMARY KEY,
    epub_id     INT      NOT NULL REFERENCES epubs(id) ON DELETE CASCADE,
    author      TEXT     NOT NULL,              -- raw dc:creator value
    role        TEXT     NOT NULL DEFAULT 'author', -- 'author', 'editor', 'translator', etc.
    position    SMALLINT NOT NULL DEFAULT 1     -- ordering when multiple authors
);

CREATE INDEX IF NOT EXISTS idx_epub_authors_epub_id ON epub_authors(epub_id);
CREATE INDEX IF NOT EXISTS idx_epub_authors_author  ON epub_authors(author);
CREATE INDEX IF NOT EXISTS idx_epubs_series         ON epubs(series);
CREATE INDEX IF NOT EXISTS idx_epubs_s3_key         ON epubs(s3_key);

-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS m4bs (
    id              SERIAL PRIMARY KEY,
    s3_key          TEXT        NOT NULL UNIQUE, -- full object key in the S3 bucket
    asin            TEXT,                        -- ----:com.apple.iTunes:ASIN freeform atom
    title           TEXT,                        -- ©nam
    artist          TEXT,                        -- ©ART — raw, may contain multiple authors
    narrator        TEXT,                        -- ©wrt / composer field
    album           TEXT,                        -- ©alb — often includes series and book number
    date            TEXT,                        -- ©day — year only in practice
    description     TEXT,                        -- ldes / desc
    comment         TEXT,                        -- ©cmt
    genre           TEXT,                        -- ©gen
    copyright       TEXT,                        -- cprt
    has_cover       BOOLEAN,                     -- whether covr atom is present
    duration_s      INT,                         -- audio duration in seconds
    bitrate_kbps    INT,
    sample_rate     INT,
    channels        SMALLINT,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_m4bs_artist ON m4bs(artist);
CREATE INDEX IF NOT EXISTS idx_m4bs_s3_key ON m4bs(s3_key);

CREATE TABLE IF NOT EXISTS m4b_chapters (
    id          SERIAL PRIMARY KEY,
    m4b_id      INT      NOT NULL REFERENCES m4bs(id) ON DELETE CASCADE,
    position    SMALLINT NOT NULL,              -- chapter order (1-based)
    title       TEXT,                           -- chapter title e.g. "Chapter 1", "Opening Credits"
    start_ms    INT      NOT NULL               -- start offset in milliseconds
);

CREATE INDEX IF NOT EXISTS idx_m4b_chapters_m4b_id ON m4b_chapters(m4b_id);
