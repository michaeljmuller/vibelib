-- 008: invited_on -> added_on.
--
-- Nothing about the behaviour changes. "Invitation" was always the wrong word: it
-- suggests something was sent and something was accepted, and neither ever
-- happened. A row in this table means one thing -- this Google account is allowed
-- in -- and the column should say when it was allowed in, not imply a ceremony
-- that does not exist.

ALTER TABLE users RENAME COLUMN invited_on TO added_on;
