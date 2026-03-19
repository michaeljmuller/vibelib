-- ebooks metadata schema
-- Raw data as extracted from the epub OPF; normalization happens externally.

-- ---------------------------------------------------------------------------
-- Abstract book schema
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS authors (
    id          SERIAL PRIMARY KEY,
    name        TEXT    NOT NULL,
    sort_name   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_authors_sort_name ON authors(sort_name);

CREATE TABLE IF NOT EXISTS author_pseudonyms (
    pseudonym_id    INT NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    author_id       INT NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    PRIMARY KEY (pseudonym_id, author_id)
);

CREATE TABLE IF NOT EXISTS narrators (
    id          SERIAL PRIMARY KEY,
    name        TEXT    NOT NULL,
    sort_name   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_narrators_sort_name ON narrators(sort_name);

CREATE TABLE IF NOT EXISTS series (
    id                SERIAL PRIMARY KEY,
    name              TEXT    NOT NULL,
    sort_name         TEXT    NOT NULL,
    highest_position  INT,                    -- total books in series, manually curated
    is_complete       BOOLEAN                 -- null = unknown, true/false = known
);

CREATE INDEX IF NOT EXISTS idx_series_sort_name ON series(sort_name);

CREATE TABLE IF NOT EXISTS books (
    id               SERIAL PRIMARY KEY,
    title            TEXT    NOT NULL,
    sort_title       TEXT    NOT NULL,
    series_id        INT     REFERENCES series(id) ON DELETE SET NULL,
    series_position  NUMERIC(6,2)             -- allows half-steps like 1.5
);

CREATE INDEX IF NOT EXISTS idx_books_sort_title  ON books(sort_title);
CREATE INDEX IF NOT EXISTS idx_books_series_id   ON books(series_id);

CREATE TABLE IF NOT EXISTS book_authors (
    book_id     INT      NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    author_id   INT      NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    position    SMALLINT NOT NULL DEFAULT 1,  -- ordering when multiple authors credited
    PRIMARY KEY (book_id, author_id)
);

CREATE INDEX IF NOT EXISTS idx_book_authors_author_id ON book_authors(author_id);


-- ---------------------------------------------------------------------------
-- Raw epub/m4b tables (loader-populated; book_id linked by manual curation)
-- ---------------------------------------------------------------------------

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

CREATE TABLE IF NOT EXISTS m4b_narrators (
    m4b_id      INT      NOT NULL REFERENCES m4bs(id) ON DELETE CASCADE,
    narrator_id INT      NOT NULL REFERENCES narrators(id) ON DELETE CASCADE,
    position    SMALLINT NOT NULL DEFAULT 1,  -- ordering when multiple narrators credited
    PRIMARY KEY (m4b_id, narrator_id)
);

CREATE INDEX IF NOT EXISTS idx_m4b_narrators_narrator_id ON m4b_narrators(narrator_id);

-- ---------------------------------------------------------------------------
-- Curation joins: link raw epub/m4b records to abstract books
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS book_epubs (
    book_id     INT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    epub_id     INT NOT NULL REFERENCES epubs(id) ON DELETE CASCADE,
    PRIMARY KEY (book_id, epub_id)
);

CREATE TABLE IF NOT EXISTS book_m4bs (
    book_id     INT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    m4b_id      INT NOT NULL REFERENCES m4bs(id) ON DELETE CASCADE,
    PRIMARY KEY (book_id, m4b_id)
);
