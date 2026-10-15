"""RSA key material for RS256 JWTs.

* Verifiers load **public** keys from a directory: one ``<kid>.pem`` per key.
  Several keys may be present at once, which is how rotation works.
* Only the auth service loads the **private** signing key.
* Key contents are never logged or included in error messages; errors only
  name the file.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

MIN_RSA_BITS = 2048
KID_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
DEV_KID_PREFIX = "dev-"


class KeyConfigError(ValueError):
    pass


@dataclass(frozen=True)
class KeyRing:
    """Public verification keys indexed by key ID."""

    keys: dict[str, rsa.RSAPublicKey]

    def get(self, kid: str) -> rsa.RSAPublicKey | None:
        return self.keys.get(kid)

    @property
    def kids(self) -> list[str]:
        return sorted(self.keys)


@dataclass(frozen=True)
class SigningKey:
    kid: str
    private_key: rsa.RSAPrivateKey


def load_keyring(directory: str | os.PathLike) -> KeyRing:
    path = Path(directory)
    if not path.is_dir():
        raise KeyConfigError(f"JWT public key directory {path} does not exist")
    keys: dict[str, rsa.RSAPublicKey] = {}
    for file in sorted(path.glob("*.pem")):
        kid = file.stem
        if not KID_PATTERN.match(kid):
            raise KeyConfigError(f"invalid key ID in file name {file.name}")
        try:
            key = serialization.load_pem_public_key(file.read_bytes())
        except (ValueError, TypeError) as exc:
            raise KeyConfigError(f"{file.name} is not a PEM public key") from exc
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size < MIN_RSA_BITS:
            raise KeyConfigError(f"{file.name} must be an RSA key of at least {MIN_RSA_BITS} bits")
        keys[kid] = key
    if not keys:
        raise KeyConfigError(f"no *.pem public keys found in {path}")
    return KeyRing(keys)


def load_signing_key(file: str | os.PathLike, kid: str, *, strict_permissions: bool) -> SigningKey:
    path = Path(file)
    if not KID_PATTERN.match(kid):
        raise KeyConfigError("JWT_SIGNING_KEY_ID is not a valid key ID")
    if not path.is_file():
        raise KeyConfigError(f"JWT private key file {path} does not exist")
    if strict_permissions and path.stat().st_mode & stat.S_IROTH:
        raise KeyConfigError(f"JWT private key file {path} must not be world-readable")
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except PermissionError as exc:
        raise KeyConfigError(f"cannot read JWT private key file {path}: permission denied") from exc
    except (ValueError, TypeError) as exc:
        raise KeyConfigError(f"{path.name} is not an unencrypted PEM private key") from exc
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < MIN_RSA_BITS:
        raise KeyConfigError(f"{path.name} must be an RSA key of at least {MIN_RSA_BITS} bits")
    return SigningKey(kid, key)


def generate_key_pair(directory: str | os.PathLike, kid: str, bits: int = 3072) -> SigningKey:
    """Write ``private/<kid>.pem`` (0600, directory 0700) and ``public/<kid>.pem`` (0644)."""
    if not KID_PATTERN.match(kid):
        raise KeyConfigError("invalid key ID")
    root = Path(directory)
    (root / "private").mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / "private").chmod(0o700)  # also tightens a pre-existing directory
    (root / "public").mkdir(parents=True, exist_ok=True)
    private_file, public_file = root / "private" / f"{kid}.pem", root / "public" / f"{kid}.pem"
    if private_file.exists() or public_file.exists():
        raise KeyConfigError(f"key {kid} already exists; refusing to overwrite it")
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(private_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    public_file.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    public_file.chmod(0o644)
    return SigningKey(kid, key)


def same_key(private_key: rsa.RSAPrivateKey, public_key: rsa.RSAPublicKey) -> bool:
    return private_key.public_key().public_numbers() == public_key.public_numbers()


def main(argv: list[str] | None = None) -> int:
    """``python -m common.keys --dir secrets/jwt --kid dev-2026-10`` generates a pair."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate an RS256 key pair for MeshBank")
    parser.add_argument("--dir", required=True)
    parser.add_argument("--kid", required=True)
    parser.add_argument("--bits", type=int, default=3072)
    args = parser.parse_args(argv)
    generate_key_pair(args.dir, args.kid, args.bits)
    # Only paths are printed, never key material.
    print(f"wrote {args.dir}/private/{args.kid}.pem (0600) and {args.dir}/public/{args.kid}.pem")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
