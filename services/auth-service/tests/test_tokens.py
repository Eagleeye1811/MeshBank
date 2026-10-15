"""RS256 issuance and verification: signatures, key IDs, algorithm pinning and claims."""

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.hazmat.primitives import serialization

from common.keys import SigningKey
from tests.conftest import PASSWORD


def _me(client, token):
    return client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})


def _claims(settings, user_id, **overrides):
    now = datetime.now(UTC)
    claims = {
        "sub": user_id,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "jti": "x",
        "typ": "access",
    }
    claims.update(overrides)
    return claims


def test_login_issues_rs256_token_with_kid(client, register, settings, jwt_keys):
    user_id = register().json()["id"]
    body = client.post(
        "/auth/login", json={"email": "asha@example.com", "password": PASSWORD}
    ).json()
    header = jwt.get_unverified_header(body["access_token"])
    assert header == {"alg": "RS256", "kid": "test-key-1", "typ": "JWT"}
    claims = jwt.decode(
        body["access_token"],
        jwt_keys.signing_key.private_key.public_key(),
        algorithms=["RS256"],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
    )
    assert claims["sub"] == user_id and claims["typ"] == "access"
    assert body["expires_in"] == 600


def test_valid_token_accepted(client, register, settings, jwt_keys):
    user_id = register().json()["id"]
    assert _me(client, jwt_keys.token(settings, user_id)).status_code == 200


def test_tampered_or_garbage_token_rejected(client, token):
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    for bad in ("garbage", tampered, ""):
        response = _me(client, bad)
        assert response.status_code == 401
        assert response.json()["error"]["code"] in ("INVALID_TOKEN", "AUTHENTICATION_REQUIRED")


def test_signature_from_untrusted_key_with_same_kid_rejected(client, register, settings, jwt_keys):
    user_id = register().json()["id"]
    forged = jwt_keys.token(settings, user_id, key=jwt_keys.untrusted_key)
    response = _me(client, forged)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_TOKEN"


def test_unknown_or_missing_key_id_rejected(client, register, settings, jwt_keys):
    user_id = register().json()["id"]
    unknown = jwt_keys.token(
        settings, user_id, key=SigningKey("rotated-away", jwt_keys.signing_key.private_key)
    )
    assert _me(client, unknown).status_code == 401
    no_kid = jwt.encode(
        _claims(settings, user_id), jwt_keys.signing_key.private_key, algorithm="RS256"
    )
    assert _me(client, no_kid).status_code == 401


def test_algorithm_cannot_be_chosen_by_the_token(client, register, settings, jwt_keys):
    user_id = register().json()["id"]
    public_pem = jwt_keys.signing_key.private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    # Classic key-confusion attack: HS256 "signed" with the public key as the secret.
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "kid": "test-key-1", "typ": "JWT"}).encode()
    ).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps(_claims(settings, user_id)).encode()).rstrip(b"=")
    import hashlib
    import hmac

    signature = base64.urlsafe_b64encode(
        hmac.new(public_pem, header + b"." + payload, hashlib.sha256).digest()
    ).rstrip(b"=")
    confused = b".".join([header, payload, signature]).decode()
    assert _me(client, confused).status_code == 401
    # alg=none
    none_header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "kid": "test-key-1"}).encode()
    ).rstrip(b"=")
    assert _me(client, f"{none_header.decode()}.{payload.decode()}.").status_code == 401


def test_wrong_issuer_audience_or_type_rejected(client, register, settings, jwt_keys):
    user_id = register().json()["id"]
    for override in ({"iss": "evil"}, {"aud": "other-api"}, {"typ": "refresh"}):
        token = jwt.encode(
            _claims(settings, user_id, **override),
            jwt_keys.signing_key.private_key,
            algorithm="RS256",
            headers={"kid": "test-key-1"},
        )
        assert _me(client, token).status_code == 401, override


def test_expired_and_not_yet_valid_tokens_rejected(client, register, settings, jwt_keys):
    user_id = register().json()["id"]
    expired = jwt_keys.token(
        settings, user_id, ttl_seconds=60, now=datetime.now(UTC) - timedelta(hours=1)
    )
    response = _me(client, expired)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"
    future = jwt_keys.token(settings, user_id, now=datetime.now(UTC) + timedelta(hours=1))
    assert _me(client, future).status_code == 401


def test_rotation_old_and_new_keys_both_verify(tmp_path, register, client, settings, jwt_keys):
    """With two public keys configured, tokens from either key ID verify;
    removing a public key invalidates tokens signed with it."""
    import shutil

    from common.keys import generate_key_pair, load_keyring
    from common.security import decode_access_token

    user_id = register().json()["id"]
    keys_dir = tmp_path / "keys"
    new_key = generate_key_pair(keys_dir, "test-key-2", bits=2048)
    public = tmp_path / "public"
    public.mkdir()
    shutil.copy(jwt_keys.public_dir + "/test-key-1.pem", public / "test-key-1.pem")
    shutil.copy(keys_dir / "public" / "test-key-2.pem", public / "test-key-2.pem")
    rotated = settings.model_copy()
    rotated._keyring = load_keyring(public)
    old_token = jwt_keys.token(settings, user_id)
    new_token = jwt_keys.token(settings, user_id, key=new_key)
    assert decode_access_token(rotated, old_token).customer_id == user_id
    assert decode_access_token(rotated, new_token).customer_id == user_id
    (public / "test-key-1.pem").unlink()
    rotated._keyring = load_keyring(public)
    import pytest

    from common.errors import UnauthorizedError

    with pytest.raises(UnauthorizedError):
        decode_access_token(rotated, old_token)


def test_jwks_endpoint_publishes_only_public_keys(client):
    body = client.get("/.well-known/jwks.json").json()
    assert [k["kid"] for k in body["keys"]] == ["test-key-1"]
    key = body["keys"][0]
    assert key["kty"] == "RSA" and key["alg"] == "RS256" and key["use"] == "sig"
    assert "d" not in key and "p" not in key  # no private components
