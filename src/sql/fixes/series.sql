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

-- Merge series that were entered under more than one name. Verified before
-- running: each pair shares its author, and the merged positions do not collide,
-- so no renumbering is needed.

-- King & Maxwell (David Baldacci): three names for one series.
UPDATE books SET series_id = k.id FROM series k, series dup
 WHERE k.name = 'King & Maxwell' AND dup.name = 'King and Maxwell' AND books.series_id = dup.id;
UPDATE books SET series_id = k.id FROM series k, series dup
 WHERE k.name = 'King & Maxwell' AND dup.name = 'Sean King and Michelle Maxwell' AND books.series_id = dup.id;
DELETE FROM series WHERE name IN ('King and Maxwell', 'Sean King and Michelle Maxwell');

-- Red Rising (Pierce Brown)
UPDATE books SET series_id = k.id FROM series k, series dup
 WHERE k.name = 'Red Rising' AND dup.name = 'Red Rising Saga' AND books.series_id = dup.id;
DELETE FROM series WHERE name = 'Red Rising Saga';

-- Liveship Traders (Robin Hobb)
UPDATE books SET series_id = k.id FROM series k, series dup
 WHERE k.name = 'Liveship Traders' AND dup.name = 'Liveship Traders Trilogy' AND books.series_id = dup.id;
DELETE FROM series WHERE name = 'Liveship Traders Trilogy';

-- Traveler's Gate (Will Wight)
UPDATE books SET series_id = k.id FROM series k, series dup
 WHERE k.name = 'Traveler''s Gate' AND dup.name = 'Traveler''s Gate Trilogy' AND books.series_id = dup.id;
DELETE FROM series WHERE name = 'Traveler''s Gate Trilogy';

-- Singularity (William Hertling)
UPDATE books SET series_id = k.id FROM series k, series dup
 WHERE k.name = 'Singularity' AND dup.name = 'The Singularity Series' AND books.series_id = dup.id;
DELETE FROM series WHERE name = 'The Singularity Series';

-- Lincoln Lawyer (Michael Connelly)
UPDATE books SET series_id = k.id FROM series k, series dup
 WHERE k.name = 'Lincoln Lawyer' AND dup.name = 'A Lincoln Lawyer Novel' AND books.series_id = dup.id;
DELETE FROM series WHERE name = 'A Lincoln Lawyer Novel';

-- From here, keyed on ids: prod is a straight restore of dev, so the ids match.
-- (series 118 = Renée Ballard, 112 = Harry Bosch Universe, book 224 = Dark Sacred Night)

-- Dark Sacred Night is Renée Ballard #2 (it was filed alone under a "Harry Bosch
-- Universe" series). Ballard was missing position 2, so this fills it and empties 112.
UPDATE books SET series_id = 118, series_position = 2 WHERE id = 224;
DELETE FROM series WHERE id = 112;

-- Merge "Dirk Pitt Adventures" (62) into "Dirk Pitt" (101): one series split in
-- two, both Clive Cussler (later books add co-author Dirk Cussler). Renumber the
-- front to the intended chronological order (Pacific Vortex #1); 7-18 already lined up.
UPDATE books SET series_id = 101, series_position = 3  WHERE id = 408;   -- Iceberg (was 62 #2)
UPDATE books SET series_id = 101, series_position = 4  WHERE id = 577;   -- Raise the Titanic! (was 62 #4)
UPDATE books SET series_id = 101, series_position = 5  WHERE id = 1289;  -- Vixen 03 (was 62 #3)
UPDATE books SET series_id = 101, series_position = 9  WHERE id = 1010;  -- Treasure (was 62 #9)
UPDATE books SET series_id = 101, series_position = 10 WHERE id = 259;   -- Dragon (was 62 #10)
UPDATE books SET series_id = 101, series_position = 11 WHERE id = 599;   -- Sahara (was 62 #11)
UPDATE books SET series_id = 101, series_position = 15 WHERE id = 108;   -- Atlantis Found (was 62 #15)
UPDATE books SET series_position = 1 WHERE id = 539;   -- Pacific Vortex! (already in 101, was #6)
DELETE FROM series WHERE id = 62;

-- The Mayor of Noobtown was entered twice: book 1255 holds the epub, 1303 the m4b,
-- same series and position. One book in two formats -- move the m4b onto 1255 and
-- drop the now-empty 1303.
UPDATE book_m4bs SET book_id = 1255 WHERE book_id = 1303 AND m4b_id = 288;
DELETE FROM books WHERE id = 1303;

-- Books 375 and 1299 are distinct (books 2 and 1: different ASINs, different
-- chapters) but both carried the bare series name as their title. Give book 2 its
-- real title so they're distinguishable; 1299 keeps "He Who Fights with Monsters".
UPDATE books SET title = 'He Who Fights with Monsters 2',
                 sort_title = 'He Who Fights with Monsters 2'
 WHERE id = 375;

-- Pern position-9 collision: three books shared slot 9. Dragonsblood is #18 and
-- The Chronicles of Pern: First Fall is #12; Dragonsdawn keeps 9.
UPDATE books SET series_position = 18 WHERE id = 1107;   -- Dragonsblood
UPDATE books SET series_position = 12 WHERE id = 1243;   -- The Chronicles of Pern: First Fall

-- In Death position-17 collision: three books shared slot 17. Delusion in Death
-- is #35 and Memory in Death is #22 (a hole); Imitation in Death keeps 17.
UPDATE books SET series_position = 35 WHERE id = 1097;   -- Delusion in Death
UPDATE books SET series_position = 22 WHERE id = 1165;   -- Memory in Death

-- Jeeves position-7 collision: three books shared slot 7. Jeeves and the Feudal
-- Spirit is #11 and Joy in the Morning is #8; The Code of the Woosters keeps 7.
UPDATE books SET series_position = 11 WHERE id = 1150;   -- Jeeves and the Feudal Spirit
UPDATE books SET series_position = 8  WHERE id = 431;    -- Joy in the Morning

-- Longmire position-17 collision: three books shared slot 17. Hell and Back is
-- #18 and Next to Last Stand is #16; Daughter of the Morning Star keeps 17.
UPDATE books SET series_position = 18 WHERE id = 385;   -- Hell and Back
UPDATE books SET series_position = 16 WHERE id = 504;   -- Next to Last Stand

-- Merge Longmire (103) into Walt Longmire Mysteries (9): one series under two
-- names (1-15 as Walt Longmire Mysteries, 16-20 as Longmire). Keep the fuller
-- official name. Both had a book at 20; First Frost (in 9) is the canonical #20,
-- so Return to Sender (the newer book) becomes #21.
UPDATE books SET series_position = 21 WHERE id = 1341;   -- Return to Sender (was Longmire #20)
UPDATE books SET series_id = 9 WHERE series_id = 103;    -- move all Longmire books into Walt Longmire Mysteries
DELETE FROM series WHERE id = 103;

-- Penric & Desdemona position-11 collision: three books shared slot 11. Demon
-- Daughter is #12 and Penric and the Bandit is #13; Knot of Shadows keeps 11.
UPDATE books SET series_position = 12 WHERE id = 245;    -- Demon Daughter
UPDATE books SET series_position = 13 WHERE id = 1195;   -- Penric and the Bandit

-- Remove Heavy Weather from the Blandings Castle series (keep the book, just
-- unlink it), clearing the position-4 collision with Summer Lightning.
UPDATE books SET series_id = NULL, series_position = NULL WHERE id = 381;

-- Penric & Desdemona into chronological reading order (resolves the collisions at
-- 6 and 9). Positions 1-3, 11-13 already correct.
UPDATE books SET series_position = 4  WHERE id = 475;    -- Masquerade in Lodi
UPDATE books SET series_position = 5  WHERE id = 551;    -- Penric's Mission
UPDATE books SET series_position = 6  WHERE id = 487;    -- Mira's Last Dance
UPDATE books SET series_position = 7  WHERE id = 894;    -- The Prisoner of Limnos
UPDATE books SET series_position = 8  WHERE id = 866;    -- The Orphans of Raspay
UPDATE books SET series_position = 9  WHERE id = 880;    -- The Physicians of Vilnoc
UPDATE books SET series_position = 10 WHERE id = 1236;   -- The Assassins of Thasalon

-- Covenant of Steel position-2 collision: The Traitor is #3; The Martyr keeps 2.
UPDATE books SET series_position = 3 WHERE id = 948;   -- The Traitor

-- Culture into publication order (resolves the position-5 collision). Positions
-- 1-3 already correct.
UPDATE books SET series_position = 4 WHERE id = 307;   -- Excession
UPDATE books SET series_position = 5 WHERE id = 417;   -- Inversions
UPDATE books SET series_position = 6 WHERE id = 460;   -- Look to Windward
UPDATE books SET series_position = 7 WHERE id = 477;   -- Matter
UPDATE books SET series_position = 8 WHERE id = 679;   -- Surface Detail

-- Pern position-15 collision: Renegades of Pern -> 10 (was 13), freeing 13 for
-- The Dolphins of Pern (was tied at 15). The Masterharper of Pern keeps 15.
UPDATE books SET series_position = 10 WHERE id = 903;   -- The Renegades of Pern
UPDATE books SET series_position = 13 WHERE id = 760;   -- The Dolphins of Pern

-- Elvis Cole position-8 collision: The Last Detective is #9; L.A. Requiem keeps 8.
UPDATE books SET series_position = 9 WHERE id = 820;   -- The Last Detective

-- Harry Bosch position-19 collision: The Burning Room is #17; The Wrong Side of
-- Goodbye keeps 19.
UPDATE books SET series_position = 17 WHERE id = 727;   -- The Burning Room

-- Batch of collision fixes to publication order, each into a free slot. Orders
-- checked against author sites / bookseriesinorder / goodreads, 2026-07.
UPDATE books SET series_position = 3 WHERE id = 496;    -- Mr Mulliner: Mulliner Nights -> 3
UPDATE books SET series_position = 3 WHERE id = 536;    -- Psmith: Psmith, Journalist -> 3
UPDATE books SET series_position = 5 WHERE id = 145;    -- Sunny Randall: Blue Screen -> 5
UPDATE books SET series_position = 3 WHERE id = 172;    -- The Palladium Wars: Citadel -> 3
UPDATE books SET series_position = 2 WHERE id = 1143;   -- The Rho Agenda: Immune -> 2
UPDATE books SET series_position = 2 WHERE id = 1245;   -- Unbounded: The Cure -> 2 (author's numbering)
-- In Death (main-novel numbering): Witness keeps 10, Purity -> 15; Salvation keeps
-- 27 and Ritual in Death (a novella) joins the other unpositioned In Death novellas.
UPDATE books SET series_position = 15 WHERE id = 1213;   -- Purity in Death
UPDATE books SET series_position = NULL WHERE id = 1220; -- Ritual in Death (novella)
-- Jesse Stone: Sea Change(5), High Profile(6), Stranger in Paradise(7), Split Image(9)
UPDATE books SET series_position = 5 WHERE id = 611;    -- Sea Change
UPDATE books SET series_position = 6 WHERE id = 393;    -- High Profile

-- Spenser (13) was scrambled throughout (gaps + collisions). Renumber the whole
-- series to publication order, which the year-level publication_date already
-- encodes -- after correcting the one wrong date (Valediction was recorded 1988;
-- Parker published it 1984). Ordering by (publication_date, sort_title) then
-- reproduces canonical publication order, gapless 1-39.
UPDATE books SET publication_date = '1984-01-01' WHERE id = 1031;  -- Valediction
UPDATE books b
SET series_position = r.rn
FROM (SELECT id, row_number() OVER (ORDER BY publication_date, sort_title) AS rn
      FROM books WHERE series_id = 13) r
WHERE b.id = r.id;

-- Vorkosigan Saga (14) to publication order. Owned main books numbered; unowned
-- (Shards #1, Barrayar #8, Gentleman Jole #18) left as gaps. The three component
-- novellas that make up fix-ups are unpositioned (they fold into the parent's
-- number): Mountains of Mourning + Labyrinth -> Borders of Infinity (6),
-- Weatherman -> The Vor Game (7).
UPDATE books SET series_position = 2  WHERE id = 1276;  -- The Warrior's Apprentice
UPDATE books SET series_position = 3  WHERE id = 305;   -- Ethan of Athos
UPDATE books SET series_position = 4  WHERE id = 1117;  -- Falling Free
UPDATE books SET series_position = 5  WHERE id = 152;   -- Brothers in Arms
UPDATE books SET series_position = 6  WHERE id = 207;   -- Borders of Infinity
UPDATE books SET series_position = 7  WHERE id = 954;   -- The Vor Game
UPDATE books SET series_position = 9  WHERE id = 488;   -- Mirror Dance
UPDATE books SET series_position = 10 WHERE id = 215;   -- Cetaganda
UPDATE books SET series_position = 11 WHERE id = 484;   -- Memory
UPDATE books SET series_position = 12 WHERE id = 442;   -- Komarr
UPDATE books SET series_position = 13 WHERE id = 21;    -- A Civil Campaign
UPDATE books SET series_position = 14 WHERE id = 1098;  -- Diplomatic Immunity
UPDATE books SET series_position = 15 WHERE id = 1294;  -- Winterfair Gifts
UPDATE books SET series_position = 16 WHERE id = 223;   -- Cryoburn
UPDATE books SET series_position = 17 WHERE id = 156;   -- Captain Vorpatril's Alliance
UPDATE books SET series_position = NULL WHERE id = 1258; -- The Mountains of Mourning (in Borders of Infinity)
UPDATE books SET series_position = NULL WHERE id = 1158; -- Labyrinth (in Borders of Infinity)
UPDATE books SET series_position = NULL WHERE id = 1291; -- Weatherman (in The Vor Game)

-- Clear the Jeeves position-13 collision: unposition Jeeves in the Offing
-- (Stiff Upper Lip, Jeeves keeps 13).
UPDATE books SET series_position = NULL WHERE id = 428;   -- Jeeves in the Offing

-- Legends & Lattes: Bookshops & Bonedust from position 0 to 2.
UPDATE books SET series_position = 2 WHERE id = 80;

-- Titular book #1 existed in the library but was never linked to its own series,
-- leaving position 1 as a false gap. Same author each time. Found by matching
-- seriesless book titles against series names.
UPDATE books SET series_id = 44,  series_position = 1 WHERE id = 452;   -- Legends & Lattes
UPDATE books SET series_id = 175, series_position = 1 WHERE id = 426;   -- Jake's Magical Market
UPDATE books SET series_id = 333, series_position = 1 WHERE id = 876;   -- The Perfect Run

-- Goblins & Greatcoats into Legends & Lattes, unnumbered (side story).
UPDATE books SET series_id = 44, series_position = NULL WHERE id = 1071;

-- Night Probe! (Clive Cussler) was in the library but never linked to Dirk Pitt,
-- which is exactly why the merged Dirk Pitt series had a hole at position 6.
UPDATE books SET series_id = 101, series_position = 6 WHERE id = 508;

-- Harper Hall books belong in Dragonriders of Pern (75) at 4/5/6. Dragonsong was
-- split off as a singleton "Harper Hall Trilogy" (123) while its siblings
-- Dragonsinger and Dragondrums already lived in Pern; Dragonsinger sat wrongly at 4.
UPDATE books SET series_position = 5 WHERE id = 263;                 -- Dragonsinger 4 -> 5
UPDATE books SET series_id = 75, series_position = 4 WHERE id = 264; -- Dragonsong into Pern #4
DELETE FROM series WHERE id = 123;                                   -- empty Harper Hall Trilogy

-- Moreta: Dragonlady of Pern is #7 in reading order (position 7 was empty).
UPDATE books SET series_position = 7 WHERE id = 1169;

-- Nerilka's Story is #8 (was unpositioned; position 8 freed by Moreta's move).
UPDATE books SET series_position = 8 WHERE id = 1177;

-- Dragon's Kin is #17 (was unpositioned; position 17 was empty).
UPDATE books SET series_position = 17 WHERE id = 1102;

-- Dragon's Fire #19 (was 20), Dragon Harper #20 (was unpositioned).
UPDATE books SET series_position = 19 WHERE id = 1101;
UPDATE books SET series_position = 20 WHERE id = 1100;

-- Dragonheart is #21 (was unpositioned).
UPDATE books SET series_position = 21 WHERE id = 1106;

-- Dragon's Time is #23 (was 22).
UPDATE books SET series_position = 23 WHERE id = 1103;

-- Dragongirl is #22 (was 28).
UPDATE books SET series_position = 22 WHERE id = 1105;

-- "Oh, Great! I Was Reincarnated as a Farmer" is #1 in Unorthodox Farming (341);
-- it was in the library but unlinked, leaving position 1 empty.
UPDATE books SET series_id = 341, series_position = 1 WHERE id = 1347;

-- Blackout (204, the Willis one) is #1 in Oxford Time Travel; it was the lone book
-- in that series, sitting at 3. (Distinct from the Newsflesh Blackout, book 205.)
UPDATE books SET series_position = 1 WHERE id = 204;

-- Normalize title "All the Skills: Book 2" -> "All the Skills 2" (matches siblings).
UPDATE books SET title = 'All the Skills 2', sort_title = 'All the Skills 2' WHERE id = 34;

-- The "Mage Errant" phantom book (1300) was just a home for the Publisher's Pack
-- m4b (books 1-2). Move that m4b onto Into the Labyrinth (414, #1), then delete the
-- now-empty phantom (its only remaining link, book_authors, cascades on delete).
UPDATE book_m4bs SET book_id = 414 WHERE m4b_id = 164 AND book_id = 1300;
DELETE FROM books WHERE id = 1300;

-- The Hallowed Hunt is Chalion #3. It was split off as a singleton "World of the
-- Five Gods" (258); moving it in empties and removes that series.
UPDATE books SET series_id = 203, series_position = 3 WHERE id = 798;
DELETE FROM series WHERE id = 258;

-- Vorkosigan Saga (14): full 15-novel reading order per user. Shards of Honor (624)
-- and Barrayar (115) merged in from the separate "Cordelia Naismith" (64), which is
-- then empty and removed. Borders of Infinity and Winterfair Gifts (novella
-- collection / novella) left in the series but unpositioned, like the other novellas.
UPDATE books SET series_id = 14, series_position = 1  WHERE id = 624;   -- Shards of Honor
UPDATE books SET series_position = 2                  WHERE id = 1276;  -- The Warrior's Apprentice
UPDATE books SET series_position = 3                  WHERE id = 305;   -- Ethan of Athos
UPDATE books SET series_position = 4                  WHERE id = 1117;  -- Falling Free
UPDATE books SET series_position = 5                  WHERE id = 152;   -- Brothers in Arms
UPDATE books SET series_position = 6                  WHERE id = 954;   -- The Vor Game
UPDATE books SET series_id = 14, series_position = 7  WHERE id = 115;   -- Barrayar
UPDATE books SET series_position = 8                  WHERE id = 488;   -- Mirror Dance
UPDATE books SET series_position = 9                  WHERE id = 215;   -- Cetaganda
UPDATE books SET series_position = 10                 WHERE id = 484;   -- Memory
UPDATE books SET series_position = 11                 WHERE id = 442;   -- Komarr
UPDATE books SET series_position = 12                 WHERE id = 21;    -- A Civil Campaign
UPDATE books SET series_position = 13                 WHERE id = 1098;  -- Diplomatic Immunity
UPDATE books SET series_position = 14                 WHERE id = 223;   -- Cryoburn
UPDATE books SET series_position = 15                 WHERE id = 156;   -- Captain Vorpatril's Alliance
UPDATE books SET series_position = NULL               WHERE id = 207;   -- Borders of Infinity
UPDATE books SET series_position = NULL               WHERE id = 1294;  -- Winterfair Gifts
DELETE FROM series WHERE id = 64;                                       -- now-empty Cordelia Naismith

-- Merge Lincoln Lawyer (212, books 6-7) into Mickey Haller (239, books 1-5).
UPDATE books SET series_id = 239 WHERE series_id = 212;
DELETE FROM series WHERE id = 212;

-- Harry Bosch: Trunk Music is #5 (was 6; position 5 was empty).
UPDATE books SET series_position = 5 WHERE id = 1020;

-- Harry Bosch: The Black Box is #16 (was 18; position 16 was empty).
UPDATE books SET series_position = 16 WHERE id = 713;

-- Harry Bosch: The Crossing is #18 (was 20; position 18 was empty).
UPDATE books SET series_position = 18 WHERE id = 1244;

-- Harry Bosch: Two Kinds of Truth is #20 (was 22; position 20 was empty).
UPDATE books SET series_position = 20 WHERE id = 1283;

-- Double Share is Trader's Tales (46) #4; it was split off as a singleton
-- "Solar Clipper" (122), which is then empty and removed.
UPDATE books SET series_id = 46, series_position = 4 WHERE id = 258;
DELETE FROM series WHERE id = 122;

-- Merge "Smuggler's Tales" (342, Suicide Run #2) into its full-named twin
-- "Smuggler's Tales From The Golden Age Of The Solar Clipper" (188, Milk Run #1).
UPDATE books SET series_id = 188 WHERE id = 1335;
DELETE FROM series WHERE id = 342;

-- Normalize "Defiance of the Fall: Book 10" -> "Defiance of the Fall 10" (siblings).
UPDATE books SET title = 'Defiance of the Fall 10', sort_title = 'Defiance of the Fall 10' WHERE id = 232;

-- Dragon's Fire (1101) and Dragon Harper (1100) are Anne & Todd McCaffrey (618)
-- co-writes; they were credited to Anne alone. Add Todd as second author.
INSERT INTO book_authors (book_id, author_id, position) VALUES
  (1101, 618, 2),
  (1100, 618, 2)
ON CONFLICT (book_id, author_id) DO NOTHING;

COMMIT;
