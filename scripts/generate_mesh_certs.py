#!/usr/bin/env python3
"""MeshBank service mesh certificates (mTLS workload identities).

Creates, under k8s/certs/ (git-ignored):
1. The mesh root CA.
2. One workload certificate per service in ``mesh_topology.WORKLOADS``. The
   subject CN is the service's cluster DNS name, which the sidecars authorize
   on; the SPIFFE ID is carried as a URI SAN.
3. A rogue CA and an attacker certificate, used by k8s/test-mesh-mtls.py to
   prove that untrusted certificates are rejected.
4. k8s/mesh-certs.yaml (git-ignored): one Kubernetes Secret per workload.

Existing material is reused, so re-running a deploy does not rotate the CA
under running pods. A workload certificate is reissued only when it is missing,
not signed by the current CA, or expires within 14 days. ``--rotate`` replaces
everything (then restart the meshed deployments; deploy.sh does this).
"""

from __future__ import annotations

import argparse
import base64
import datetime
import ipaddress
from collections.abc import Callable
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from mesh_topology import NAMESPACE, WORKLOADS, Workload

ROOT_DIR = Path(__file__).resolve().parents[1]
CERTS_DIR = ROOT_DIR / "k8s" / "certs"
MANIFEST_PATH = ROOT_DIR / "k8s" / "mesh-certs.yaml"
RENEW_BEFORE = datetime.timedelta(days=14)


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def key_pem(key: rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def cert_pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def write_private(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def create_ca(
    common_name: str, org: str = "MeshBank"
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = generate_key()
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Mesh Security"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now() - datetime.timedelta(minutes=5))
        .not_valid_after(now() + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def create_workload_cert(
    common_name: str,
    spiffe_id: str,
    dns_names: list[str],
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = generate_key()
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MeshBank"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Mesh Workload"),
        ]
    )
    sans: list[x509.GeneralName] = [x509.DNSName(d) for d in dns_names]
    sans.append(x509.UniformResourceIdentifier(spiffe_id))
    sans.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now() - datetime.timedelta(minutes=5))
        .not_valid_after(now() + datetime.timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=True,
        )
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def load_pair(name: str) -> tuple[rsa.RSAPrivateKey, x509.Certificate] | None:
    crt, key = CERTS_DIR / f"{name}.crt", CERTS_DIR / f"{name}.key"
    if not (crt.exists() and key.exists()):
        return None
    private = serialization.load_pem_private_key(key.read_bytes(), password=None)
    if not isinstance(private, rsa.RSAPrivateKey):
        return None
    return private, x509.load_pem_x509_certificate(crt.read_bytes())


def still_valid(cert: x509.Certificate, ca_cert: x509.Certificate) -> bool:
    if cert.issuer != ca_cert.subject or cert.not_valid_after_utc - RENEW_BEFORE < now():
        return False
    ca_public = ca_cert.public_key()
    if not isinstance(ca_public, rsa.RSAPublicKey) or cert.signature_hash_algorithm is None:
        return False
    try:
        ca_public.verify(
            cert.signature,
            cert.tbs_certificate_bytes,
            padding.PKCS1v15(),
            cert.signature_hash_algorithm,
        )
    except InvalidSignature:
        return False
    return True


def ensure_pair(
    name: str,
    make: Callable[[], tuple[rsa.RSAPrivateKey, x509.Certificate]],
    rotate: bool,
    valid: Callable[[x509.Certificate], bool] = lambda _cert: True,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate, bool]:
    existing = None if rotate else load_pair(name)
    if existing and valid(existing[1]):
        print(f"  [kept]   {name}")
        return *existing, False
    key, cert = make()
    write_private(CERTS_DIR / f"{name}.key", key_pem(key))
    (CERTS_DIR / f"{name}.crt").write_text(cert_pem(cert), encoding="utf-8")
    print(f"  [issued] {name}")
    return key, cert, True


def workload_dns(w: Workload) -> list[str]:
    return [w.name, f"{w.name}.{NAMESPACE}", f"{w.name}.{NAMESPACE}.svc", w.fqdn, "localhost"]


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rotate", action="store_true", help="replace the CA and every cert")
    args = parser.parse_args()

    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    (CERTS_DIR / ".gitignore").write_text("*\n!.gitignore\n", encoding="utf-8")
    print(f"Mesh certificates in {CERTS_DIR}")

    ca_key, ca_cert, ca_new = ensure_pair(
        "ca",
        lambda: create_ca("MeshBank Mesh Root CA"),
        args.rotate,
        valid=lambda c: c.not_valid_after_utc - RENEW_BEFORE > now(),
    )
    ca_text = cert_pem(ca_cert)

    secrets = []
    for w in WORKLOADS.values():
        key, cert, _ = ensure_pair(
            w.name,
            lambda w=w: create_workload_cert(w.fqdn, w.spiffe_id, workload_dns(w), ca_key, ca_cert),
            args.rotate or ca_new,
            valid=lambda c: still_valid(c, ca_cert),
        )
        secrets.append(
            f"""---
apiVersion: v1
kind: Secret
metadata:
  name: {w.cert_secret}
  namespace: {NAMESPACE}
  labels:
    meshbank.io/workload: {w.name}
type: Opaque
data:
  tls.crt: {b64(cert_pem(cert))}
  tls.key: {b64(key_pem(key))}
  ca.crt: {b64(ca_text)}
"""
        )

    # Untrusted material for the negative tests.
    rogue_key, rogue_ca, _ = ensure_pair(
        "rogue-ca", lambda: create_ca("Untrusted Rogue Root CA", org="EvilCorp"), args.rotate
    )
    ensure_pair(
        "attacker",
        lambda: create_workload_cert(
            # Claims a trusted service's name, but is signed by the wrong CA.
            WORKLOADS["payment-service"].fqdn,
            "spiffe://evilcorp.internal/ns/attacker/sa/attacker-service",
            ["attacker", "localhost"],
            rogue_key,
            rogue_ca,
        ),
        args.rotate,
    )

    MANIFEST_PATH.write_text(
        "# Generated by scripts/generate_mesh_certs.py. Contains private keys: never commit.\n"
        + "".join(secrets),
        encoding="utf-8",
    )
    MANIFEST_PATH.chmod(0o600)
    print(f"  [OK] {MANIFEST_PATH.relative_to(ROOT_DIR)} ({len(secrets)} workload secrets)")
    if ca_new:
        print("  NOTE: new CA issued; restart meshed pods so every sidecar trusts it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
