-- ============================================================================
-- Library Database Schema
-- PostgreSQL Database Schema for E-book and Audiobook Library
-- ============================================================================

-- ============================================================================
-- CORE ENTITY TABLES
-- ============================================================================

-- Users table
CREATE TABLE users (
    user_id BIGSERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    full_name VARCHAR(255) NOT NULL,
    disabled BOOLEAN NOT NULL DEFAULT FALSE
);

-- Authors table with pseudonym support
CREATE TABLE authors (
    author_id BIGSERIAL PRIMARY KEY,
    primary_name VARCHAR(500) NOT NULL,
    pseudonym_for BIGINT REFERENCES authors(author_id) ON DELETE CASCADE,
    search_vector tsvector,
    CONSTRAINT chk_primary_name_not_empty CHECK (LENGTH(TRIM(primary_name)) > 0)
);

-- Narrators table
CREATE TABLE narrators (
    narrator_id BIGSERIAL PRIMARY KEY,
    name VARCHAR(500) NOT NULL,
    CONSTRAINT chk_narrator_name_not_empty CHECK (LENGTH(TRIM(name)) > 0),
    CONSTRAINT uq_narrator_name UNIQUE(name)
);

-- Series table
CREATE TABLE series (
    series_id BIGSERIAL PRIMARY KEY,
    name VARCHAR(500) NOT NULL,
    description TEXT,
    CONSTRAINT chk_series_name_not_empty CHECK (LENGTH(TRIM(name)) > 0)
);

-- ============================================================================
-- BOOKS AND RELATED TABLES
-- ============================================================================

-- Books table - core book metadata
CREATE TABLE books (
    book_id BIGSERIAL PRIMARY KEY,
    title VARCHAR(1000) NOT NULL,
    language_code VARCHAR(10),
    isbn VARCHAR(20),
    publication_year INTEGER,
    publication_date DATE,
    acquisition_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    search_vector tsvector,
    CONSTRAINT chk_title_not_empty CHECK (LENGTH(TRIM(title)) > 0),
    CONSTRAINT chk_isbn_format CHECK (isbn IS NULL OR isbn ~ '^[0-9X\-]{10,17}$'),
    CONSTRAINT chk_publication_year_range CHECK (
        publication_year IS NULL OR
        (publication_year >= 1000 AND publication_year <= EXTRACT(YEAR FROM NOW()) + 5)
    ),
    CONSTRAINT chk_acquisition_date_not_future CHECK (
        acquisition_date IS NULL OR acquisition_date <= CURRENT_DATE
    )
);

-- Book series relationship with ordering
CREATE TABLE book_series (
    book_id BIGINT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    series_id BIGINT NOT NULL REFERENCES series(series_id) ON DELETE CASCADE,
    sort_order NUMERIC(10, 2) NOT NULL,
    display_number VARCHAR(20),
    PRIMARY KEY (book_id, series_id),
    CONSTRAINT chk_sort_order_positive CHECK (sort_order > 0)
);

-- Book authors relationship
CREATE TABLE book_authors (
    book_id BIGINT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    author_id BIGINT NOT NULL REFERENCES authors(author_id) ON DELETE CASCADE,
    author_order SMALLINT NOT NULL DEFAULT 1,
    PRIMARY KEY (book_id, author_id),
    CONSTRAINT chk_author_order_positive CHECK (author_order > 0)
);

-- Book alternate titles
CREATE TABLE book_alternate_titles (
    alternate_title_id BIGSERIAL PRIMARY KEY,
    book_id BIGINT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    title VARCHAR(1000) NOT NULL,
    CONSTRAINT chk_alternate_title_not_empty CHECK (LENGTH(TRIM(title)) > 0),
    CONSTRAINT uq_book_alternate_title UNIQUE(book_id, title)
);

-- Book tags
CREATE TABLE book_tags (
    book_id BIGINT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    tag VARCHAR(100) NOT NULL,
    PRIMARY KEY (book_id, tag),
    CONSTRAINT chk_tag_not_empty CHECK (LENGTH(TRIM(tag)) > 0)
);

-- ============================================================================
-- FILE STORAGE TABLES
-- ============================================================================

-- E-book files
CREATE TABLE ebook_files (
    ebook_file_id BIGSERIAL PRIMARY KEY,
    book_id BIGINT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    s3_object_key VARCHAR(1024) NOT NULL,
    file_format VARCHAR(20) NOT NULL,
    file_size_bytes BIGINT,
    CONSTRAINT chk_s3_object_key_not_empty CHECK (LENGTH(TRIM(s3_object_key)) > 0),
    CONSTRAINT chk_file_size_positive CHECK (file_size_bytes IS NULL OR file_size_bytes > 0),
    CONSTRAINT uq_ebook_s3_object_key UNIQUE(s3_object_key)
);

-- Audiobook files
CREATE TABLE audiobook_files (
    audiobook_file_id BIGSERIAL PRIMARY KEY,
    book_id BIGINT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    s3_object_key VARCHAR(1024) NOT NULL,
    file_format VARCHAR(20) NOT NULL,
    duration_seconds INTEGER,
    file_size_bytes BIGINT,
    CONSTRAINT chk_s3_object_key_not_empty CHECK (LENGTH(TRIM(s3_object_key)) > 0),
    CONSTRAINT chk_duration_positive CHECK (duration_seconds IS NULL OR duration_seconds > 0),
    CONSTRAINT chk_file_size_positive CHECK (file_size_bytes IS NULL OR file_size_bytes > 0),
    CONSTRAINT uq_audiobook_s3_object_key UNIQUE(s3_object_key)
);

-- Audiobook narrators relationship
CREATE TABLE audiobook_narrators (
    audiobook_file_id BIGINT NOT NULL REFERENCES audiobook_files(audiobook_file_id) ON DELETE CASCADE,
    narrator_id BIGINT NOT NULL REFERENCES narrators(narrator_id) ON DELETE CASCADE,
    narrator_order SMALLINT NOT NULL DEFAULT 1,
    PRIMARY KEY (audiobook_file_id, narrator_id),
    CONSTRAINT chk_narrator_order_positive CHECK (narrator_order > 0)
);

-- ============================================================================
-- REVIEWS AND METADATA TABLES
-- ============================================================================

-- User reviews
CREATE TABLE reviews (
    review_id BIGSERIAL PRIMARY KEY,
    book_id BIGINT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    num_stars SMALLINT,
    review_text TEXT,
    spoilers TEXT,
    private_notes TEXT,
    recommended BOOLEAN,
    CONSTRAINT uq_review_user_book UNIQUE(book_id, user_id),
    CONSTRAINT chk_num_stars_range CHECK (num_stars IS NULL OR (num_stars >= 1 AND num_stars <= 5))
);

-- Amazon metadata
CREATE TABLE amazon_metadata (
    book_id BIGINT PRIMARY KEY REFERENCES books(book_id) ON DELETE CASCADE,
    asin VARCHAR(10) NOT NULL,
    sample_time TIMESTAMPTZ NOT NULL,
    rating NUMERIC(3, 2),
    num_ratings INTEGER,
    publication_date DATE,
    page_count INTEGER,
    CONSTRAINT chk_asin_format CHECK (asin ~ '^[A-Z0-9]{10}$'),
    CONSTRAINT chk_rating_range CHECK (rating IS NULL OR (rating >= 0 AND rating <= 5.00)),
    CONSTRAINT chk_num_ratings_positive CHECK (num_ratings IS NULL OR num_ratings >= 0),
    CONSTRAINT chk_page_count_positive CHECK (page_count IS NULL OR page_count > 0),
    CONSTRAINT uq_asin UNIQUE(asin)
);

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Users indexes
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_disabled ON users(disabled) WHERE disabled = FALSE;

-- Authors indexes
CREATE INDEX idx_authors_primary_name ON authors(primary_name);
CREATE INDEX idx_authors_pseudonym_for ON authors(pseudonym_for) WHERE pseudonym_for IS NOT NULL;
CREATE INDEX idx_authors_search_vector ON authors USING GIN(search_vector);

-- Narrators indexes
CREATE INDEX idx_narrators_name ON narrators(name);

-- Series indexes
CREATE INDEX idx_series_name ON series(name);

-- Books indexes
CREATE INDEX idx_books_title ON books(title);
CREATE INDEX idx_books_isbn ON books(isbn) WHERE isbn IS NOT NULL;
CREATE INDEX idx_books_publication_year ON books(publication_year) WHERE publication_year IS NOT NULL;
CREATE INDEX idx_books_acquisition_date ON books(acquisition_date) WHERE acquisition_date IS NOT NULL;
CREATE INDEX idx_books_search_vector ON books USING GIN(search_vector);

-- Book series indexes
CREATE INDEX idx_book_series_series_id ON book_series(series_id, sort_order);
CREATE INDEX idx_book_series_book_id ON book_series(book_id);

-- Book authors indexes
CREATE INDEX idx_book_authors_book_id ON book_authors(book_id, author_order);
CREATE INDEX idx_book_authors_author_id ON book_authors(author_id);

-- Book alternate titles indexes
CREATE INDEX idx_book_alternate_titles_book_id ON book_alternate_titles(book_id);
CREATE INDEX idx_book_alternate_titles_title ON book_alternate_titles(title);

-- Book tags indexes
CREATE INDEX idx_book_tags_tag ON book_tags(tag);
CREATE INDEX idx_book_tags_book_id ON book_tags(book_id);

-- Ebook files indexes
CREATE INDEX idx_ebook_files_book_id ON ebook_files(book_id);
CREATE INDEX idx_ebook_files_file_format ON ebook_files(file_format);

-- Audiobook files indexes
CREATE INDEX idx_audiobook_files_book_id ON audiobook_files(book_id);
CREATE INDEX idx_audiobook_files_file_format ON audiobook_files(file_format);

-- Audiobook narrators indexes
CREATE INDEX idx_audiobook_narrators_audiobook_id ON audiobook_narrators(audiobook_file_id);
CREATE INDEX idx_audiobook_narrators_narrator_id ON audiobook_narrators(narrator_id);

-- Reviews indexes
CREATE INDEX idx_reviews_book_id ON reviews(book_id);
CREATE INDEX idx_reviews_user_id ON reviews(user_id);
CREATE INDEX idx_reviews_recommended ON reviews(recommended) WHERE recommended = TRUE;
CREATE INDEX idx_reviews_num_stars ON reviews(num_stars) WHERE num_stars IS NOT NULL;

-- Amazon metadata indexes
CREATE INDEX idx_amazon_metadata_asin ON amazon_metadata(asin);
CREATE INDEX idx_amazon_metadata_sample_time ON amazon_metadata(sample_time);

-- ============================================================================
-- TRIGGERS AND FUNCTIONS
-- ============================================================================

-- Function to automatically update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply updated_at trigger to books table
CREATE TRIGGER update_books_updated_at BEFORE UPDATE ON books
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- FULL-TEXT SEARCH
-- ============================================================================

-- Function to update books search vector
CREATE OR REPLACE FUNCTION books_search_vector_update()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', COALESCE(NEW.title, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.isbn, '')), 'B');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER books_search_vector_trigger BEFORE INSERT OR UPDATE ON books
    FOR EACH ROW EXECUTE FUNCTION books_search_vector_update();

-- Function to update authors search vector
CREATE OR REPLACE FUNCTION authors_search_vector_update()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_vector := to_tsvector('english', COALESCE(NEW.primary_name, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER authors_search_vector_trigger BEFORE INSERT OR UPDATE ON authors
    FOR EACH ROW EXECUTE FUNCTION authors_search_vector_update();

-- Function to enforce that canonical authors cannot be pseudonyms
CREATE OR REPLACE FUNCTION check_pseudonym_chain()
RETURNS TRIGGER AS $$
DECLARE
    target_pseudonym_for BIGINT;
BEGIN
    -- If this author is not a pseudonym, no check needed
    IF NEW.pseudonym_for IS NULL THEN
        RETURN NEW;
    END IF;

    -- Check if the referenced author is itself a pseudonym
    SELECT pseudonym_for INTO target_pseudonym_for
    FROM authors
    WHERE author_id = NEW.pseudonym_for;

    IF target_pseudonym_for IS NOT NULL THEN
        RAISE EXCEPTION 'Cannot create pseudonym: author % is already a pseudonym of author %',
            NEW.pseudonym_for, target_pseudonym_for;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER check_pseudonym_chain_trigger BEFORE INSERT OR UPDATE ON authors
    FOR EACH ROW EXECUTE FUNCTION check_pseudonym_chain();
