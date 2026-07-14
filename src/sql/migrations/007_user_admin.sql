-- 007: who can invite, and who can throw people out.
--
-- A boolean, not a roles table. There is exactly one privilege in this library --
-- managing the guest list -- and inventing a role system to express a single bit
-- would be modelling a problem nobody has. If a second privilege ever appears,
-- that is the moment to reach for something bigger, and adding it then costs no
-- more than adding it now.
--
-- Bootstrapping: the earliest-invited user becomes the first admin. That is the
-- person who stood the library up -- there was nobody else to invite them -- so it
-- is a fact about the data rather than a name hardcoded into a migration. Every
-- later admin is promoted by an existing one (util/users.sh promote).
--
-- Note that a database with no users at all gets no admin, and no way to make one
-- through the app. That is correct: an empty allowlist means nobody can log in to
-- do the promoting either, and the way out is the shell, which is where it belongs.

ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT false;

UPDATE users SET is_admin = true
WHERE id = (SELECT id FROM users ORDER BY id LIMIT 1);
