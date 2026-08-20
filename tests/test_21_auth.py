"""Step 21 — real Clerk auth (phase2/3 step 6).

auth.verify_token() is tested against a real, locally-generated RSA keypair
(no real Clerk instance/network involved) — PyJWKClient is monkeypatched to
serve our own public key instead of fetching Clerk's JWKS, so the actual
jwt.decode()/signature-verification code path is genuinely exercised, not
just the mock wiring.

api.get_current_tenant_id() is tested with auth.verify_token() itself
monkeypatched (mirroring how translate.py's DeepL calls are mocked
elsewhere in this suite) — that function's own correctness is test_auth's
job, not api's.
"""
import time

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import api
import auth
import store


def _generate_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


class _FakeJWKClient:
    def __init__(self, public_pem):
        self._public_pem = public_pem

    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey(self._public_pem)


@pytest.fixture
def rsa_keypair():
    return _generate_rsa_keypair()


@pytest.fixture(autouse=True)
def _reset_jwks_client_cache(monkeypatch):
    # verify_token() memoizes its PyJWKClient at module scope — every test
    # gets a clean slate regardless of test order.
    monkeypatch.setattr(auth, "_jwks_client", None)
    monkeypatch.setattr(auth, "_jwks_client_url", None)


def _configure_fake_jwks(monkeypatch, public_pem):
    monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/.well-known/jwks.json")
    monkeypatch.setattr(auth, "PyJWKClient", lambda url: _FakeJWKClient(public_pem))


# ── auth.verify_token() — real signature verification, fake JWKS source ────

def test_verify_token_accepts_a_validly_signed_token(monkeypatch, rsa_keypair):
    private_pem, public_pem = rsa_keypair
    _configure_fake_jwks(monkeypatch, public_pem)
    token = jwt.encode({"sub": "user_1", "exp": int(time.time()) + 3600}, private_pem, algorithm="RS256")
    assert auth.verify_token(token)["sub"] == "user_1"


def test_verify_token_rejects_an_expired_token(monkeypatch, rsa_keypair):
    private_pem, public_pem = rsa_keypair
    _configure_fake_jwks(monkeypatch, public_pem)
    token = jwt.encode({"sub": "user_1", "exp": int(time.time()) - 10}, private_pem, algorithm="RS256")
    with pytest.raises(auth.AuthError):
        auth.verify_token(token)


def test_verify_token_rejects_a_token_signed_by_a_different_key(monkeypatch, rsa_keypair):
    _, public_pem = rsa_keypair
    other_private_pem, _ = _generate_rsa_keypair()
    _configure_fake_jwks(monkeypatch, public_pem)
    token = jwt.encode({"sub": "user_1", "exp": int(time.time()) + 3600}, other_private_pem, algorithm="RS256")
    with pytest.raises(auth.AuthError):
        auth.verify_token(token)


def test_verify_token_rejects_a_token_missing_required_claims(monkeypatch, rsa_keypair):
    private_pem, public_pem = rsa_keypair
    _configure_fake_jwks(monkeypatch, public_pem)
    token = jwt.encode({"exp": int(time.time()) + 3600}, private_pem, algorithm="RS256")  # no 'sub'
    with pytest.raises(auth.AuthError):
        auth.verify_token(token)


def test_verify_token_raises_when_jwks_url_not_configured(monkeypatch):
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
    with pytest.raises(auth.AuthNotConfigured):
        auth.verify_token("irrelevant")


# ── api.get_current_tenant_id() — auth.verify_token() itself mocked ────────

def _creds(token="tok"):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_missing_token_is_401():
    with pytest.raises(HTTPException) as exc:
        api.get_current_tenant_id(creds=None)
    assert exc.value.status_code == 401


def test_invalid_token_is_401(monkeypatch):
    def boom(token):
        raise auth.AuthError("bad signature")
    monkeypatch.setattr(auth, "verify_token", boom)
    with pytest.raises(HTTPException) as exc:
        api.get_current_tenant_id(creds=_creds())
    assert exc.value.status_code == 401


def test_valid_token_for_existing_clerk_user_resolves_its_tenant(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    conn = store.init_db(db_path)
    tenant_id = store.create_tenant_for_clerk_user(conn, "user_existing")

    monkeypatch.setattr(auth, "verify_token", lambda token: {"sub": "user_existing"})
    assert api.get_current_tenant_id(creds=_creds()) == tenant_id


def test_valid_token_for_a_new_clerk_user_auto_provisions_a_tenant(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    store.init_db(db_path)  # seeds the default tenant (id 1), not "user_new"

    monkeypatch.setattr(auth, "verify_token",
                         lambda token: {"sub": "user_new", "email": "new@example.com"})
    resolved = api.get_current_tenant_id(creds=_creds())

    conn = store.init_db(db_path)
    assert resolved != 1
    assert store.get_tenant_id_by_clerk_user_id(conn, "user_new") == resolved


def test_same_clerk_user_resolves_to_the_same_tenant_every_time(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(api, "DB_PATH", db_path)
    store.init_db(db_path)

    monkeypatch.setattr(auth, "verify_token", lambda token: {"sub": "user_repeat"})
    first = api.get_current_tenant_id(creds=_creds())
    second = api.get_current_tenant_id(creds=_creds())
    assert first == second


def test_concurrent_first_requests_for_a_new_clerk_user_dont_500(tmp_path):
    """A brand-new user's first page load fires several API calls at once,
    each resolving get_tenant_id_by_clerk_user_id -> None before any of them
    has committed a tenant row, so more than one calls create_tenant_for_...
    for the same clerk_user_id. The loser must resolve to the winner's row,
    not raise the unique-constraint IntegrityError (which previously reached
    the browser as a CORS error, not a 401/500 — see store.py).
    """
    db_path = str(tmp_path / "t.db")
    conn = store.init_db(db_path)

    first = store.create_tenant_for_clerk_user(conn, "user_racing")
    second = store.create_tenant_for_clerk_user(conn, "user_racing")
    assert first == second


# ── auth.verify_ops_token() / api.require_ops_access() ──────────────────────
# Operational endpoints (health, reports) are gated on a static service
# token, not any tenant's Clerk session — a regular tenant login must never
# be sufficient to pull cross-tenant operational data.

def test_verify_ops_token_accepts_the_configured_secret(monkeypatch):
    monkeypatch.setenv("OPS_API_TOKEN", "s3cret")
    assert auth.verify_ops_token("s3cret") is True


def test_verify_ops_token_rejects_a_wrong_token(monkeypatch):
    monkeypatch.setenv("OPS_API_TOKEN", "s3cret")
    assert auth.verify_ops_token("wrong") is False


def test_verify_ops_token_raises_when_not_configured(monkeypatch):
    monkeypatch.delenv("OPS_API_TOKEN", raising=False)
    with pytest.raises(auth.AuthNotConfigured):
        auth.verify_ops_token("anything")


def test_require_ops_access_401s_with_no_token():
    with pytest.raises(HTTPException) as exc:
        api.require_ops_access(creds=None)
    assert exc.value.status_code == 401


def test_require_ops_access_403s_with_the_wrong_token(monkeypatch):
    monkeypatch.setenv("OPS_API_TOKEN", "s3cret")
    with pytest.raises(HTTPException) as exc:
        api.require_ops_access(creds=_creds("wrong"))
    assert exc.value.status_code == 403


def test_require_ops_access_passes_with_the_correct_token(monkeypatch):
    monkeypatch.setenv("OPS_API_TOKEN", "s3cret")
    assert api.require_ops_access(creds=_creds("s3cret")) is None


def test_a_regular_clerk_session_does_not_satisfy_require_ops_access(monkeypatch):
    # A valid Clerk token isn't even checked against Clerk here — it's just
    # some string that isn't the ops secret, and must fail the same way any
    # other wrong token would (403, not treated as a valid tenant session).
    monkeypatch.setenv("OPS_API_TOKEN", "s3cret")
    with pytest.raises(HTTPException) as exc:
        api.require_ops_access(creds=_creds("a-real-looking.clerk.jwt"))
    assert exc.value.status_code == 403


# Post-CR-007: the real Clerk org roster for the colleague picker.

class _FakeResponse:
    def __init__(self, json_data, status=200):
        self._json = json_data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._json


def test_list_organization_members_returns_empty_when_secret_key_unset(monkeypatch):
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    assert auth.list_organization_members("org_1") == []


def test_list_organization_members_returns_empty_with_no_org_id(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")
    assert auth.list_organization_members(None) == []


def test_list_organization_members_parses_the_clerk_response(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured.update(url=url, headers=headers)
        return _FakeResponse({"data": [
            {"public_user_data": {"user_id": "user_a", "identifier": "a@example.com"}},
            {"public_user_data": {"user_id": "user_b", "identifier": "b@example.com"}},
        ]})

    monkeypatch.setattr(auth.requests, "get", fake_get)
    members = auth.list_organization_members("org_1")
    assert members == [
        {"email": "a@example.com", "clerk_user_id": "user_a"},
        {"email": "b@example.com", "clerk_user_id": "user_b"},
    ]
    assert "org_1" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer sk_test_x"


def test_list_organization_members_skips_entries_without_an_email(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(auth.requests, "get", lambda *a, **k: _FakeResponse(
        {"data": [{"public_user_data": {"user_id": "user_a"}}]}))
    assert auth.list_organization_members("org_1") == []


def test_list_organization_members_returns_empty_on_request_failure(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")

    def raise_error(*a, **k):
        raise requests.RequestException("network error")

    monkeypatch.setattr(auth.requests, "get", raise_error)
    assert auth.list_organization_members("org_1") == []


def test_get_org_members_endpoint_excludes_the_caller(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(auth, "list_organization_members", lambda org_id: [
        {"email": "me@example.com", "clerk_user_id": "user_me"},
        {"email": "colleague@example.com", "clerk_user_id": "user_colleague"},
    ])
    identity = api.Identity(tenant_id=1, account_name="me@example.com",
                             clerk_user_id="user_me", org_id="org_1")
    result = api.get_org_members(identity=identity)
    assert result == [{"email": "colleague@example.com", "clerk_user_id": "user_colleague"}]
