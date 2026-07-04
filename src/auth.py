"""Clerk session-token verification (phase2/3 step 6).

Clerk session tokens are RS256-signed JWTs. Verifying them server-side means
checking the signature against Clerk's published public keys (JWKS) rather
than calling Clerk's API on every request — PyJWT's PyJWKClient fetches and
caches that JWKS, only re-fetching when it sees an unfamiliar key id.

CLERK_JWKS_URL is the instance's JWKS endpoint, e.g.
https://<your-instance>.clerk.accounts.dev/.well-known/jwks.json (found on
the Clerk dashboard, or derivable from the publishable key).
"""
import os
import jwt
from jwt import PyJWKClient

_jwks_client = None
_jwks_client_url = None


class AuthError(Exception):
    """The presented token is missing, malformed, expired, or otherwise
    fails verification — the caller's fault, maps to 401."""


class AuthNotConfigured(Exception):
    """CLERK_JWKS_URL isn't set — the server can't verify anything, so no
    token could ever succeed. Not the caller's fault; maps to 500."""


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
