import os
from functools import wraps

import requests
from authlib.integrations.flask_client import OAuth
from flask import Flask, Response, redirect, render_template, session, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")

SERVICE_URL = os.environ.get("SERVICE_URL", "http://service:5000")

# OAuth setup
oauth = OAuth(app)
oauth.register(
    name="github",
    client_id=os.environ.get("GITHUB_CLIENT_ID"),
    client_secret=os.environ.get("GITHUB_CLIENT_SECRET"),
    access_token_url="https://github.com/login/oauth/access_token",
    authorize_url="https://github.com/login/oauth/authorize",
    api_base_url="https://api.github.com/",
    client_kwargs={"scope": "user:email"},
)


def login_required(f):
    """Decorator to require authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("landing"))
        return f(*args, **kwargs)
    return decorated_function


@app.route("/")
def landing():
    """Landing page with OAuth login options."""
    user = session.get("user")
    return render_template("landing.html", user=user)


@app.route("/login/github")
def login_github():
    """Redirect to GitHub OAuth."""
    redirect_uri = url_for("callback_github", _external=True)
    return oauth.github.authorize_redirect(redirect_uri)


@app.route("/callback/github")
def callback_github():
    """Handle GitHub OAuth callback."""
    token = oauth.github.authorize_access_token()
    resp = oauth.github.get("user")
    user_info = resp.json()
    session["user"] = {
        "provider": "github",
        "id": user_info["id"],
        "login": user_info["login"],
        "name": user_info.get("name") or user_info["login"],
        "email": user_info.get("email"),
        "avatar": user_info.get("avatar_url"),
    }
    session["token"] = token["access_token"]
    return redirect(url_for("objects_list"))


@app.route("/logout")
def logout():
    """Clear session and log out."""
    session.clear()
    return redirect(url_for("landing"))


@app.route("/objects")
@login_required
def objects_list():
    """Page listing S3 object keys from the service."""
    headers = {}
    if "token" in session:
        headers["Authorization"] = f"Bearer {session['token']}"

    try:
        response = requests.get(
            f"{SERVICE_URL}/api/objects",
            headers=headers,
            timeout=5,
        )
        response.raise_for_status()
        objects = response.json().get("objects", [])
    except requests.RequestException as e:
        objects = []
        app.logger.error(f"Failed to fetch objects: {e}")

    return render_template("objects.html", objects=objects, user=session.get("user"))


@app.route("/ebook/<path:s3_key>")
@login_required
def ebook_details(s3_key):
    """Page displaying EPUB metadata and cover."""
    headers = {}
    if "token" in session:
        headers["Authorization"] = f"Bearer {session['token']}"

    metadata = None
    error = None

    try:
        response = requests.get(
            f"{SERVICE_URL}/api/ebooks/{s3_key}/metadata",
            headers=headers,
            timeout=10,
        )
        if response.status_code == 200:
            metadata = response.json()
        else:
            error = response.json().get("error", "Failed to fetch metadata")
    except requests.RequestException as e:
        app.logger.error(f"Failed to fetch ebook metadata: {e}")
        error = "Service unavailable"

    return render_template(
        "ebook_details.html",
        metadata=metadata,
        s3_key=s3_key,
        error=error,
        user=session.get("user"),
    )


@app.route("/ebook/<path:s3_key>/cover")
@login_required
def ebook_cover_proxy(s3_key):
    """Proxy the cover image from the service."""
    headers = {}
    if "token" in session:
        headers["Authorization"] = f"Bearer {session['token']}"

    try:
        response = requests.get(
            f"{SERVICE_URL}/api/ebooks/{s3_key}/cover",
            headers=headers,
            timeout=10,
        )
        if response.status_code == 200:
            return Response(
                response.content,
                mimetype=response.headers.get("Content-Type", "image/jpeg"),
            )
        else:
            return "", 404
    except requests.RequestException:
        return "", 500


@app.route("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
