from datetime import datetime, timedelta, timezone
from pathlib import Path
import ipaddress
import os
import ssl
import uuid
import plistlib
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID


def _private_write(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)
    if os.name != "nt":
        os.chmod(path, 0o600)


def _key_bytes(key):
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


class Certificates:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.root.chmod(0o700)
        self.ca_path = self.root / "LumaCapture-Root-CA.crt"
        key_path = self.root / "ca-key.pem"
        now = datetime.now(timezone.utc)
        if self.ca_path.exists() and key_path.exists():
            self.ca = x509.load_pem_x509_certificate(self.ca_path.read_bytes())
            self.key = serialization.load_pem_private_key(key_path.read_bytes(), None)
            if self.ca.not_valid_after_utc < now + timedelta(days=2):
                raise RuntimeError(
                    "Local CA has expired. Remove its trust on the phone before replacing the certificate directory."
                )
        elif self.ca_path.exists() or key_path.exists():
            raise RuntimeError(
                "Incomplete certificate identity; restore both CA files or reset trust explicitly"
            )
        else:
            self.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
            name = x509.Name(
                [x509.NameAttribute(NameOID.COMMON_NAME, "LumaCapture Local CA " + uuid.uuid4().hex[:8])]
            )
            self.ca = (
                x509.CertificateBuilder()
                .subject_name(name)
                .issuer_name(name)
                .public_key(self.key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=1825))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(
                    x509.SubjectKeyIdentifier.from_public_key(self.key.public_key()), critical=False
                )
                .add_extension(
                    x509.AuthorityKeyIdentifier.from_issuer_public_key(self.key.public_key()), critical=False
                )
                .add_extension(
                    x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True
                )
                .sign(self.key, hashes.SHA256())
            )
            _private_write(key_path, _key_bytes(self.key))
            _private_write(self.ca_path, self.ca.public_bytes(serialization.Encoding.PEM))
        self.fingerprint = self.ca.fingerprint(hashes.SHA256()).hex(":").upper()

    def context(self, address):
        ip = ipaddress.ip_address(address)
        leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(timezone.utc)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LumaCapture Local Camera")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(self.ca.subject)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(min(now + timedelta(days=90), self.ca.not_valid_after_utc))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self.key.public_key()), critical=False
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(
                x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True
            )
            .sign(self.key, hashes.SHA256())
        )
        cert_path, key_path = self.root / "server.pem", self.root / "server-key.pem"
        _private_write(
            cert_path,
            cert.public_bytes(serialization.Encoding.PEM) + self.ca.public_bytes(serialization.Encoding.PEM),
        )
        _private_write(key_path, _key_bytes(leaf_key))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert_path, key_path)
        return ctx

    def ios_profile(self):
        payload_id = str(uuid.uuid4()).upper()
        return plistlib.dumps(
            {
                "PayloadContent": [
                    {
                        "PayloadCertificateFileName": self.ca_path.name,
                        "PayloadContent": self.ca.public_bytes(serialization.Encoding.DER),
                        "PayloadDescription": "Trust only after comparing the certificate fingerprint with your desktop.",
                        "PayloadDisplayName": "LumaCapture Local Camera CA",
                        "PayloadIdentifier": "org.lumacapture.ca." + payload_id,
                        "PayloadType": "com.apple.security.root",
                        "PayloadUUID": payload_id,
                        "PayloadVersion": 1,
                    }
                ],
                "PayloadDisplayName": "LumaCapture Local Camera",
                "PayloadIdentifier": "org.lumacapture.trust",
                "PayloadRemovalDisallowed": False,
                "PayloadType": "Configuration",
                "PayloadUUID": str(uuid.uuid4()).upper(),
                "PayloadVersion": 1,
            }
        )
