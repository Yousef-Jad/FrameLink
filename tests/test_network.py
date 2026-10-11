import asyncio
import ssl
import time
import av
import numpy as np
import pytest
from aiohttp import ClientSession, web
from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription, VideoStreamTrack
from cryptography import x509
from lumacapture.media.buffers import Sources
from lumacapture.security.certificates import Certificates
from lumacapture.networking.server import PhoneServer


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
            assert srv.sources.phone.latest().rgb.shape == (180, 320, 3)
            assert peer.connectionState == "connected"
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
