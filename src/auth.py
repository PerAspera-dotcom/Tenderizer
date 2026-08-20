"""Auth verification for the API (phase2/3 step 6).

Two independent schemes:
  - verify_token(): Clerk session tokens (RS256-signed JWTs) for regular
    tenant users. Verifying server-side means checking the signature against
    Clerk's published public keys (JWKS) rather than calling Clerk's API on
    every request — PyJWT's PyJWKClient fetches and caches that JWKS, only
    re-fetching when it sees an unfamiliar key id. CLERK_JWKS_URL is the
    instance's JWKS endpoint, e.g.
    https://<your-instance>.clerk.accounts.dev/.well-known/jwks.json (found
    on the Clerk dashboard, or derivable from the publishable key).
  - verify_ops_token(): a static shared secret (OPS_API_TOKEN) for
    operational endpoints that carry cross-tenant operational data, not a
    single tenant's business data — GET /api/reports/latest is the current
    example (not /api/health, which despite its name is the Phase-1
    tenant-facing Portal Health panel, gated on a regular Clerk session like
    everything else). These are the kind of endpoint an uptime monitor or
    internal dashboard hits without a human user session at all.
"""
import hmac
import logging
import os
import jwt
import requests
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

_jwks_client = None
_jwks_client_url = None


class AuthError(Exception):
    """The presented token is missing, malformed, expired, or otherwise
    fails verification — the caller's fault, maps to 401."""


class AuthNotConfigured(Exception):
    """Required server-side config (CLERK_JWKS_URL / OPS_API_TOKEN) isn't
    set — the server can't verify anything, so no token could ever succeed.
    Not the caller's fault; maps to 500."""


def _get_jwks_client():
    global _jwks_client, _jwks_client_url
    jwks_url = os.getenv("CLERK_JWKS_URL")
    if not jwks_url:
        raise AuthNotConfigured("CLERK_JWKS_URL is not set")
    if _jwks_client is None or _jwks_client_url != jwks_url:
        _jwks_client = PyJWKClient(jwks_url)
        _jwks_client_url = jwks_url
    return _jwks_client


def verify_token(token):
    """Decode and cryptographically verify a Clerk session token.

    Returns its claims dict (at least 'sub' = the Clerk user id) on success.
    Raises AuthError on any invalid/expired/malformed token, AuthNotConfigured
    if CLERK_JWKS_URL isn't set — never returns claims it hasn't verified.
    """
    client = _get_jwks_client()
    try:
        signing_key = client.get_signing_key_from_jwt(token)
        return jwt.decode(token, signing_key.key, algorithms=["RS256"],
                           options={"require": ["exp", "sub"]})
    except jwt.PyJWTError as e:
        raise AuthError(str(e)) from e


def list_organization_members(org_id):
    """The real Clerk org roster (emails) for the "forward to a colleague" /
    "assign for review" pickers — the session JWT only ever tells the server
    who the *caller* is and which org they're in (see api._org_id_from_claims),
    never the full membership list, so this is the one place the backend
    actually calls out to Clerk's API rather than just verifying a token.

    Same "inert if unconfigured" convention as alerts.py/Sentry: returns []
    (never raises) if CLERK_SECRET_KEY isn't set or the call fails, so a
    tenant that hasn't wired it up yet just sees an empty picker rather than
    a 500 — callers fall back to manual email entry in that case.
    """
    secret_key = os.getenv("CLERK_SECRET_KEY")
    if not secret_key or not org_id:
        return []
    try:
        resp = requests.get(
            f"https://api.clerk.com/v1/organizations/{org_id}/memberships",
            headers={"Authorization": f"Bearer {secret_key}"},
            params={"limit": 100},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except requests.RequestException:
        logger.exception("Clerk organization membership lookup failed")
        return []
    members = []
    for m in data:
        user = m.get("public_user_data") or {}
        # `identifier` is the member's primary email address on an
        # email/password or email-code Clerk instance (this one — see
        # get_current_identity's own claims.get("email") reliance).
        email = user.get("identifier")
        if email:
            members.append({"email": email, "clerk_user_id": user.get("user_id")})
    return members


def verify_ops_token(token):
    """Constant-time comparison against OPS_API_TOKEN.

    Returns True/False rather than raising AuthError on a mismatch — unlike
    a Clerk token, an ops token isn't "invalid" in a way worth explaining
    (no signature/expiry/claims to diagnose), and the caller (api.py) treats
    a mismatch as 403, not 401: a request with *some* token, just the wrong
    one, is a privilege failure, not a missing-credentials one. Raises
    AuthNotConfigured if OPS_API_TOKEN isn't set.
    """
    expected = os.getenv("OPS_API_TOKEN")
    if not expected:
        raise AuthNotConfigured("OPS_API_TOKEN is not set")
    return hmac.compare_digest(token, expected)
