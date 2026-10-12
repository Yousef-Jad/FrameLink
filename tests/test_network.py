import asyncio
import ssl
import time
import av
import numpy as np
import pytest
import ipaddress
from aiohttp import ClientSession, web
from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription, VideoStreamTrack
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding
from framelink.media.buffers import Sources
from framelink.security.certificates import Certificates
from framelink.security.pairing import PairingTokens
from framelink.networking.server import PhoneServer


@pytest.fixture
async def server(tmp_path):
    certificates = Certificates(tmp_path / "certs")
    server = PhoneServer(Sources(), certificates)
    server.address = "127.0.0.1"
    runner = web.AppRunner(server.make_app(), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=certificates.context("127.0.0.1"))
    await site.start()
    server.port = site._server.sockets[0].getsockname()[1]
    context = ssl.create_default_context(cafile=str(certificates.ca_path))
    async with ClientSession() as client:
        yield server, client, context
    await server._close_peer()
    await runner.cleanup()


def test_certificate_identity_survives_restart(tmp_path):
    ca = Certificates(tmp_path)
    again = Certificates(tmp_path)
    assert ca.fingerprint == again.fingerprint
    ca.context("192.168.1.10")
    leaf = x509.load_pem_x509_certificate((tmp_path / "server.pem").read_bytes())
    assert (
        str(
            leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
                x509.IPAddress
            )[0]
        )
        == "192.168.1.10"
    )
    assert leaf.issuer == ca.ca.subject
    assert b"com.apple.security.root" in ca.ios_profile()


@pytest.mark.integration
async def test_https_pairing_validation(server):
    srv, client, context = server
    headers = {"Origin": srv.origin}
    async with client.get(srv.origin + "/", ssl=context) as response:
        assert response.status == 200 and "getUserMedia" not in await response.text()
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    async with client.post(
        srv.origin + "/pair", json={"token": "bad"}, headers=headers, ssl=context
    ) as response:
        assert response.status == 401
    token = srv.tokens.issue()
    async with client.post(
        srv.origin + "/pair", json={"token": token}, headers={"Origin": "https://evil.example"}, ssl=context
    ) as response:
        assert response.status == 403
    async with client.post(
        srv.origin + "/pair", json={"token": token}, headers=headers, ssl=context
    ) as response:
        assert response.status == 200
        session = (await response.json())["session"]
    async with client.post(
        srv.origin + "/pair", json={"token": token}, headers=headers, ssl=context
    ) as response:
        assert response.status == 401
    async with client.post(srv.origin + "/offer", json={}, headers=headers, ssl=context) as response:
        assert response.status == 401
    headers["X-Luma-Session"] = session
    async with client.post(
        srv.origin + "/offer", json={"type": "answer", "sdp": "bad"}, headers=headers, ssl=context
    ) as response:
        assert response.status == 400
    async with client.post(srv.origin + "/disconnect", json={}, headers=headers, ssl=context) as response:
        assert response.status == 200
    assert srv.session_secret is None


async def test_invalid_network_requests(server):
    srv, client, context = server
    headers = {"Origin": srv.origin}
    async with client.post(
        srv.origin + "/pair", data="{", headers=headers | {"Content-Type": "application/json"}, ssl=context
    ) as response:
        assert response.status == 400
    async with client.post(srv.origin + "/pair", json=["invalid"], headers=headers, ssl=context) as response:
        assert response.status == 401
    async with client.post(
        srv.origin + "/pair", json={"token": "x" * 100000}, headers=headers, ssl=context
    ) as response:
        assert response.status == 413
    async with client.get(srv.origin + "/server-key.pem", ssl=context) as response:
        assert response.status == 404
    async with client.get(srv.origin + "/", headers={"Host": "evil.example"}, ssl=context) as response:
        assert response.status == 403


async def test_waiting_disconnect_reauthenticates_after_session_changes(server):
    from types import SimpleNamespace

    srv, _, _ = server
    srv.session_secret = "old-session"
    srv.session_expiry = time.monotonic() + 60
    await srv._offer_lock.acquire()
    request = SimpleNamespace(headers={"X-Luma-Session": "old-session"})
    disconnect = asyncio.create_task(srv.disconnect_request(request))
    await asyncio.sleep(0)
    srv.session_secret = "new-session"
    token = srv.tokens.issue()
    srv._offer_lock.release()
    with pytest.raises(web.HTTPUnauthorized):
        await disconnect
    assert srv.session_secret == "new-session"
    assert srv.tokens.consume(token)


def test_network_diagnostics_never_include_pairing_credentials(tmp_path):
    srv = PhoneServer(Sources(), Certificates(tmp_path / "certs"))
    srv.address = "172.20.10.7"
    srv.state = "Phone failed"
    srv.session_secret = "private-session"
    report = srv.network_diagnostics()
    text = str(report)
    assert "private-session" not in text
    assert report["desktop_ip"] == "172.20.10.7"
    assert "ice" in report and "dtls" in report


def test_failed_peer_cleanup_preserves_valid_pairing_session(tmp_path):
    srv = PhoneServer(Sources(), Certificates(tmp_path / "certs"))
    srv.session_secret = "valid-session"
    srv.session_expiry = time.monotonic() + 60
    srv.state = "Phone failed"
    srv.clear_failed_peer()
    assert srv.session_secret == "valid-session"


def test_local_answer_uses_qr_interface_and_reports_ice_diagnostics(tmp_path):
    srv = PhoneServer(Sources(), Certificates(tmp_path / "certs"))
    srv.address = "172.20.10.7"
    answer = """v=0
a=candidate:1 1 UDP 2130706431 192.168.0.106 40000 typ host
a=candidate:2 1 UDP 2130706431 172.20.10.7 40001 typ host
"""
    filtered = srv.filter_answer_sdp(answer)
    assert "172.20.10.7" in filtered
    assert "192.168.0.106" not in filtered
    srv.ice_diagnostics["ice_connection_state"] = "failed"
    srv.ice_diagnostics["failure_reason"] = "ice connection failed"
    assert srv.network_diagnostics()["ice"]["ice_connection_state"] == "failed"


class FixtureCamera(VideoStreamTrack):
    """Synthetic transport fixture; this does not validate a physical phone camera."""

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        frame = av.VideoFrame.from_ndarray(np.full((180, 320, 3), pts % 255, np.uint8), format="rgb24")
        frame.pts, frame.time_base = pts, time_base
        return frame


@pytest.mark.integration
async def test_webrtc_receive_and_reconnect(server):
    srv, client, context = server
    async with client.post(
        srv.origin + "/pair", json={"token": srv.tokens.issue()}, headers={"Origin": srv.origin}, ssl=context
    ) as response:
        session = (await response.json())["session"]
    headers = {"Origin": srv.origin, "X-Luma-Session": session}
    for attempt in range(2):
        peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        peer.addTrack(FixtureCamera())
        peer.createDataChannel("luma-status")
        try:
            await peer.setLocalDescription(await peer.createOffer())
            async with client.post(
                srv.origin + "/offer",
                json={"type": "offer", "sdp": peer.localDescription.sdp},
                headers=headers,
                ssl=context,
            ) as response:
                assert response.status == 200, await response.text()
                answer = await response.json()
            await peer.setRemoteDescription(RTCSessionDescription(**answer))
            deadline = time.monotonic() + 12
            while srv.sources.phone.latest() is None and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert srv.sources.phone.latest() is not None
            assert srv.sources.phone_preview.latest() is not None
            assert srv.sources.phone.latest().rgb.shape == (180, 320, 3)
            assert peer.connectionState == "connected"
            assert srv.metrics["first_rtp_monotonic"] is not None
            assert srv.metrics["first_frame_decoded_monotonic"] is not None
            assert srv.state.startswith("Receiving video")
            srv.mark_preview_displayed(srv.sources.phone.latest().sequence)
            assert srv.metrics["first_preview_displayed_monotonic"] is not None
            assert srv.state.startswith("Preview ready")
        finally:
            await peer.close()
            await srv._close_peer()
        assert srv.sources.phone.latest() is None


async def test_certificate_download_is_explicit_and_time_limited(tmp_path):
    from aiohttp.test_utils import TestServer, TestClient

    srv = PhoneServer(Sources(), Certificates(tmp_path / "certs"))
    srv.address = "127.0.0.1"
    test_server = TestServer(srv.bootstrap_app())
    await test_server.start_server()
    srv.port = test_server.port - 1
    async with TestClient(test_server) as client:
        response = await client.get("/ca.crt")
        assert response.status == 403
        srv.enable_bootstrap()
        response = await client.get("/ca.crt")
        assert response.status == 200
        cert = x509.load_der_x509_certificate(await response.read())
        assert cert.subject == srv.certificates.ca.subject
        response = await client.get("/server-key.pem")
        assert response.status == 404
        srv.bootstrap_until = 0
        response = await client.get("/ios.mobileconfig")
        assert response.status == 403


@pytest.mark.integration
async def test_webrtc_mdns_candidate_unmasking_and_metrics(server):
    srv, client, context = server
    async with client.post(
        srv.origin + "/pair", json={"token": srv.tokens.issue()}, headers={"Origin": srv.origin}, ssl=context
    ) as response:
        session = (await response.json())["session"]
    # Test X-FrameLink-Session header
    headers = {"Origin": srv.origin, "X-FrameLink-Session": session}
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    peer.addTrack(FixtureCamera())
    peer.createDataChannel("framelink-status")
    try:
        offer = await peer.createOffer()
        await peer.setLocalDescription(offer)
        # Emulate browser mDNS candidate obfuscation: replace first IP candidate with .local hostname
        raw_sdp = peer.localDescription.sdp
        mdns_sdp = []
        replaced = False
        for line in raw_sdp.splitlines():
            if line.startswith("a=candidate:") and not replaced:
                fields = line.split()
                if len(fields) >= 8:
                    fields[4] = "masked-uuid-test.local"
                    line = " ".join(fields)
                    replaced = True
            mdns_sdp.append(line)
        simulated_offer_sdp = "\r\n".join(mdns_sdp) + "\r\n"

        async with client.post(
            srv.origin + "/offer",
            json={"type": "offer", "sdp": simulated_offer_sdp},
            headers=headers,
            ssl=context,
        ) as response:
            assert response.status == 200, await response.text()
            answer = await response.json()
        await peer.setRemoteDescription(RTCSessionDescription(**answer))
        deadline = time.monotonic() + 12
        while srv.sources.phone.latest() is None and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert srv.sources.phone.latest() is not None
        assert srv.metrics["frames_received"] > 0
        assert srv.metrics["frames_decoded"] > 0
        assert srv.metrics["width"] == 320
        assert srv.metrics["height"] == 180
        assert "receiving video" in srv.state.lower()
    finally:
        await peer.close()
        await srv._close_peer()


def test_leaf_certificate_reuse_and_regeneration(tmp_path):
    ca = Certificates(tmp_path)
    ca_orig_fingerprint = ca.fingerprint

    # Initial generation for 192.168.0.106
    ca.context("192.168.0.106")
    leaf1 = x509.load_pem_x509_certificate((tmp_path / "server.pem").read_bytes())
    serial1 = leaf1.serial_number

    # Reuse when same IP is requested
    ca.context("192.168.0.106")
    leaf2 = x509.load_pem_x509_certificate((tmp_path / "server.pem").read_bytes())
    assert leaf2.serial_number == serial1

    # Regenerate when IP changes
    ca.context("192.168.0.150")
    leaf3 = x509.load_pem_x509_certificate((tmp_path / "server.pem").read_bytes())
    assert leaf3.serial_number != serial1
    san = leaf3.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    ips = san.get_values_for_type(x509.IPAddress)
    assert ipaddress.IPv4Address("192.168.0.150") in ips
    assert ipaddress.IPv4Address("127.0.0.1") in ips

    # Root CA remains completely untouched
    assert ca.fingerprint == ca_orig_fingerprint


def test_apple_tls_certificate_specifications(tmp_path):
    ca = Certificates(tmp_path)
    ca.context("192.168.0.106")
    leaf = x509.load_pem_x509_certificate((tmp_path / "server.pem").read_bytes())

    # Validity period <= 398 days (Apple requirement)
    validity_days = (leaf.not_valid_after_utc - leaf.not_valid_before_utc).days
    assert validity_days <= 398
    assert validity_days >= 89

    # Extended Key Usage: serverAuth
    eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert x509.oid.ExtendedKeyUsageOID.SERVER_AUTH in eku

    # Key Usage: digitalSignature and keyEncipherment
    ku = leaf.extensions.get_extension_for_class(x509.KeyUsage).value
    assert ku.digital_signature is True
    assert ku.key_encipherment is True

    # Basic Constraints: leaf is not a CA
    bc = leaf.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca is False

    # CA constraints: ca=True with path_length=0
    ca_bc = ca.ca.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert ca_bc.ca is True
    assert ca_bc.path_length == 0

    # Subject Key Identifier & Authority Key Identifier present
    assert leaf.extensions.get_extension_for_class(x509.SubjectKeyIdentifier) is not None
    assert leaf.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier) is not None

    # Cryptographic signature validation against CA public key
    ca.ca.public_key().verify(
        leaf.signature,
        leaf.tbs_certificate_bytes,
        padding.PKCS1v15(),
        leaf.signature_hash_algorithm,
    )


def test_pairing_token_lifecycle_and_stability():
    tokens = PairingTokens(ttl=600)

    # Initial token creation
    t1 = tokens.get_or_create()
    assert len(t1) >= 16
    assert tokens.is_active()
    assert not tokens.is_expired()
    assert tokens.remaining_seconds() > 0

    # Idempotent repeated calls must not rotate or churn token
    t2 = tokens.get_or_create()
    assert t2 == t1
    assert tokens.session_id is not None

    # Explicit invalidation
    tokens.invalidate()
    assert tokens.is_expired()
    assert not tokens.is_active()

    # Creation after invalidation produces fresh token
    t3 = tokens.get_or_create()
    assert t3 != t1
    assert tokens.is_active()


@pytest.mark.integration
async def test_pairing_url_stability_and_explicit_rotation(server):
    srv, _, _ = server
    srv.state = "Waiting"
    url1 = srv.pairing_url()
    url2 = srv.pairing_url()
    assert url1 == url2, "Pairing URL must remain stable across calls"

    url3 = srv.new_pairing_url()
    assert url3 != url1, "Explicit new_pairing_url must rotate token"


@pytest.mark.integration
async def test_https_verify_endpoint(server):
    srv, client, context = server
    async with client.get(srv.origin + "/verify", ssl=context) as response:
        assert response.status == 200
        text = await response.text()
        assert "cannot determine whether you bypassed" in text
        assert "Safari now trusts" not in text
        assert "Safari now trusts your desktop certificate" not in text
