#!/usr/bin/env python3
"""MeshBank Service Mesh Certificate and Workload Identity (mTLS) Generator.

Generates:
1. Mesh Root CA
2. Workload certificate for payment-service (with SPIFFE SAN URI)
3. Workload certificate for transaction-service (with SPIFFE SAN URI)
4. Rogue CA and attacker client certificate for mTLS rejection tests
5. Kubernetes secret manifest k8s/mesh-certs.yaml for zero-friction cluster deployment
"""

from __future__ import annotations

import datetime
from pathlib import Path
import ipaddress
import base64

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CERTS_DIR = Path(__file__).resolve().parents[1] / "k8s" / "certs"


def generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )


def serialize_key(key: rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def serialize_cert(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")


def create_ca(common_name: str, org: str = "MeshBank") -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = generate_key()
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Mesh Security"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
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
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256(), default_backend())
    )
    return key, cert


def create_workload_cert(
    service_name: str,
    spiffe_uri: str,
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    dns_names: list[str],
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = generate_key()
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"{service_name}.meshbank.svc.cluster.local"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MeshBank"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Mesh Workload"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)

    san_items: list[x509.GeneralName] = [x509.DNSName(dns) for dns in dns_names]
    san_items.append(x509.UniformResourceIdentifier(spiffe_uri))
    san_items.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=90))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
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
            x509.ExtendedKeyUsage([
                x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
                x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH,
            ]),
            critical=True,
        )
        .add_extension(
            x509.SubjectAlternativeName(san_items),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256(), default_backend())
    )
    return key, cert


def b64(data: str) -> str:
    return base64.b64encode(data.encode("utf-8")).decode("utf-8")


def main() -> None:
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Generating mesh certificates in {CERTS_DIR}...")

    # 1. Mesh Root CA
    ca_key, ca_cert = create_ca("MeshBank Mesh Root CA")
    ca_key_pem = serialize_key(ca_key)
    ca_cert_pem = serialize_cert(ca_cert)
    (CERTS_DIR / "ca.crt").write_text(ca_cert_pem, encoding="utf-8")
    (CERTS_DIR / "ca.key").write_text(ca_key_pem, encoding="utf-8")
    print("  [OK] Mesh Root CA created (ca.crt, ca.key)")

    # 2. Payment service workload cert
    pay_key, pay_cert = create_workload_cert(
        service_name="payment-service",
        spiffe_uri="spiffe://meshbank.internal/ns/meshbank/sa/payment-service",
        ca_key=ca_key,
        ca_cert=ca_cert,
        dns_names=[
            "payment-service",
            "payment-service.meshbank",
            "payment-service.meshbank.svc",
            "payment-service.meshbank.svc.cluster.local",
            "localhost",
        ],
    )
    pay_key_pem = serialize_key(pay_key)
    pay_cert_pem = serialize_cert(pay_cert)
    (CERTS_DIR / "payment-service.crt").write_text(pay_cert_pem, encoding="utf-8")
    (CERTS_DIR / "payment-service.key").write_text(pay_key_pem, encoding="utf-8")
    print("  [OK] payment-service workload certificate created with SPIFFE ID")

    # 3. Transaction service workload cert
    tx_key, tx_cert = create_workload_cert(
        service_name="transaction-service",
        spiffe_uri="spiffe://meshbank.internal/ns/meshbank/sa/transaction-service",
        ca_key=ca_key,
        ca_cert=ca_cert,
        dns_names=[
            "transaction-service",
            "transaction-service.meshbank",
            "transaction-service.meshbank.svc",
            "transaction-service.meshbank.svc.cluster.local",
            "localhost",
        ],
    )
    tx_key_pem = serialize_key(tx_key)
    tx_cert_pem = serialize_cert(tx_cert)
    (CERTS_DIR / "transaction-service.crt").write_text(tx_cert_pem, encoding="utf-8")
    (CERTS_DIR / "transaction-service.key").write_text(tx_key_pem, encoding="utf-8")
    print("  [OK] transaction-service workload certificate created with SPIFFE ID")

    # 4. Rogue / Untrusted CA & Attacker cert
    rogue_key, rogue_ca_cert = create_ca("Untrusted Rogue Root CA", org="EvilCorp")
    att_key, att_cert = create_workload_cert(
        service_name="attacker-service",
        spiffe_uri="spiffe://evilcorp.internal/ns/attacker/sa/attacker-service",
        ca_key=rogue_key,
        ca_cert=rogue_ca_cert,
        dns_names=["attacker", "localhost"],
    )
    (CERTS_DIR / "rogue-ca.crt").write_text(serialize_cert(rogue_ca_cert), encoding="utf-8")
    (CERTS_DIR / "attacker.crt").write_text(serialize_cert(att_cert), encoding="utf-8")
    (CERTS_DIR / "attacker.key").write_text(serialize_key(att_key), encoding="utf-8")
    print("  [OK] Untrusted attacker certificate created for rejection test")

    # 5. Generate Kubernetes secret manifest
    k8s_secret_manifest = f"""# Auto-generated by scripts/generate_mesh_certs.py
apiVersion: v1
kind: Secret
metadata:
  name: mesh-ca-cert
  namespace: meshbank
type: Opaque
data:
  ca.crt: {b64(ca_cert_pem)}
---
apiVersion: v1
kind: Secret
metadata:
  name: payment-mesh-certs
  namespace: meshbank
type: Opaque
data:
  tls.crt: {b64(pay_cert_pem)}
  tls.key: {b64(pay_key_pem)}
  ca.crt: {b64(ca_cert_pem)}
---
apiVersion: v1
kind: Secret
metadata:
  name: transaction-mesh-certs
  namespace: meshbank
type: Opaque
data:
  tls.crt: {b64(tx_cert_pem)}
  tls.key: {b64(tx_key_pem)}
  ca.crt: {b64(ca_cert_pem)}
"""
    manifest_path = Path(__file__).resolve().parents[1] / "k8s" / "mesh-certs.yaml"
    manifest_path.write_text(k8s_secret_manifest, encoding="utf-8")
    print(f"  [OK] Kubernetes mesh secrets written to {manifest_path}")

    # Ensure k8s/certs is ignored by git
    gitignore = CERTS_DIR / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n!.gitignore\n", encoding="utf-8")
        print("  [OK] Created .gitignore in certs directory to prevent committing private keys")

    print("\nCertificate generation completed successfully.")


if __name__ == "__main__":
    main()
