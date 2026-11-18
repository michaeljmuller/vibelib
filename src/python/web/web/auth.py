"""Google OAuth sign-in, and the gate that keeps the rest of the library private.

Two separate questions, answered by two separate things. *Who are you* is Google's
job, and it does it well. *May you come in* is the users table's job (migration
006), and Google cannot help with it: it will cheerfully authenticate any of the
billions of Google accounts in existence. So a successful Google login is where
authorization starts, not where it ends -- the callback takes the verified email
Google returns and looks it up on the allowlist, and a miss is shown the door.
"""

import base64
import binascii
import hashlib
import hmac
import logging
import os
import re
from pathlib import Path
from urllib.parse import quote

import bcrypt
from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from . import db

log = logging.getLogger("uvicorn.error")

# The app's own public origin, stated rather than inferred. Inferring it from the
# incoming request breaks the moment a TLS-terminating proxy sits in front: the app
# sees a plain http:// request to an internal hostname and builds a redirect_uri
# that is not the one registered with Google, which rejects the login outright
# (redirect_uri_mismatch). The default is the one origin where that cannot happen.
BASE_URL = os.environ.get("OAUTH_BASE_URL", "http://localhost:8000").rstrip("/")

# Deliberately no default. This secret signs the session cookie, so a fallback value
# would mean an unset variable in production yields cookies anyone can forge --
# silently, and indistinguishably from a working system. Better to refuse to boot.
SESSION_SECRET = os.environ["OAUTH_SESSION_SECRET"]

# Must byte-for-byte match a redirect URI registered on the OAuth client in the
# Google Cloud console (Google Auth Platform -> Clients).
REDIRECT_URI = f"{BASE_URL}/auth/callback"

# Everything not listed here needs a session. Stated as a small allowlist rather
# than a list of protected routes so that the default for any route added later --
# including one somebody forgets to think about -- is private.
PUBLIC_PATHS = frozenset({"/login", "/auth/login", "/auth/callback", "/auth/logout"})
PUBLIC_PREFIXES = ("/static/",)  # the login page is built out of these

# --- the dev bypass ---------------------------------------------------------
#
# Set OAUTH_DEV_USER to an email and the app stops asking Google anything: every
# request arrives already signed in as that person. It exists because testing a
# gated app otherwise means a real browser and a real Google password, which you
# cannot script.
#
# It is also, precisely, a switch that turns authentication off, so it is built to
# be impossible to leave on by accident:
#
#   1. It refuses to work over https. Setting it on a real deployment does not open
#      the library -- it stops the app from booting at all (see _check_dev_user).
#      Fail loudly on the first deploy, rather than quietly serving the world.
#   2. It cannot invent access. The email still has to be on the users allowlist, so
#      the bypass skips the Google round-trip, not the allowlist.
#   3. It says so, every single startup, in the logs.
DEV_USER = os.environ.get("OAUTH_DEV_USER", "").strip().lower() or None


def check_dev_user() -> None:
    """Called at startup, before the app serves anything."""
    if not DEV_USER:
        return
    if BASE_URL.startswith("https://"):
        raise RuntimeError(
            f"OAUTH_DEV_USER is set ({DEV_USER}) but OAUTH_BASE_URL is {BASE_URL}. "
            "That combination would serve the entire library to anyone who asked. "
            "Refusing to start: unset OAUTH_DEV_USER."
        )
    log.warning("=" * 78)
    log.warning("AUTHENTICATION IS DISABLED. Every request is signed in as %s.", DEV_USER)
    log.warning("OAUTH_DEV_USER is set. This must never be set anywhere public.")
    log.warning("=" * 78)

# --- the API token ------------------------------------------------------------
#
# Set ADMIN_API_TOKEN and a script can send `Authorization: Bearer <token>`
# instead of signing in with Google. It exists so that dates can be corrected in
# bulk (acquisition dates from purchase histories, publication dates the files
# state), by a tool rather than by hand.
#
# It is not an admin login. It reaches only the operations in TOKEN_SCOPE: find a
# book, read its files and their dates, set a file's acquisition date, and set a
# book's publication date. Everything else is a 403 for a token request,
# including any route added later, so a leaked token can at worst change dates.
# It cannot add people to the allowlist,
# relink or delete books, start an ingest, or download a book.
#
# A token request never reads or writes the session, so the token cannot be
# turned into a browser login. Unset, token requests are refused outright.
API_TOKEN = os.environ.get("ADMIN_API_TOKEN", "").strip() or None

# A token is the whole credential, with no second factor and no expiry, so a
# short one is refused at startup rather than accepted quietly.
MIN_TOKEN_LENGTH = 32

TOKEN_SCOPE = (
    ("GET", re.compile(r"/api/books")),
    ("GET", re.compile(r"/api/books/\d+")),
    ("PUT", re.compile(r"/api/admin/assets/(epub|m4b)/\d+/acquired-on")),
    ("PUT", re.compile(r"/api/admin/books/\d+/publication-date")),
)

# Who a token request is, as far as require_admin and the routes are concerned.
# No id: nothing in TOKEN_SCOPE looks one up, and a route that did would be one
# the token has no business reaching.
TOKEN_USER = {"id": None, "email": "api-token", "name": "API token", "is_admin": True}


def check_api_token() -> None:
    """Called at startup, before the app serves anything."""
    if API_TOKEN and len(API_TOKEN) < MIN_TOKEN_LENGTH:
        raise RuntimeError(
            f"ADMIN_API_TOKEN is {len(API_TOKEN)} characters; it must be at least "
            f"{MIN_TOKEN_LENGTH}. Generate one with: openssl rand -hex 32"
        )
    if API_TOKEN:
        log.info("ADMIN_API_TOKEN is set: token requests may set acquisition and publication dates.")


def token_allows(method: str, path: str) -> bool:
    return any(method == m and pattern.fullmatch(path) for m, pattern in TOKEN_SCOPE)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else None


# --- OPDS devices -------------------------------------------------------------
#
# An e-reader (KOReader on a Kindle) cannot do a Google sign-in, so the OPDS
# catalog also takes HTTP Basic auth, checked against an htpasswd file. One line
# per device, with a bcrypt hash: `htpasswd -nbB <name> <password>` writes one.
# Deleting a line revokes that device on its next request. The file is re-read
# whenever it changes, so neither needs a restart. No file means no device gets in.
#
# A device reaches only OPDS_SCOPE: the catalog, and epub downloads and covers.
# Audiobooks are left out of OPDS on purpose, and so is everything else; any
# route added later is a 403 for a device too.
OPDS_HTPASSWD = Path(os.environ.get("OPDS_HTPASSWD", "/opds/htpasswd"))

OPDS_SCOPE = (
    ("GET", re.compile(r"/opds(/.*)?")),
    ("GET", re.compile(r"/download/epub/\d+")),
    ("GET", re.compile(r"/covers/epub/(thumb/)?\d+")),
)

# The 401 that makes a reader app ask for a username and password.
CHALLENGE = {"WWW-Authenticate": 'Basic realm="vibelib", charset="UTF-8"'}


class Htpasswd:
    """The device list, re-read when the file changes.

    A reader fetches a feed and then a cover for every book on it, each with the
    same credentials. bcrypt is slow on purpose, so a password that checked out
    once is remembered (as a SHA-256 of it, never the password itself) until the
    file next changes.
    """

    def __init__(self, path: Path):
        self.path = path
        self._stamp: tuple | None = None
        self._hashes: dict[str, bytes] = {}
        self._verified: dict[str, bytes] = {}

    def _reload(self) -> None:
        try:
            st = self.path.stat()
            stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        except FileNotFoundError:
            stamp = None
        if stamp == self._stamp:
            return

        hashes: dict[str, bytes] = {}
        if stamp is not None:
            for number, line in enumerate(self.path.read_text().splitlines(), 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                name, sep, hashed = line.partition(":")
                # Only bcrypt. htpasswd's other formats include unsalted SHA-1
                # and plain text, and an old line in one of them should fail
                # loudly rather than work.
                if not sep or not hashed.startswith(("$2y$", "$2b$", "$2a$")):
                    log.warning("%s line %d is not name:bcrypt-hash (use htpasswd -B); ignored",
                                self.path, number)
                    continue
                # $2y$ is Apache's name for what the bcrypt library calls $2b$.
                hashes[name] = ("$2b$" + hashed[4:]).encode()
        self._hashes = hashes
        self._verified = {}
        self._stamp = stamp

    def names(self) -> list[str]:
        self._reload()
        return sorted(self._hashes)

    def check(self, name: str, password: str) -> bool:
        self._reload()
        hashed = self._hashes.get(name)
        if hashed is None:
            return False
        digest = hashlib.sha256(password.encode()).digest()
        remembered = self._verified.get(name)
        if remembered is not None and hmac.compare_digest(remembered, digest):
            return True
        if bcrypt.checkpw(password.encode(), hashed):
            self._verified[name] = digest
            return True
        return False


devices = Htpasswd(OPDS_HTPASSWD)


def check_opds() -> None:
    """Called at startup: says whether any device can reach the catalog."""
    names = devices.names()
    if names:
        log.info("OPDS: %d device(s) in %s: %s", len(names), OPDS_HTPASSWD, ", ".join(names))
    else:
        log.info("OPDS: no devices in %s; the catalog refuses everyone but signed-in users.",
                 OPDS_HTPASSWD)


def opds_allows(method: str, path: str) -> bool:
    return any(method == m and pattern.fullmatch(path) for m, pattern in OPDS_SCOPE)


def _basic(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return ("", "")  # malformed: judged, and refused, like a wrong password
    name, _, password = decoded.partition(":")
    return name, password


oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.environ["OAUTH_GOOGLE_CLIENT_ID"],
    client_secret=os.environ["OAUTH_GOOGLE_CLIENT_SECRET"],
    # Google's discovery document. Authlib reads the current endpoints and token
    # signing keys from it, so none of them are pinned here and key rotation on
    # Google's side is a non-event.
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

router = APIRouter()


def _safe_next(target: str | None) -> str:
    """Where to drop someone after login. Only same-origin paths: a bare "/foo" is
    fine, but "//evil.example" is a protocol-relative URL the browser would happily
    treat as another site, which would turn this into an open redirect."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return "/"


@router.get("/auth/login")
async def login(request: Request, next: str | None = None):
    # Stashed in the session so the callback -- which Google calls with only its own
    # parameters -- can still put the person back where they were headed.
    request.session["next"] = _safe_next(next)
    return await oauth.google.authorize_redirect(request, REDIRECT_URI)


@router.get("/auth/callback")
async def callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        # Expired code, mismatched state, or they clicked "cancel" on Google's
        # screen. Nothing here is actionable by the user beyond trying again.
        return RedirectResponse("/login?error=failed", status_code=302)

    claims = token["userinfo"]

    # Some Google account types can carry an address the account does not actually
    # control, flagged by this claim. Trusting an unverified one would let anyone
    # who can create such an account walk in as any allowed address.
    if not claims.get("email_verified"):
        return RedirectResponse("/login?error=unverified", status_code=302)

    email = claims["email"].lower()
    if db.get_user(email) is None:
        return RedirectResponse("/login?error=denied", status_code=302)

    name = claims.get("name")
    db.record_login(email, name)

    # Read the destination before clearing: a fresh session for the newly-authenticated
    # person, rather than promoting the one that carried them through the handshake.
    destination = _safe_next(request.session.get("next"))
    request.session.clear()
    request.session["email"] = email
    request.session["name"] = name or email
    return RedirectResponse(destination, status_code=302)


@router.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@router.get("/api/me")
def me(request: Request):
    """Who the browser is signed in as. Reachable only through the gate below, so the
    user is guaranteed to have been looked up by the time this runs."""
    user = current_user(request)
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "is_admin": user["is_admin"],
    }


def current_user(request: Request) -> dict:
    """The signed-in user, as the gate looked them up. Only valid behind the gate,
    which is everywhere except PUBLIC_PATHS."""
    return request.state.user


def require_admin(request: Request) -> dict:
    """FastAPI dependency for the routes that manage the allowlist."""
    user = current_user(request)
    if not user["is_admin"]:
        raise HTTPException(403, "admins only")
    return user


class RequireLogin(BaseHTTPMiddleware):
    """The gate. Default-deny: anything outside PUBLIC_PATHS needs a session."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        token = _bearer(request)
        if token is not None:
            return await self._token_request(request, call_next, token, path)

        basic = _basic(request)
        if basic is not None:
            return await self._device_request(request, call_next, *basic, path)

        email = request.session.get("email")

        if email is None and DEV_USER:
            # The bypass skips Google, not the allowlist: the email still has to be
            # on the allowlist, checked below like anyone else's.
            email = DEV_USER

        # Looked up on every request rather than trusted from the cookie. The cookie
        # only proves who someone is, and it says so for a month -- but an admin who
        # takes an account off the list means *now*, not whenever that cookie happens to
        # lapse. One indexed lookup is a fair price for revocation that works.
        user = db.get_user(email) if email else None

        if user is None:
            if email and DEV_USER == email:
                log.warning("OAUTH_DEV_USER=%s is not on the allowlist.", DEV_USER)
            # They are off the list (or never were on it): the session is worthless,
            # so do not leave it in their browser pretending otherwise.
            request.session.clear()
            return self._turn_away(request, path)

        request.state.user = user
        request.session["email"] = user["email"]
        request.session["name"] = user["name"] or user["email"]
        return await call_next(request)

    async def _token_request(self, request: Request, call_next, token: str, path: str):
        # A request that names a token is judged by the token alone, never by a
        # cookie it also carries: a bad token is a 401 even from a signed-in browser.
        if API_TOKEN is None or not hmac.compare_digest(token.encode(), API_TOKEN.encode()):
            return JSONResponse({"detail": "invalid token"}, status_code=401)
        if not token_allows(request.method, path):
            log.warning("API token refused for %s %s: outside its scope", request.method, path)
            return JSONResponse({"detail": "outside the token's scope"}, status_code=403)
        request.state.user = TOKEN_USER
        return await call_next(request)

    async def _device_request(self, request: Request, call_next, name: str, password: str, path: str):
        # Like a token request: judged by the credentials alone, and it never
        # touches the session, so a device password cannot become a browser login.
        if not devices.check(name, password):
            log.warning("OPDS login refused for %r", name)
            return JSONResponse({"detail": "invalid credentials"}, status_code=401, headers=CHALLENGE)
        if not opds_allows(request.method, path):
            log.warning("OPDS device %r refused for %s %s: outside its scope", name, request.method, path)
            return JSONResponse({"detail": "outside the device's scope"}, status_code=403)
        request.state.user = {"id": None, "email": f"opds:{name}", "name": name, "is_admin": False}
        return await call_next(request)

    def _turn_away(self, request: Request, path: str):
        # A script wants a status code it can branch on; a person wants the login
        # page, and to arrive at the page they originally asked for once they are
        # through it. Sending a 302 to an XHR would just yield the login HTML with a
        # 200 stapled to it, which is indistinguishable from success.
        if path.startswith("/api/"):
            return JSONResponse({"detail": "not authenticated"}, status_code=401)
        # A reader app cannot follow a redirect to a Google login; a challenge is
        # what makes it ask for the device's username and password.
        if path == "/opds" or path.startswith("/opds/"):
            return JSONResponse({"detail": "not authenticated"}, status_code=401, headers=CHALLENGE)

        target = path + ("?" + request.url.query if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target)}", status_code=302)
