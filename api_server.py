"""
Minimal dummy API server protected by Auth0.

Validates the Bearer token (Auth0 OBO delegated token) on every request using
Auth0's JWKS endpoint, then returns static data along with both the user and
agent identities extracted from the token claims.

Token claims with Agent as a Principal (OBO flow):
    sub     = Auth0 user ID       (who the agent is acting for)
    act.sub = Auth0 agent ID      (which agent is acting)

Install deps:
    pip install flask python-jose[cryptography] requests
"""

import os
import requests
from functools import wraps
from flask import Flask, request, jsonify, abort
from jose import jwt, JWTError
from dotenv import load_dotenv

load_dotenv()

AUTH0_DOMAIN       = os.environ["AUTH0_DOMAIN"]
AUTH0_API_AUDIENCE = os.environ["AUTH0_API_AUDIENCE"]

app = Flask(__name__)

# ── Token validation ──────────────────────────────────────────────────────────

def _get_jwks():
    url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    return requests.get(url, timeout=5).json()

def require_auth(f):
    """Decorator: validates Auth0 JWT access token on every request."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            abort(401, "Missing Bearer token")

        token = auth_header.split(" ", 1)[1]
        jwks = _get_jwks()

        try:
            payload = jwt.decode(
                token,
                jwks,
                algorithms=["RS256"],
                audience=AUTH0_API_AUDIENCE,
                issuer=f"https://{AUTH0_DOMAIN}/",
            )
        except JWTError as e:
            abort(401, f"Invalid token: {e}")

        request.token_claims = payload
        return f(*args, **kwargs)
    return decorated

# ── API routes ────────────────────────────────────────────────────────────────

@app.get("/data")
@require_auth
def get_data():
    """
    Returns static dummy data.
    With Agent as a Principal (OBO token):
        acting_user  → sub claim  (the user the agent is acting for)
        acting_agent → act.sub claim (the agent's identity)
    """
    claims = request.token_claims
    return jsonify({
        "message":      "Cross-App Access + Agent as a Principal successful!",
        "acting_user":  claims.get("sub"),
        "acting_agent": (claims.get("act") or {}).get("sub"),
        "data": [
            {"id": 1, "name": "Widget Alpha", "value": 42},
            {"id": 2, "name": "Widget Beta",  "value": 99},
        ],
    })

if __name__ == "__main__":
    app.run(port=8080, debug=True)
