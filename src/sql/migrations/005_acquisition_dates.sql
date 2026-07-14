-- 005: when each asset was acquired. This is external, manually-entered
-- knowledge — not metadata extracted from the file — so it lives beside the
-- raw epubs/m4bs tables rather than in them.
--
-- Acquisition is a property of the file, not the abstract book: the ebook and
-- the audiobook of one book are usually acquired at different times, hence one
-- table per asset type.
--
-- The asset id is the primary key, so an asset has at most one acquisition
-- date, and a row cannot exist without an asset. No row = not known; the
-- library predates this table, so most assets have none yet.
--
-- Convention matches books.publication_date: when only the year is known,
-- store YYYY-01-01.


CREATE TABLE epub_acquisitions (
    epub_id      INT  PRIMARY KEY REFERENCES epubs(id) ON DELETE CASCADE,
    acquired_on  DATE NOT NULL
);

CREATE TABLE m4b_acquisitions (
    m4b_id       INT  PRIMARY KEY REFERENCES m4bs(id) ON DELETE CASCADE,
    acquired_on  DATE NOT NULL
);

CREATE INDEX idx_epub_acquisitions_acquired_on ON epub_acquisitions(acquired_on);
CREATE INDEX idx_m4b_acquisitions_acquired_on  ON m4b_acquisitions(acquired_on);

