import ipaddress
import plistlib

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID

from framelink.media.buffers import Sources
from framelink.networking.server import PhoneServer
from framelink.security.certificates import Certificates
from conftest import wait_until


def test_leaf_chain_ip_eku_and_public_transfer(tmp_path):
    identity = Certificates(tmp_path / "identity")
    identity.context("192.168.77.12")
    leaf, ca = x509.load_pem_x509_certificates((identity.root / "server.pem").read_bytes())
    leaf.verify_directly_issued_by(ca)
    assert ipaddress.ip_address("192.168.77.12") in leaf.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.IPAddress)
    assert (
        ExtendedKeyUsageOID.SERVER_AUTH
        in leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    )
    assert (leaf.not_valid_after_utc - leaf.not_valid_before_utc).days <= 90
    assert leaf.public_key().key_size >= 2048
    original = identity.fingerprint
    again = Certificates(identity.root)
    again.context("192.168.77.13")
    assert again.fingerprint == original
    assert again.is_server_cert_valid(
        identity.root / "server.pem", identity.root / "server-key.pem", "192.168.77.13"
    )
    assert not again.is_server_cert_valid(
        identity.root / "server.pem", identity.root / "server-key.pem", "192.168.77.12"
    )
    again.export_public(tmp_path / "transfer")
    assert {p.name for p in (tmp_path / "transfer").iterdir()} == {
        "FrameLink-Root-CA.crt",
        "FrameLink.mobileconfig",
        "Fingerprint.txt",
    }
    profile = plistlib.loads((tmp_path / "transfer/FrameLink.mobileconfig").read_bytes())
    root = x509.load_der_x509_certificate(profile["PayloadContent"][0]["PayloadContent"])
    assert root.fingerprint(hashes.SHA256()) == identity.ca.fingerprint(hashes.SHA256())
    with pytest.raises(ValueError, match="separate transfer folder"):
        identity.export_public(identity.root)
    assert Certificates(identity.root).fingerprint == original


def test_mismatched_leaf_key_is_repaired_without_changing_root(tmp_path):
    identity = Certificates(tmp_path)
    identity.context("127.0.0.1")
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (tmp_path / "server-key.pem").write_bytes(
        wrong.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    assert not identity.is_server_cert_valid(
        tmp_path / "server.pem", tmp_path / "server-key.pem", "127.0.0.1"
    )
    fingerprint = identity.fingerprint
    identity.context("127.0.0.1")
    assert identity.fingerprint == fingerprint
    assert identity.is_server_cert_valid(tmp_path / "server.pem", tmp_path / "server-key.pem", "127.0.0.1")


@pytest.mark.integration
def test_actual_qr_endpoint_strict_certificate_validation(tmp_path):
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    identity = Certificates(tmp_path)
    server = PhoneServer(Sources(), identity)
    server.start("127.0.0.1", port)
    try:
        wait_until(lambda: server._ready.is_set())
        assert not server.error
        result = identity.audit_endpoint(server.pairing_url())
        assert result["verified"] and result["server_auth"]
        assert "127.0.0.1" in result["san"]
        # Regenerate a leaf on disk while listener retains its old TLS context.
        identity.context("192.168.77.22")
        with pytest.raises(ValueError, match="Served certificate differs"):
            identity.audit_endpoint(server.origin)
    finally:
        server.stop()
        server.thread.join(10)


def test_certificate_setup_displays_live_validation_result(tmp_path):
    import socket
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton
    from framelink.storage.library import Library
    from framelink.ui.window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(Library(tmp_path / "db"), tmp_path / "data", smoke=True)
    window.timer.stop()  # This isolated listener uses loopback, outside the LAN-interface selector.
    window.certificates = Certificates(tmp_path / "identity")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    window.server = PhoneServer(window.sources, window.certificates)
    window.server.start("127.0.0.1", port)
    wait_until(lambda: window.server._ready.is_set())
    observed = []
    polls = [0]
    timer = QTimer()

    def check_dialog():
        dialog = window.findChild(QDialog)
        if not dialog:
            return
        polls[0] += 1
        if polls[0] == 1:
            for control in dialog.findChildren(QPushButton):
                if control.text() == "Validate served certificate at QR address":
                    control.click()
        texts = [label.text() for label in dialog.findChildren(QLabel)]
        if any(
            text.startswith("Served certificate validated:")
            or text.startswith("Served certificate validation failed:")
            for text in texts
        ):
            observed.extend(texts)
            dialog.accept()
        elif polls[0] > 100:
            dialog.reject()

    timer.timeout.connect(check_dialog)
    timer.start(100)
    try:
        window.certificate_setup()
        assert any("SAN 127.0.0.1" in text for text in observed), observed
        assert any(window.certificates.fingerprint in text for text in observed)
    finally:
        timer.stop()
        window.server.stop()
        window.server.thread.join(10)
        window.compression.stop()
        window.compression.thread.join(10)
        window.close()
        app.processEvents()
