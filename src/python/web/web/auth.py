"""Google OAuth sign-in, and the gate that keeps the rest of the library private.

Two separate questions, answered by two separate things. *Who are you* is Google's
job, and it does it well. *May you come in* is the users table's job (migration
006), and Google cannot help with it: it will cheerfully authenticate any of the
billions of Google accounts in existence. So a successful Google login is where
authorization starts, not where it ends -- the callback takes the verified email
Google returns and looks it up on the allowlist, and a miss is shown the door.
"""

import logging
import os
from urllib.parse import quote

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Request
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
#      the bypass skips the Google round-trip, not the invitation.
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
    # who can create such an account walk in as any invited address.
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
    """Who the browser is signed in as. Reachable only through the gate below, so a
    session is guaranteed to exist by the time this runs."""
    return {"email": request.session["email"], "name": request.session["name"]}


class RequireLogin(BaseHTTPMiddleware):
    """The gate. Default-deny: anything outside PUBLIC_PATHS needs a session."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        is_public = path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)
        if is_public or "email" in request.session:
            return await call_next(request)

        if DEV_USER:
            # Still checked against the allowlist: the bypass skips Google, not the
            # invitation. An unknown OAUTH_DEV_USER falls through to the login page,
            # which is the same answer a stranger gets.
            user = db.get_user(DEV_USER)
            if user:
                request.session["email"] = user["email"]
                request.session["name"] = user["name"] or user["email"]
                return await call_next(request)
            log.warning("OAUTH_DEV_USER=%s is not on the allowlist; ignoring it.", DEV_USER)

        # A script wants a status code it can branch on; a person wants the login
        # page, and to arrive at the page they originally asked for once they are
        # through it. Sending a 302 to an XHR would just yield the login HTML with a
        # 200 stapled to it, which is indistinguishable from success.
        if path.startswith("/api/"):
            return JSONResponse({"detail": "not authenticated"}, status_code=401)

        target = path + ("?" + request.url.query if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target)}", status_code=302)
