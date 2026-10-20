"""Test-only TLS material for the real-engine environment (docker-compose.test.yml).

Writes a throw-away CA and a server certificate for the MySQL and MariaDB test containers, so
the suite can exercise ``verify-ca`` / ``verify-full`` (and a wrong CA being refused) instead of
skipping certificate verification:

    python make_certs.py /certs    # ca.pem, server-cert.pem, server-key.pem

The server certificate names the Compose service hosts (``mysql``, ``mariadb``) plus
``localhost`` / ``127.0.0.1``. The key is world-readable on purpose: the database containers run
as their own user and the material protects nothing but disposable test databases. Existing
material that is still valid for a day is kept (the run is idempotent)."""

from __future__ import annotations

import ipaddress
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

HOSTS = ("mysql", "mariadb", "localhost")
IPS = ("127.0.0.1",)


def _still_valid(path: Path) -> bool:
    try:
        cert = x509.load_pem_x509_certificate(path.read_bytes())
    except (OSError, ValueError):
        return False
    return cert.not_valid_after_utc > datetime.now(UTC) + timedelta(days=1)


def make(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    ca_file, cert_file, key_file = out / "ca.pem", out / "server-cert.pem", out / "server-key.pem"
    if key_file.exists() and _still_valid(ca_file) and _still_valid(cert_file):
        print(f"test TLS material in {out} is current")
        return
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "DBManager integration-test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    san = [x509.DNSName(h) for h in HOSTS] + [x509.IPAddress(ipaddress.ip_address(i)) for i in IPS]
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTS[0])]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=29))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
        )
    )
    for f in (ca_file, cert_file, key_file):
        f.chmod(0o644)
    print(f"wrote test TLS material to {out}")


if __name__ == "__main__":
    make(Path(sys.argv[1] if len(sys.argv) > 1 else "/certs"))
