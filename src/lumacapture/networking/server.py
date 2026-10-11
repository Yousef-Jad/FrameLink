import asyncio
import contextlib
import hmac
import json
from pathlib import Path
import secrets
import threading
import time
import av
from aiohttp import web
from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError
from lumacapture.security.pairing import PairingTokens, RateLimiter
from lumacapture.networking.interfaces import local_address
from lumacapture.media.model import TimestampNormalizer


WEB_ROOT = Path(__file__).resolve().parent.parent / "web"


class PhoneServer:
    def __init__(self, sources, certificates, notify=lambda *args: None, recording_status=lambda: "Ready"):
        self.sources, self.certificates = sources, certificates
        self.notify, self.recording_status = notify, recording_status
        self.tokens = PairingTokens()
        self.limiter = RateLimiter()
        self.session_secret = None
        self.session_expiry = 0
        self.peer = None
        self.channel = None
        self.consumers = set()
        self.loop = None
        self.thread = None
        self.address = None
        self.port = 8443
        self.bootstrap_until = 0
        self.state = "Offline"
        self.error = ""
        self.bytes_received = 0
        self.bitrate = 0
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._offer_lock = asyncio.Lock()

    @property
    def origin(self):
        return f"https://{self.address}:{self.port}"

    def pairing_url(self):
        if self.state == "Offline":
            raise ValueError("Start the local server first")
        return self.origin + "/#" + self.tokens.issue()

    def start(self, address, port=8443):
        if not local_address(address, allow_loopback=True):
            raise ValueError("The camera server may bind only to a private LAN address")
        if self.thread and self.thread.is_alive():
            raise ValueError("Stop the existing server before changing network interfaces")
        self.address, self.port = address, port
        self._ready.clear()
        self._stop_requested.clear()
        self.error = ""
        self.thread = threading.Thread(target=self._thread_main, name="phone-server", daemon=False)
        self.thread.start()

    def _thread_main(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._start())
            self._ready.set()
            if not self._stop_requested.is_set():
                self.loop.run_forever()
        except Exception as exc:
            self.error = str(exc)
            self.notify("error", f"Phone server: {exc}")
        finally:
            self._ready.set()
            self.loop.run_until_complete(self._cleanup())
            self.loop.close()
            self.loop = None
            self.state = "Offline"

    async def _start(self):
        # Contended asyncio locks belong to their event loop. Restarting the
        # listener creates a new loop, so it must also create a new lock.
        self._offer_lock = asyncio.Lock()
        self.runner = web.AppRunner(self.make_app(), access_log=None)
        await self.runner.setup()
        await web.TCPSite(
            self.runner, self.address, self.port, ssl_context=self.certificates.context(self.address)
        ).start()
        self.bootstrap_runner = web.AppRunner(self.bootstrap_app(), access_log=None)
        await self.bootstrap_runner.setup()
        await web.TCPSite(self.bootstrap_runner, self.address, self.port + 1).start()
        self.state = "Waiting for phone"
        self.notify("server", self.origin)
        self.heartbeat = asyncio.create_task(self._heartbeat())

    def make_app(self):
        @web.middleware
        async def guard(request, handler):
            if not local_address(request.remote or "", allow_loopback=self.address in {"127.0.0.1", "::1"}):
                raise web.HTTPForbidden(text="Local network access only")
            if request.host != f"{self.address}:{self.port}":
                raise web.HTTPForbidden(text="Unexpected Host")
            if request.method not in {"GET", "POST"}:
                raise web.HTTPMethodNotAllowed(request.method, ["GET", "POST"])
            if request.method == "POST":
                if request.headers.get("Origin") != self.origin:
                    raise web.HTTPForbidden(text="Unexpected origin")
                if request.content_type != "application/json":
                    raise web.HTTPUnsupportedMediaType(text="JSON is required")
                if not self.limiter.allow(request.remote):
                    raise web.HTTPTooManyRequests(text="Too many requests. Wait one minute.")
            try:
                response = await handler(request)
            except (json.JSONDecodeError, UnicodeError, ValueError, TypeError, KeyError) as exc:
                raise web.HTTPBadRequest(text="Invalid request") from exc
            response.headers.update(
                {
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer",
                    "Permissions-Policy": "camera=(self), microphone=(self)",
                    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
                }
            )
            return response

        app = web.Application(client_max_size=64 * 1024, middlewares=[guard])
        app.router.add_get("/", self.index)
        app.router.add_get("/camera.js", self.javascript)
        app.router.add_get("/style.css", self.stylesheet)
        app.router.add_post("/pair", self.pair)
        app.router.add_post("/offer", self.offer)
        app.router.add_post("/disconnect", self.disconnect_request)
        return app

    async def index(self, request):
        return web.Response(text=(WEB_ROOT / "index.html").read_text(), content_type="text/html")

    async def javascript(self, request):
        return web.Response(text=(WEB_ROOT / "camera.js").read_text(), content_type="application/javascript")

    async def stylesheet(self, request):
        return web.Response(text=(WEB_ROOT / "style.css").read_text(), content_type="text/css")

    async def pair(self, request):
        async with self._offer_lock:
            return await self._pair(request)

    async def _pair(self, request):
        body = await request.json()
        if not isinstance(body, dict) or not self.tokens.consume(body.get("token")):
            raise web.HTTPUnauthorized(
                text="Pairing code is invalid, used, or expired. Scan a new code on the desktop."
            )
        if self.session_secret:
            raise web.HTTPConflict(text="Disconnect the paired phone on the desktop first")
        self.session_secret = secrets.token_urlsafe(32)
        self.session_expiry = time.monotonic() + 12 * 60 * 60
        self.state = "Paired · awaiting camera permission"
        self.notify("phone", self.state)
        return web.json_response({"session": self.session_secret})

    def authenticate(self, request):
        value = request.headers.get("X-Luma-Session", "")
        if (
            not self.session_secret
            or time.monotonic() >= self.session_expiry
            or not hmac.compare_digest(value, self.session_secret)
        ):
            raise web.HTTPUnauthorized(text="Pairing session expired. Scan a new desktop QR code.")

    async def offer(self, request):
        async with self._offer_lock:
            return await self._offer(request)

    async def _offer(self, request):
        self.authenticate(request)
        body = await request.json()
        if not isinstance(body, dict) or body.get("type") != "offer" or not isinstance(body.get("sdp"), str):
            raise web.HTTPBadRequest(text="Expected a WebRTC offer")
        sdp = body["sdp"]
        if len(sdp) > 60000 or sdp.count("m=video ") != 1 or sdp.count("m=audio ") > 1 or sdp.count("m=") > 3:
            raise web.HTTPBadRequest(text="Offer must contain one camera and at most one audio track")
        for line in sdp.splitlines():
            if line.startswith("a=candidate:"):
                fields = line.split()
                if len(fields) < 8 or not (
                    local_address(fields[4], allow_loopback=self.address == "127.0.0.1")
                    or fields[4].endswith(".local")
                ):
                    raise web.HTTPBadRequest(text="Only local ICE candidates are accepted")
        await self._close_peer()
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.peer = pc

        @pc.on("track")
        def track_received(track):
            task = asyncio.create_task(self._consume(track, pc))
            self.consumers.add(task)
            task.add_done_callback(self.consumers.discard)

        @pc.on("datachannel")
        def channel_received(channel):
            if channel.label != "luma-status":
                channel.close()
                return
            self.channel = channel

        @pc.on("connectionstatechange")
        async def connection_changed():
            if self.peer is not pc:
                return
            self.state = (
                "Camera connected" if pc.connectionState == "connected" else "Phone " + pc.connectionState
            )
            self.notify("phone", self.state)
            if pc.connectionState in {"failed", "closed"}:
                self.sources.phone.clear()
                self.sources.audio["phone"].clear()

        try:
            await asyncio.wait_for(pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer")), 10)
            answer = await pc.createAnswer()
            await asyncio.wait_for(pc.setLocalDescription(answer), 15)
        except Exception as exc:
            await self._close_peer()
            raise web.HTTPBadRequest(
                text="WebRTC negotiation failed; check camera permissions and network access"
            ) from exc
        return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})

    async def _consume(self, track, peer):
        normalizer = TimestampNormalizer()
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
        try:
            while self.peer is peer:
                frame = await track.recv()
                arrival = time.monotonic()
                stamp = normalizer.map(float(frame.time or 0), arrival)
                if track.kind == "video":
                    # Do not accept unbounded decoder output dimensions into the compositor.
                    if frame.width > 3840 or frame.height > 3840 or frame.width * frame.height > 3840 * 2160:
                        self.notify("warning", "Phone resolution exceeds the 4K receive limit")
                        continue
                    rgb = await asyncio.to_thread(frame.to_ndarray, format="rgb24")
                    self.sources.phone.put(rgb, stamp)
                else:
                    for out in resampler.resample(frame):
                        self.sources.audio["phone"].put(out.to_ndarray(), stamp - out.samples / 48000)
        except (MediaStreamError, asyncio.CancelledError):
            pass
        except Exception as exc:
            self.notify("error", f"Phone media error: {exc}")

    async def _heartbeat(self):
        previous = 0
        while True:
            await asyncio.sleep(1)
            if self.session_secret and time.monotonic() > self.session_expiry:
                await self._disconnect()
            if self.channel and self.channel.readyState == "open":
                self.channel.send(json.dumps({"recording": self.recording_status()}))
            if self.peer:
                stats = await self.peer.getStats()
                # Inbound RTP stats expose received packets, not bytes, on some aiortc versions.
                total = sum(getattr(s, "bytesReceived", 0) for s in stats.values() if s.type == "transport")
                self.bitrate = max(0, total - previous) * 8
                previous = total
                self.bytes_received = total

    async def _close_peer(self):
        peer, self.peer = self.peer, None
        self.channel = None
        for task in list(self.consumers):
            task.cancel()
        if self.consumers:
            await asyncio.gather(*self.consumers, return_exceptions=True)
        if peer:
            await peer.close()
        self.sources.phone.clear()
        self.sources.audio["phone"].clear()

    async def _disconnect(self):
        async with self._offer_lock:
            await self._disconnect_locked()

    async def _disconnect_locked(self):
        self.session_secret = None
        self.tokens.revoke()
        if self.channel and self.channel.readyState == "open":
            self.channel.send(json.dumps({"disconnect": True}))
            await asyncio.sleep(0.1)
        await self._close_peer()
        self.state = "Waiting for phone"
        self.notify("phone", self.state)

    async def disconnect_request(self, request):
        async with self._offer_lock:
            self.authenticate(request)
            await self._disconnect_locked()
        return web.json_response({"ok": True})

    def disconnect(self):
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._disconnect(), self.loop)

    def bootstrap_app(self):
        async def handle(request):
            if time.monotonic() > self.bootstrap_until or not local_address(
                request.remote or "", allow_loopback=self.address == "127.0.0.1"
            ):
                raise web.HTTPForbidden(
                    text="Certificate setup is disabled. Enable it on the desktop for ten minutes."
                )
            if request.host != f"{self.address}:{self.port + 1}":
                raise web.HTTPForbidden()
            if request.path == "/ca.crt":
                return web.Response(
                    body=self.certificates.ca.public_bytes(
                        __import__("cryptography").hazmat.primitives.serialization.Encoding.DER
                    ),
                    content_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="LumaCapture-Root-CA.crt"'},
                )
            if request.path == "/ios.mobileconfig":
                return web.Response(
                    body=self.certificates.ios_profile(), content_type="application/x-apple-aspen-config"
                )
            return web.Response(
                text=(WEB_ROOT / "trust.html")
                .read_text()
                .replace("{{FINGERPRINT}}", self.certificates.fingerprint),
                content_type="text/html",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )

        app = web.Application(client_max_size=1024)
        app.router.add_get("/", handle)
        app.router.add_get("/ca.crt", handle)
        app.router.add_get("/ios.mobileconfig", handle)
        return app

    def enable_bootstrap(self):
        self.bootstrap_until = time.monotonic() + 600
        return f"http://{self.address}:{self.port + 1}/"

    async def _cleanup(self):
        if hasattr(self, "heartbeat"):
            self.heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.heartbeat
        for attr in ("runner", "bootstrap_runner"):
            runner = getattr(self, attr, None)
            if runner:
                await runner.cleanup()
        await self._close_peer()

    def stop(self):
        self._stop_requested.set()
        self.tokens.revoke()
        self.session_secret = None
        self.bootstrap_until = 0
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
