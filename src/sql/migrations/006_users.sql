-- 006: who is allowed in.
--
-- Sign-in is Google OAuth, but Google authenticates every Google account in the
-- world -- it establishes who someone is, not whether they may enter. This table
-- is the second half: a row here is an invitation, and no row is a closed door.
-- The login callback checks one against the other.
--
-- Keyed on email rather than Google's immutable subject id, because an invitation
-- is extended to an address before that person has ever signed in -- at which
-- point there is no subject id to write down. The trade: someone who moves to a
-- new address needs a new row. At family scale that is a text message, not a
-- problem.
--
-- name is cached from the OpenID profile on each login so the UI can greet people
-- by name. It is a convenience, never identity -- email is identity.


CREATE TABLE users (
    id             SERIAL PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,       -- always stored lowercase; the app folds case before it looks anyone up
    name           TEXT,
    invited_on     DATE NOT NULL DEFAULT CURRENT_DATE,
    last_login_at  TIMESTAMPTZ                 -- NULL until they first sign in: an invitation not yet taken up
);

