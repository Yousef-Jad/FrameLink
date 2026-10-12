from datetime import datetime, timedelta, timezone
from pathlib import Path
import ipaddress
import os
import ssl
import uuid
import plistlib
import socket
from urllib.parse import urlsplit
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
        self.ca_path = self.root / "FrameLink-Root-CA.crt"
        legacy = self.root / "LumaCapture-Root-CA.crt"
        if not self.ca_path.exists() and legacy.exists():
            _private_write(self.ca_path, legacy.read_bytes())
        key_path = self.root / "ca-key.pem"
        now = datetime.now(timezone.utc)
        if self.ca_path.exists() and key_path.exists():
            self.ca = x509.load_pem_x509_certificate(self.ca_path.read_bytes())
            self.key = serialization.load_pem_private_key(key_path.read_bytes(), None)
            if self.key.public_key().public_numbers() != self.ca.public_key().public_numbers():
                raise RuntimeError(
                    "CA certificate and private key do not match; restore this desktop's identity"
                )
            self.ca.verify_directly_issued_by(self.ca)
            if self.ca.not_valid_before_utc > now:
                raise RuntimeError("Local CA is not yet valid; check the desktop date and time")
            if not self.ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                raise RuntimeError("Stored root certificate is not a CA")
            if os.name != "nt":
                key_path.chmod(0o600)
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
                [x509.NameAttribute(NameOID.COMMON_NAME, "FrameLink Local CA " + uuid.uuid4().hex[:8])]
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

    def is_server_cert_valid(self, cert_path: Path, key_path: Path, address: str) -> bool:
        if not cert_path.exists() or not key_path.exists():
            return False
        try:
            certs = x509.load_pem_x509_certificates(cert_path.read_bytes())
            if not certs:
                return False
            leaf = certs[0]
            now = datetime.now(timezone.utc)
            if leaf.not_valid_after_utc < now + timedelta(days=2) or leaf.not_valid_before_utc > now:
                return False
            if leaf.issuer != self.ca.subject:
                return False
            leaf.verify_directly_issued_by(self.ca)
            key = serialization.load_pem_private_key(key_path.read_bytes(), None)
            if key.public_key().public_numbers() != leaf.public_key().public_numbers():
                return False
            if (
                ExtendedKeyUsageOID.SERVER_AUTH
                not in leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            ):
                return False
            if len(certs) != 2 or certs[1].fingerprint(hashes.SHA256()) != self.ca.fingerprint(
                hashes.SHA256()
            ):
                return False
            # Check SAN matching
            try:
                san_ext = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            except x509.ExtensionNotFound:
                return False

            ip_target = None
            try:
                ip_target = ipaddress.ip_address(address)
            except ValueError:
                pass

            matched = False
            for val in san_ext.value:
                if ip_target and isinstance(val, x509.IPAddress) and val.value == ip_target:
                    matched = True
                    break
                elif not ip_target and isinstance(val, x509.DNSName) and val.value == address:
                    matched = True
                    break
            return matched
        except Exception:
            return False

    def context(self, address):
        cert_path, key_path = self.root / "server.pem", self.root / "server-key.pem"
        if not self.is_server_cert_valid(cert_path, key_path, address):
            san_entries = []
            try:
                ip = ipaddress.ip_address(address)
                san_entries.append(x509.IPAddress(ip))
            except ValueError:
                san_entries.append(x509.DNSName(address))
            if address != "127.0.0.1":
                san_entries.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
            san_entries.append(x509.DNSName("localhost"))

            leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            now = datetime.now(timezone.utc)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"FrameLink Local Camera ({address})")])
            cert = (
                x509.CertificateBuilder()
                .subject_name(name)
                .issuer_name(self.ca.subject)
                .public_key(leaf_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(min(now + timedelta(days=90), self.ca.not_valid_after_utc))
                .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(
                    x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False
                )
                .add_extension(
                    x509.AuthorityKeyIdentifier.from_issuer_public_key(self.key.public_key()), critical=False
                )
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(
                    x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True
                )
                .sign(self.key, hashes.SHA256())
            )
            _private_write(
                cert_path,
                cert.public_bytes(serialization.Encoding.PEM)
                + self.ca.public_bytes(serialization.Encoding.PEM),
            )
            _private_write(key_path, _key_bytes(leaf_key))

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        if os.name != "nt":
            key_path.chmod(0o600)
        ctx.load_cert_chain(cert_path, key_path)
        return ctx

    def export_public(self, directory):
        """Export only public trust material, never the private identity directory."""
        directory = Path(directory).resolve()
        if directory == self.root.resolve() or self.root.resolve() in directory.parents:
            raise ValueError("Choose a separate transfer folder, outside the private certificate directory")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / self.ca_path.name).write_bytes(self.ca.public_bytes(serialization.Encoding.DER))
        (directory / "FrameLink.mobileconfig").write_bytes(self.ios_profile())
        (directory / "Fingerprint.txt").write_text("FrameLink root SHA-256\n" + self.fingerprint + "\n")

    def audit_endpoint(self, url):
        """Validate the certificate actually served at the QR address with strict trust."""
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Expected the HTTPS pairing URL")
        ctx = ssl.create_default_context(cafile=str(self.ca_path))
        ctx.verify_flags |= ssl.VERIFY_X509_STRICT
        with socket.create_connection((parsed.hostname, parsed.port or 443), timeout=5) as connection:
            with ctx.wrap_socket(connection, server_hostname=parsed.hostname) as tls:
                leaf = x509.load_der_x509_certificate(tls.getpeercert(binary_form=True))
                leaf.verify_directly_issued_by(self.ca)
                configured = x509.load_pem_x509_certificates((self.root / "server.pem").read_bytes())[0]
                if leaf.fingerprint(hashes.SHA256()) != configured.fingerprint(hashes.SHA256()):
                    raise ValueError(
                        "Served certificate differs from configured certificate; restart the listener"
                    )
                return {
                    "url": f"https://{parsed.netloc}",
                    "tls": tls.version(),
                    "root_sha256": self.fingerprint,
                    "leaf_sha256": leaf.fingerprint(hashes.SHA256()).hex(":"),
                    "san": [
                        str(value.value)
                        for value in leaf.extensions.get_extension_for_class(
                            x509.SubjectAlternativeName
                        ).value
                    ],
                    "server_auth": ExtendedKeyUsageOID.SERVER_AUTH
                    in leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value,
                    "valid_until": leaf.not_valid_after_utc.isoformat(),
                    "verified": True,
                    "phone_trust": "Not observable from the desktop; verify manually on iPhone",
                }

    def ios_profile(self):
        payload_id = str(uuid.uuid4()).upper()
        return plistlib.dumps(
            {
                "PayloadContent": [
                    {
                        "PayloadCertificateFileName": self.ca_path.name,
                        "PayloadContent": self.ca.public_bytes(serialization.Encoding.DER),
                        "PayloadDescription": "FrameLink Local Root CA for encrypted desktop camera streaming.",
                        "PayloadDisplayName": "FrameLink Local CA",
                        "PayloadIdentifier": "org.framelink.ca." + payload_id,
                        "PayloadType": "com.apple.security.root",
                        "PayloadUUID": payload_id,
                        "PayloadVersion": 1,
                    }
                ],
                "PayloadDescription": "Enables encrypted local camera streaming to FrameLink Studio.",
                "PayloadDisplayName": "FrameLink Local CA Trust Profile",
                "PayloadIdentifier": "org.framelink.trust",
                "PayloadOrganization": "FrameLink Studio",
                "PayloadRemovalDisallowed": False,
                "PayloadType": "Configuration",
                "PayloadUUID": str(uuid.uuid4()).upper(),
                "PayloadVersion": 1,
            }
        )
