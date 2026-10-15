import os
import stat

import pytest

from app.config import Settings
from app.passwords import PasswordService, password_policy_violations
from common.client_ip import parse_cidrs, resolve_client_ip
from common.keys import KeyConfigError, generate_key_pair, load_keyring, load_signing_key

STRONG_DB = "postgresql://auth_svc:Zq8v1L0p9WmT4r7XyBn2@db/meshbank_auth"


@pytest.fixture(scope="module")
def passwords():
    return PasswordService(time_cost=1, memory_cost_kib=1024, parallelism=1)


def test_hash_and_verify(passwords):
    hashed = passwords.hash("CorrectHorse42battery")
    assert hashed.startswith("$argon2id$")
    assert passwords.verify(hashed, "CorrectHorse42battery")
    assert not passwords.verify(hashed, "WrongHorse42battery")


def test_hashes_are_salted(passwords):
    assert passwords.hash("CorrectHorse42battery") != passwords.hash("CorrectHorse42battery")


def test_verify_without_hash_is_false(passwords):
    assert not passwords.verify(None, "meshbank-dummy-password-for-timing")
    assert not passwords.verify("not-a-hash", "anything")


@pytest.mark.parametrize(
    ("password", "ok"),
    [
        ("CorrectHorse42battery", True),
        ("short1A", False),
        ("nodigitsatallhere", False),
        ("123456789012345", False),
        (" leadingspace123", False),
    ],
)
def test_password_policy(password, ok):
    assert (password_policy_violations(password) == []) is ok


def _prod(**overrides):
    values = {
        "_env_file": None,
        "app_env": "production",
        "internal_api_token": "t" * 48,
        "database_url": STRONG_DB,
        "db_migration_mode": "check",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_accepts_strong_configuration(jwt_keys):
    settings = _prod()
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert not settings.uses_development_secrets()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"internal_api_token": None}, "INTERNAL_API_TOKEN"),
        ({"database_url": "sqlite:///./x.db"}, "PostgreSQL"),
        ({"database_url": "postgresql://u:postgres@db/x"}, "strong"),
        ({"db_migration_mode": "create_all"}, "create_all"),
        ({"jwt_public_keys_dir": "/nonexistent/keys"}, "does not exist"),
        ({"jwt_private_key_file": "/nonexistent/key.pem"}, "does not exist"),
        ({"jwt_signing_key_id": "test-key-9"}, "missing from JWT_PUBLIC_KEYS_DIR"),
    ],
)
def test_production_refuses_unsafe_configuration(overrides, message):
    with pytest.raises(ValueError, match=message):
        _prod(**overrides)


def test_missing_keys_refused_in_every_environment(monkeypatch):
    monkeypatch.delenv("JWT_PUBLIC_KEYS_DIR")
    with pytest.raises(ValueError, match="make dev-keys"):
        Settings(_env_file=None, app_env="development")


def test_production_refuses_dev_keys_and_world_readable_private_key(tmp_path):
    dev = generate_key_pair(tmp_path / "dev", "dev-local", bits=2048)
    with pytest.raises(ValueError, match="development JWT keys"):
        _prod(
            jwt_public_keys_dir=str(tmp_path / "dev" / "public"),
            jwt_private_key_file=str(tmp_path / "dev" / "private" / "dev-local.pem"),
            jwt_signing_key_id=dev.kid,
        )
    generate_key_pair(tmp_path / "prod", "prod-2026-10", bits=2048)
    private = tmp_path / "prod" / "private" / "prod-2026-10.pem"
    os.chmod(private, 0o644)
    with pytest.raises(ValueError, match="world-readable"):
        _prod(
            jwt_public_keys_dir=str(tmp_path / "prod" / "public"),
            jwt_private_key_file=str(private),
            jwt_signing_key_id="prod-2026-10",
        )


def test_private_and_public_key_must_match(tmp_path, jwt_keys):
    generate_key_pair(tmp_path, "test-key-1", bits=2048)  # same kid, different key
    with pytest.raises(ValueError, match="does not match"):
        Settings(
            _env_file=None,
            app_env="test",
            jwt_private_key_file=str(tmp_path / "private" / "test-key-1.pem"),
        )


def test_key_generation_permissions_and_no_overwrite(tmp_path):
    generate_key_pair(tmp_path, "k1", bits=2048)
    private_mode = stat.S_IMODE((tmp_path / "private" / "k1.pem").stat().st_mode)
    assert private_mode == 0o600
    with pytest.raises(KeyConfigError, match="refusing to overwrite"):
        generate_key_pair(tmp_path, "k1", bits=2048)


def test_weak_or_invalid_keys_rejected(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    (tmp_path / "pub").mkdir()
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)  # noqa: S505
    (tmp_path / "pub" / "weak.pem").write_bytes(
        weak.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    with pytest.raises(KeyConfigError, match="at least 2048"):
        load_keyring(tmp_path / "pub")
    (tmp_path / "pub" / "weak.pem").write_text("not a key")
    with pytest.raises(KeyConfigError) as exc:
        load_keyring(tmp_path / "pub")
    assert "not a key" not in str(exc.value)  # contents never echoed
    with pytest.raises(KeyConfigError):
        load_signing_key(tmp_path / "pub" / "weak.pem", "weak", strict_permissions=False)


@pytest.mark.parametrize(
    ("peer", "xff", "trusted", "expected"),
    [
        ("203.0.113.9", "1.2.3.4", "", "203.0.113.9"),  # no trusted proxies: ignore header
        ("203.0.113.9", "1.2.3.4", "10.0.0.0/8", "203.0.113.9"),  # peer is not a proxy
        ("10.0.0.5", "198.51.100.7", "10.0.0.0/8", "198.51.100.7"),
        ("10.0.0.5", "6.6.6.6, 198.51.100.7, 10.0.0.9", "10.0.0.0/8", "198.51.100.7"),
        ("10.0.0.5", "garbage, 198.51.100.7", "10.0.0.0/8", "198.51.100.7"),
        ("10.0.0.5", "198.51.100.7, not-an-ip", "10.0.0.0/8", "10.0.0.5"),
        ("10.0.0.5", None, "10.0.0.0/8", "10.0.0.5"),
    ],
)
def test_client_ip_resolution(peer, xff, trusted, expected):
    assert resolve_client_ip(peer, xff, parse_cidrs(trusted)) == expected
