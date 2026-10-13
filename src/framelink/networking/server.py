import asyncio
import contextlib
import hmac
import json
from pathlib import Path
import secrets
import threading
import time
from concurrent.futures import Future
import av
from aiohttp import web
from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError
from framelink.security.pairing import PairingTokens, RateLimiter
from framelink.networking.interfaces import local_address
from framelink.networking.interfaces import interface_for_address, firewall_diagnostics
from framelink.networking.ice import extract_ice_candidates, filter_local_answer_candidates, filter_remote_offer_candidates
from framelink.media.model import TimestampNormalizer


WEB_ROOT = Path(__file__).resolve().parent.parent / "web"


class PhoneServer:
    def __init__(
        self,
        sources,
        certificates,
        notify=lambda *args: None,
        recording_status=lambda: "Ready",
        recording_snapshot=lambda: {},
        recording_command=None,
    ):
        self.sources, self.certificates = sources, certificates
        self.notify = notify
        self.recording_status = recording_status
        self.recording_snapshot = recording_snapshot
        self.recording_command = recording_command
        self.tokens = PairingTokens()
        self.limiter = RateLimiter()
        self.session_secret = None
        self.session_expiry = 0
        self.peer = None
        self.channel = None
        self.consumers = set()
        self.receiver_watchers = set()
        self.loop = None
        self.thread = None
        self.address = None
        self.port = 8443
        self.bootstrap_until = 0
        self.state = "Offline"
        self.error = ""
        self.phone_quality = {}
        self.bytes_received = 0
        self.bitrate = 0
        self.metrics = self._new_metrics()
        self.preview_ack_sent = False
        self._video_connected_since = None
        self.pending_status = "awaiting-video"
        self.ice_diagnostics = {
            "remote_candidates": [],
            "local_candidates": [],
            "selected_pair": None,
            "ice_gathering_state": "new",
            "ice_connection_state": "new",
            "dtls_state": "new",
            "failure_reason": "",
        }
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._offer_lock = asyncio.Lock()
        self._recording_command_lock = asyncio.Lock()
        self._recording_command_results = {}
        self._recording_command_pending = set()
        self._connection_generation = 0

    @property
    def origin(self):
        return f"https://{self.address}:{self.port}"

    @staticmethod
    def _new_metrics():
        return {
            "packets_received": 0,
            "frames_received": 0,
            "frames_decoded": 0,
            "frames_buffered": 0,
            "frames_displayed": 0,
            "last_preview_time": 0.0,
            "fps": 0.0,
            "width": 0,
            "height": 0,
            "last_frame_stamp": 0.0,
            "last_frame_time": 0.0,
            "first_rtp_monotonic": None,
            "first_frame_decoded_monotonic": None,
            "first_preview_displayed_monotonic": None,
            "first_frame_sequence": None,
            "receiver_packets_lost": 0,
            "receiver_jitter": 0.0,
            "receiver_bitrate": 0,
            "receiver_bytes_total": 0,
        }

    def _receiver_snapshot(self, stats):
        inbound = [
            item
            for item in stats.values()
            if getattr(item, "type", "") == "inbound-rtp" and getattr(item, "kind", "") == "video"
        ]
        if not inbound:
            return None
        packets = sum(int(getattr(item, "packetsReceived", 0) or 0) for item in inbound)
        lost = sum(int(getattr(item, "packetsLost", 0) or 0) for item in inbound)
        jitter = max((float(getattr(item, "jitter", 0.0) or 0.0) for item in inbound), default=0.0)
        bytes_total = sum(int(getattr(item, "bytesReceived", 0) or 0) for item in inbound)
        now = time.monotonic()
        previous_bytes = self.metrics.get("receiver_bytes_total", 0)
        previous_time = self.metrics.get("receiver_sample_time")
        bitrate = 0
        if previous_time is not None and now > previous_time:
            bitrate = max(0, round((bytes_total - previous_bytes) * 8 / (now - previous_time)))
        self.metrics["packets_received"] = packets
        self.metrics["receiver_packets_lost"] = lost
        self.metrics["receiver_jitter"] = jitter
        if bytes_total:
            self.metrics["receiver_bitrate"] = bitrate
        self.metrics["receiver_bytes_total"] = bytes_total
        self.metrics["receiver_sample_time"] = now
        return packets

    def _send_status(self, status):
        self.pending_status = status
        channel = self.channel
        if not channel or channel.readyState != "open":
            return False
        try:
            channel.send(json.dumps({"status": status, **self._recording_message()}))
            return True
        except Exception:
            return False

    def _recording_message(self):
        try:
            snapshot = self.recording_snapshot() or {}
        except Exception:
            snapshot = {}
        return {
            "recording": self.recording_status(),
            "recorder": snapshot,
        }

    def _send_recording_snapshot(self):
        channel = self.channel
        if not channel or channel.readyState != "open":
            return False
        try:
            channel.send(json.dumps(self._recording_message()))
            return True
        except Exception:
            return False

    def push_recording_snapshot(self):
        """Push a desktop-side state transition without waiting for heartbeat."""
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self._send_recording_snapshot)

    @staticmethod
    def _recording_command_error(command_id, error, snapshot=None):
        result = {"type": "recording-result", "id": command_id, "ok": False, "error": error}
        if snapshot is not None:
            result["recorder"] = snapshot
        return result

    async def _handle_recording_command(self, report, channel, peer):
        command_id = report.get("id") if isinstance(report, dict) else ""
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 80:
            response = self._recording_command_error("", "Invalid recording command id")
        elif self.session_secret is None or time.monotonic() >= self.session_expiry:
            response = self._recording_command_error(command_id, "Phone session expired; scan a new QR code")
        elif report.get("v") != 1 or report.get("action") not in {"start", "stop"}:
            response = self._recording_command_error(command_id, "Invalid recording command")
        elif not isinstance(report.get("revision"), int) or report["revision"] < 0:
            response = self._recording_command_error(command_id, "Invalid recording state revision")
        elif self.recording_command is None:
            response = self._recording_command_error(command_id, "Desktop recording control is unavailable")
        else:
            generation = self._connection_generation
            async with self._recording_command_lock:
                if command_id in self._recording_command_results:
                    response = self._recording_command_results[command_id]
                else:
                    future = Future()
                    self._recording_command_pending.add(future)
                    def is_current():
                        return (
                            self.peer is peer
                            and self.channel is channel
                            and self._connection_generation == generation
                            and self.session_secret is not None
                            and time.monotonic() < self.session_expiry
                        )
                    try:
                        self.recording_command(
                            report,
                            future,
                            is_current,
                        )
                        response = await asyncio.wait_for(asyncio.wrap_future(future), 10)
                        response = {
                            "type": "recording-result",
                            "id": command_id,
                            **response,
                        }
                    except asyncio.CancelledError:
                        if not is_current():
                            return
                        raise
                    except asyncio.TimeoutError:
                        response = self._recording_command_error(
                            command_id, "Desktop did not confirm the recording command"
                        )
                    except Exception as exc:
                        response = self._recording_command_error(command_id, str(exc)[:200])
                    finally:
                        self._recording_command_pending.discard(future)
                    self._recording_command_results[command_id] = response
        if (
            self.peer is peer
            and self.channel is channel
            and channel.readyState == "open"
            and self.session_secret is not None
            and time.monotonic() < self.session_expiry
        ):
            try:
                channel.send(json.dumps(response))
            except Exception:
                pass

    def network_diagnostics(self):
        return {
            "desktop_ip": self.address,
            "interface": interface_for_address(self.address),
            "https": {
                "origin": self.origin if self.address else None,
                "tcp_port": self.port,
                "reachable_from_peer": None,
            },
            "firewall": firewall_diagnostics(self.port),
            "ice": dict(self.ice_diagnostics),
            "dtls": {"state": self.ice_diagnostics.get("dtls_state", "new")},
            "receiver": {
                key: self.metrics.get(key)
                for key in (
                    "packets_received",
                    "receiver_packets_lost",
                    "frames_received",
                    "frames_decoded",
                    "frames_buffered",
                    "frames_displayed",
                    "last_frame_time",
                    "last_preview_time",
                    "video_frame_age_seconds",
                    "camera_lifecycle",
                    "first_preview_displayed_monotonic",
                )
            },
        }

    def filter_answer_sdp(self, sdp):
        return filter_local_answer_candidates(sdp, self.address)

    def clear_failed_peer(self):
        """Clear failed transport state while retaining a valid pairing session."""
        self.channel = None
        self.sources.phone.clear()
        self.sources.phone_preview.clear()
        self.sources.audio["phone"].clear()
        if self.session_secret and time.monotonic() < self.session_expiry:
            self.state = "Waiting for WebRTC reconnect"
            self.notify("phone", self.state)

    @staticmethod
    def _candidate_summary(candidate):
        if candidate is None:
            return None
        return {
            "foundation": getattr(candidate, "foundation", ""),
            "address": getattr(candidate, "host", ""),
            "port": getattr(candidate, "port", 0),
            "protocol": getattr(candidate, "protocol", ""),
            "type": getattr(candidate, "type", ""),
        }

    def _refresh_transport_diagnostics(self, peer):
        for transceiver in peer.getTransceivers():
            transport = getattr(transceiver.sender, "transport", None)
            if transport is None:
                continue
            ice = getattr(transport, "iceTransport", None)
            connection = getattr(ice, "_connection", None)
            if connection is not None:
                self.ice_diagnostics["ice_connection_state"] = ice.state
                nominated = getattr(connection, "_nominated", {}).get(1)
                if nominated:
                    self.ice_diagnostics["selected_pair"] = {
                        "local": self._candidate_summary(nominated.protocol.local_candidate),
                        "remote": self._candidate_summary(nominated.remote_candidate),
                    }
                self.ice_diagnostics["local_candidates"] = [
                    self._candidate_summary(candidate) for candidate in connection.local_candidates
                ]
                self.ice_diagnostics["remote_candidates"] = [
                    self._candidate_summary(candidate) for candidate in connection.remote_candidates
                ]
            self.ice_diagnostics["dtls_state"] = getattr(transport, "state", "new")
            return
    def pairing_url(self):
        if self.state == "Offline" or not self.address:
            raise ValueError("Start the local server first")
        return f"{self.origin}/#{self.tokens.get_or_create()}"

    def new_pairing_url(self):
        if self.state == "Offline" or not self.address:
            raise ValueError("Start the local server first")
        return f"{self.origin}/#{self.tokens.issue()}"

    def start(self, address, port=8443):
        if not local_address(address, allow_loopback=True):
            raise ValueError("The camera server may bind only to a private LAN address")
        if self.thread and self.thread.is_alive():
            raise ValueError("Stop the existing server before changing network interfaces")
        self.address, self.port = address, port
        self._ready.clear()
        self._stop_requested.clear()
        self.error = ""
        self.metrics = self._new_metrics()
        self.preview_ack_sent = False
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
        self._recording_command_lock = asyncio.Lock()
        self._recording_command_results.clear()
        self._recording_command_pending.clear()
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
            allowed_hosts = {f"{self.address}:{self.port}", f"[{self.address}]:{self.port}", self.address}
            if request.host not in allowed_hosts:
                raise web.HTTPForbidden(text="Unexpected Host")
            if request.method not in {"GET", "POST"}:
                raise web.HTTPMethodNotAllowed(request.method, ["GET", "POST"])
            if request.method == "POST":
                if request.headers.get("Origin") not in {
                    self.origin,
                    f"https://[{self.address}]:{self.port}",
                }:
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
        app.router.add_get("/recording.js", self.recording_script)
        app.router.add_get("/quality.js", self.quality_script)
        app.router.add_get("/style.css", self.stylesheet)
        app.router.add_get("/verify", self.verify)
        app.router.add_get("/ca.crt", self.ca_cert)
        app.router.add_get("/ios.mobileconfig", self.ios_mobileconfig)
        app.router.add_post("/pair", self.pair)
        app.router.add_post("/offer", self.offer)
        app.router.add_post("/disconnect", self.disconnect_request)
        return app

    async def index(self, request):
        return web.Response(text=(WEB_ROOT / "index.html").read_text(), content_type="text/html")

    async def quality_script(self, request):
        return web.Response(text=(WEB_ROOT / "quality.js").read_text(), content_type="application/javascript")

    async def javascript(self, request):
        return web.Response(text=(WEB_ROOT / "camera.js").read_text(), content_type="application/javascript")

    async def recording_script(self, request):
        return web.Response(text=(WEB_ROOT / "recording.js").read_text(), content_type="application/javascript")

    async def stylesheet(self, request):
        return web.Response(text=(WEB_ROOT / "style.css").read_text(), content_type="text/css")

    async def ca_cert(self, request):
        from cryptography.hazmat.primitives import serialization

        return web.Response(
            body=self.certificates.ca.public_bytes(serialization.Encoding.DER),
            content_type="application/x-x509-ca-cert",
            headers={"Content-Disposition": 'attachment; filename="FrameLink-Root-CA.crt"'},
        )

    async def ios_mobileconfig(self, request):
        return web.Response(
            body=self.certificates.ios_profile(), content_type="application/x-apple-aspen-config"
        )

    async def verify(self, request):
        html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>FrameLink — HTTPS Connection Check</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0b0e14; color: #f0f3fa; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; box-sizing: border-box; text-align: center; }
    .card { background: #151922; border: 1px solid #283042; border-radius: 20px; padding: 36px 24px; max-width: 440px; box-shadow: 0 10px 40px rgba(0,0,0,0.5); }
    .icon { font-size: 54px; margin-bottom: 16px; }
    h1 { font-size: 22px; margin: 0 0 12px 0; color: #38bdf8; }
    p { font-size: 15px; color: #94a3b8; line-height: 1.5; margin: 0 0 24px 0; }
    .badge { display: inline-block; background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 8px; padding: 6px 14px; font-size: 13px; font-weight: 600; margin-bottom: 20px; }
    .btn { display: block; background: #2563eb; color: white; text-decoration: none; padding: 14px 20px; border-radius: 12px; font-weight: 600; font-size: 16px; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">🔒</div>
    <div class="badge">HTTPS ENDPOINT REACHED</div>
    <h1>Check your browser’s certificate status</h1>
    <p>This page cannot determine whether you bypassed a certificate warning. Close this tab if Safari displayed a warning. Install and explicitly trust the matching desktop root certificate, then open this address again without selecting Visit This Website.</p>
    <a href="/" class="btn">Open Camera</a>
  </div>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")

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
        self.state = "Pairing successful · awaiting camera permission"
        self.notify("phone", self.state)
        return web.json_response({"session": self.session_secret})

    def authenticate(self, request):
        value = request.headers.get("X-FrameLink-Session") or request.headers.get("X-Luma-Session", "")
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

        remote_ip = request.remote or ""
        if remote_ip.startswith("::ffff:"):
            remote_ip = remote_ip[7:]
        elif "%" in remote_ip:
            remote_ip = remote_ip.split("%")[0]

        # Sanitize and rewrite ICE candidates:
        # Chrome/Safari obfuscate local host candidates with mDNS UUIDs (.local).
        # On local networks without an mDNS responder on the phone, aioice fails resolution
        # and discards the candidate, causing ICE to fail. We unmask .local with remote_ip.
        self.ice_diagnostics["remote_candidates"] = extract_ice_candidates(sdp)
        sdp = filter_remote_offer_candidates(sdp, remote_ip) if remote_ip else sdp

        await self._close_peer()
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self._connection_generation += 1
        self.peer = pc
        self.metrics = self._new_metrics()
        self.preview_ack_sent = False
        self._video_connected_since = None
        self.pending_status = "awaiting-video"
        self.state = "WebRTC negotiating"
        self.notify("phone", self.state)
        self.ice_diagnostics.update(
            remote_candidates=extract_ice_candidates(sdp),
            local_candidates=[],
            selected_pair=None,
            ice_gathering_state=pc.iceGatheringState,
            ice_connection_state=pc.iceConnectionState,
            dtls_state="new",
            failure_reason="",
        )

        @pc.on("track")
        def track_received(track):
            task = asyncio.create_task(self._consume(track, pc))
            self.consumers.add(task)
            task.add_done_callback(self.consumers.discard)

        @pc.on("datachannel")
        def channel_received(channel):
            if channel.label not in {"framelink-status", "luma-status"}:
                channel.close()
                return
            self.channel = channel

            @channel.on("message")
            def quality_message(message):
                if not isinstance(message, str) or len(message) > 4096:
                    return
                try:
                    report = json.loads(message)
                    if isinstance(report, dict) and report.get("type") == "recording-command":
                        asyncio.create_task(self._handle_recording_command(report, channel, pc))
                        return
                    if isinstance(report, dict) and report.get("type") == "camera-lifecycle":
                        self.metrics["camera_lifecycle"] = {
                            key: report.get(key) for key in (
                                "ready_state", "muted", "enabled", "peer_state", "ice_state",
                                "restarts", "reconnects", "hidden", "mute_events", "unmute_events", "ended_events",
                            )
                        }
                        return
                    if isinstance(report, dict) and report.get("type") == "ice-diagnostics":
                        self.ice_diagnostics.update(
                            {
                                key: report.get(key)
                                for key in (
                                    "local_candidates",
                                    "remote_candidates",
                                    "selected_pair",
                                    "ice_gathering_state",
                                    "ice_connection_state",
                                    "peer_connection_state",
                                    "dtls_state",
                                )
                                if key in report
                            }
                        )
                        return
                    if not isinstance(report, dict) or report.get("type") != "quality":
                        return
                    safe = {}
                    for key in (
                        "capture_width",
                        "capture_height",
                        "capture_fps",
                        "sent_width",
                        "sent_height",
                        "sent_fps",
                        "bitrate",
                        "target_bitrate",
                        "rtt",
                    ):
                        value = report.get(key, 0)
                        if isinstance(value, (int, float)) and 0 <= value <= 1000000000:
                            safe[key] = value
                    for key in ("limitation", "parameter_error"):
                        safe[key] = str(report.get(key, ""))[:200]
                    self.phone_quality = safe
                except (ValueError, TypeError):
                    pass

            self._send_status("awaiting-video")

            @channel.on("open")
            def channel_opened():
                self._send_status(self.pending_status)
                self._send_recording_snapshot()

        @pc.on("connectionstatechange")
        async def connection_changed():
            if self.peer is not pc:
                return
            if pc.connectionState == "connected":
                if self.metrics.get("first_frame_decoded_monotonic") is None:
                    self.state = "WebRTC connected · awaiting video frames"
            elif pc.connectionState == "connecting":
                if self.metrics.get("first_frame_decoded_monotonic") is None:
                    self.state = "WebRTC connecting…"
            elif pc.connectionState in {"failed", "closed", "disconnected"}:
                self.ice_diagnostics["failure_reason"] = f"connection={pc.connectionState}"
                self.clear_failed_peer()
                self.state = "Phone " + pc.connectionState
            else:
                self.state = "Phone " + pc.connectionState
            self.notify("phone", self.state)

        @pc.on("icegatheringstatechange")
        def ice_gathering_changed():
            self.ice_diagnostics["ice_gathering_state"] = pc.iceGatheringState

        @pc.on("iceconnectionstatechange")
        def ice_connection_changed():
            self.ice_diagnostics["ice_connection_state"] = pc.iceConnectionState
            if pc.iceConnectionState == "failed":
                self.ice_diagnostics["failure_reason"] = "ice connection failed"

        @pc.on("icecandidate")
        def ice_candidate(candidate):
            if candidate:
                self.ice_diagnostics["local_candidates"].append(
                    {
                        "address": candidate.host,
                        "port": candidate.port,
                        "protocol": candidate.protocol,
                        "type": candidate.type,
                    }
                )

        try:
            await asyncio.wait_for(pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer")), 10)
            answer = await pc.createAnswer()
            await asyncio.wait_for(pc.setLocalDescription(answer), 15)
            answer_sdp = self.filter_answer_sdp(pc.localDescription.sdp)
            self.ice_diagnostics["local_candidates"] = extract_ice_candidates(answer_sdp)
            self.ice_diagnostics["ice_gathering_state"] = pc.iceGatheringState
            watcher = asyncio.create_task(self._watch_first_rtp(pc))
            self.receiver_watchers.add(watcher)
            watcher.add_done_callback(self.receiver_watchers.discard)
        except Exception as exc:
            await self._close_peer()
            raise web.HTTPBadRequest(
                text="WebRTC negotiation failed; check camera permissions and network access"
            ) from exc
        return web.json_response({"sdp": self.filter_answer_sdp(pc.localDescription.sdp), "type": pc.localDescription.type})

    async def _consume(self, track, peer):
        normalizer = TimestampNormalizer()
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
        first_frame = True
        frame_count = 0
        fps_start = time.monotonic()
        try:
            while self.peer is peer:
                frame = await track.recv()
                if self.peer is not peer:
                    break
                arrival = time.monotonic()
                stamp = normalizer.map(float(frame.time or 0), arrival)
                if track.kind == "video":
                    # Do not accept unbounded decoder output dimensions into the compositor.
                    if frame.width > 3840 or frame.height > 3840 or frame.width * frame.height > 3840 * 2160:
                        self.notify("warning", "Phone resolution exceeds the 4K receive limit")
                        continue
                    rgb = await asyncio.to_thread(frame.to_ndarray, format="rgb24")
                    decoded_at = time.monotonic()
                    if self.peer is not peer:
                        break
                    self.sources.phone.put(rgb, stamp)
                    self.sources.phone_preview.put(rgb, stamp)
                    self.metrics["frames_buffered"] += 1
                    self.metrics["frames_received"] += 1
                    self.metrics["frames_decoded"] += 1
                    self.metrics["width"] = frame.width
                    self.metrics["height"] = frame.height
                    self.metrics["last_frame_stamp"] = stamp
                    self.metrics["last_frame_time"] = arrival
                    frame_count += 1
                    elapsed = arrival - fps_start
                    if elapsed >= 1.0:
                        self.metrics["fps"] = frame_count / elapsed
                        frame_count = 0
                        fps_start = arrival
                    if self.pending_status == "video-stalled":
                        self._send_status("video-received")
                    if first_frame:
                        first_frame = False
                        self.metrics["first_frame_decoded_monotonic"] = decoded_at
                        self.metrics["first_frame_sequence"] = self.sources.phone_preview.latest().sequence
                        self.state = f"Receiving video ({frame.width}×{frame.height})"
                        self.notify("phone", self.state)
                        self._send_status("video-received")
                else:
                    for out in resampler.resample(frame):
                        self.sources.audio["phone"].put(out.to_ndarray(), stamp - out.samples / 48000)
        except (MediaStreamError, asyncio.CancelledError):
            pass
        except Exception as exc:
            self.notify("error", f"Phone media error: {exc}")

    async def _watch_first_rtp(self, peer):
        """Observe the receiver's first inbound RTP packet without delaying decode."""
        while self.peer is peer and self.metrics.get("first_rtp_monotonic") is None:
            try:
                stats = await peer.getStats()
                packets = self._receiver_snapshot(stats)
                if packets:
                    self.metrics["first_rtp_monotonic"] = time.monotonic()
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(0.02)

    def mark_preview_displayed(self, sequence):
        """Record T8 after Qt has copied and presented a phone preview frame."""
        if self.loop and self.loop.is_running() and threading.current_thread() is not self.thread:
            result = Future()

            def apply_on_server_loop():
                try:
                    result.set_result(self._mark_preview_displayed(sequence))
                except Exception as exc:
                    result.set_exception(exc)

            try:
                self.loop.call_soon_threadsafe(apply_on_server_loop)
                return result.result(timeout=1)
            except (RuntimeError, TimeoutError):
                return False
            except Exception:
                return False
        return self._mark_preview_displayed(sequence)

    def _mark_preview_displayed(self, sequence):
        """Mutate preview timing state on the server's asyncio thread."""
        if sequence != self.metrics.get("last_preview_sequence"):
            self.metrics["frames_displayed"] = self.metrics.get("frames_displayed", 0) + 1
            self.metrics["last_preview_sequence"] = sequence
            self.metrics["last_preview_time"] = time.monotonic()
        if self.metrics.get("first_preview_displayed_monotonic") is not None:
            return False
        if self.metrics.get("first_frame_sequence") is None:
            return False
        self.metrics["first_preview_displayed_monotonic"] = time.monotonic()
        self.preview_ack_sent = True
        self.state = f"Preview ready ({self.metrics['width']}×{self.metrics['height']})"
        self.notify("phone", self.state)
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self._send_status, "preview-ready")
        return True

    def _check_video_stall(self, now):
        if not self.peer or self.peer.connectionState != "connected":
            self._video_connected_since = None
            return
        if getattr(self, "_video_connected_since", None) is None:
            self._video_connected_since = now
        last = self.metrics.get("last_frame_time") or self._video_connected_since
        self.metrics["video_frame_age_seconds"] = max(0, now - last)
        if now - last > 5 and self.pending_status != "video-stalled":
            self.state = "Video stalled · awaiting camera frames"
            self.metrics["video_stalls"] = self.metrics.get("video_stalls", 0) + 1
            self._send_status("video-stalled")
            self.notify("phone", self.state)

    async def _heartbeat(self):
        previous = 0
        connect_time = None
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            if self.session_secret and now > self.session_expiry:
                await self._disconnect()
            self._check_video_stall(now)
            if self.channel and self.channel.readyState == "open":
                try:
                    self.channel.send(
                        json.dumps(
                            {
                                **self._recording_message(),
                                "status": self.pending_status,
                                "fps": round(self.metrics.get("fps", 0), 1),
                                "frames": self.metrics.get("frames_received", 0),
                                "receiver": {k: self.metrics[k] for k in ("width", "height", "fps")},
                            }
                        )
                    )
                except Exception:
                    pass
            if self.peer:
                try:
                    self._refresh_transport_diagnostics(self.peer)
                    stats = await self.peer.getStats()
                    self._receiver_snapshot(stats)
                    total = sum(
                        getattr(s, "bytesReceived", 0) for s in stats.values() if s.type == "transport"
                    )
                    self.bitrate = max(0, total - previous) * 8
                    previous = total
                    self.bytes_received = total
                    self.metrics["receiver_bitrate"] = self.bitrate
                except Exception:
                    pass
                if self.peer.connectionState == "connected":
                    if connect_time is None:
                        connect_time = now
                    elif now - connect_time > 5 and self.metrics.get("frames_received", 0) == 0:
                        diagnostics = self.network_diagnostics()
                        self.ice_diagnostics["failure_reason"] = (
                            f"connected without decoded video; packets={self.metrics.get('packets_received', 0)}; ICE={self.ice_diagnostics.get('ice_connection_state')} "
                            f"DTLS={self.ice_diagnostics.get('dtls_state')} "
                            f"pair={self.ice_diagnostics.get('selected_pair')}"
                        )
                        self.notify(
                            "warning",
                            "No decoded camera video is arriving. "
                            f"ICE={self.ice_diagnostics.get('ice_connection_state')}, "
                            f"DTLS={self.ice_diagnostics.get('dtls_state')}. "
                            f"Video RTP packets={self.metrics.get('packets_received', 0)}; "
                            f"interface={diagnostics.get('interface') or 'selection'}. "
                            "Compare camera-track, RTP and decoder diagnostics before changing network settings.",
                        )
                else:
                    connect_time = None
            else:
                connect_time = None

    async def _close_peer(self):
        self._connection_generation += 1
        peer, self.peer = self.peer, None
        self.channel = None
        for future in list(self._recording_command_pending):
            future.cancel()
        self._recording_command_pending.clear()
        for task in list(self.consumers):
            task.cancel()
        if self.consumers:
            await asyncio.gather(*self.consumers, return_exceptions=True)
        for task in list(self.receiver_watchers):
            task.cancel()
        if self.receiver_watchers:
            await asyncio.gather(*self.receiver_watchers, return_exceptions=True)
        self.receiver_watchers.clear()
        if peer:
            await peer.close()
        self.sources.phone.clear()
        self.sources.phone_preview.clear()
        self.sources.audio["phone"].clear()

    async def _disconnect(self):
        async with self._offer_lock:
            await self._disconnect_locked()

    async def _disconnect_locked(self):
        self.session_secret = None
        self.tokens.revoke()
        self._recording_command_results.clear()
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
                request.remote or "", allow_loopback=self.address in {"127.0.0.1", "::1"}
            ):
                raise web.HTTPForbidden(
                    text="Certificate setup is disabled. Enable it on the desktop for ten minutes."
                )
            if request.path == "/ca.crt":
                return await self.ca_cert(request)
            if request.path == "/ios.mobileconfig":
                return await self.ios_mobileconfig(request)
            template = (WEB_ROOT / "trust.html").read_text()
            rendered = (
                template.replace("{{FINGERPRINT}}", self.certificates.fingerprint)
                .replace("{{HOST}}", self.address or "localhost")
                .replace("{{PORT}}", str(self.port))
                .replace("{{ORIGIN}}", self.origin)
            )
            return web.Response(
                text=rendered,
                content_type="text/html",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )

        app = web.Application(client_max_size=1024)
        app.router.add_get("/", handle)
        app.router.add_get("/ca.crt", handle)
        app.router.add_get("/ios.mobileconfig", handle)
        return app

    def enable_bootstrap(self):
        self.bootstrap_until = time.monotonic() + 3600
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
