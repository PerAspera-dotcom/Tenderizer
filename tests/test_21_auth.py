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
