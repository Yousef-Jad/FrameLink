"""Linux Chromium integration with real TLS trust and a synthetic camera device.

The CA is installed only in a private NSS database mounted into the browser's
namespace. User trust stores are never modified. No certificate-error bypass is used.
"""

import asyncio
import os
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time

from playwright.async_api import async_playwright
from framelink.media.buffers import Sources
from framelink.media.model import RecordingConfig, State
from framelink.networking.server import PhoneServer
from framelink.recording.engine import Recorder
from framelink.security.certificates import Certificates
from framelink.storage.library import Library


def _delta(end, start, scale=1000):
    if end is None or start is None:
        return None
    return round((end - start) * scale, 3)


def build_startup_timing(browser_timing, metrics):
    """Normalize browser and desktop startup marks without mixing clocks."""
    browser_marks = browser_timing.get("marks", {})
    t6 = metrics.get("first_rtp_monotonic")
    t7 = metrics.get("first_frame_decoded_monotonic")
    t8 = metrics.get("first_preview_displayed_monotonic")
    return {
        "clock_domains": {
            "browser": browser_timing.get("clock", "performance.now"),
            "desktop": "time.monotonic",
        },
        "marks": {
            "T0": browser_marks.get("t0_page_opened"),
            "T1": browser_marks.get("t1_permission_granted"),
            "T2": browser_marks.get("t2_get_user_media_completed"),
            "T3": browser_marks.get("t3_sdp_offer_created"),
            "T4": browser_marks.get("t4_sdp_answer_received"),
            "T5": browser_marks.get("t5_ice_connected"),
            "T6": t6,
            "T7": t7,
            "T8": t8,
        },
        "durations_ms": {
            "page_to_permission": _delta(
                browser_marks.get("t1_permission_granted"), browser_marks.get("t0_page_opened")
            , scale=1),
            "permission_to_camera": _delta(
                browser_marks.get("t2_get_user_media_completed"), browser_marks.get("t1_permission_granted")
            , scale=1),
            "camera_to_offer": _delta(
                browser_marks.get("t3_sdp_offer_created"), browser_marks.get("t2_get_user_media_completed")
            , scale=1),
            "offer_to_answer": _delta(
                browser_marks.get("t4_sdp_answer_received"), browser_marks.get("t3_sdp_offer_created")
            , scale=1),
            "answer_to_ice": _delta(
                browser_marks.get("t5_ice_connected"), browser_marks.get("t4_sdp_answer_received")
            , scale=1),
            "camera_to_ice": _delta(
                browser_marks.get("t5_ice_connected"), browser_marks.get("t2_get_user_media_completed")
            , scale=1),
            "rtp_to_decode": _delta(t7, t6),
            "decode_to_preview": _delta(t8, t7),
        },
        "first_frame_latency_ms": _delta(t8, t6),
        "availability": {
            **browser_timing.get("availability", {}),
            "T6": "available" if t6 is not None else "Receiver statistics unavailable",
            "T7": "available" if t7 is not None else "No decoded frame received",
            "T8": "available" if t8 is not None else "Qt preview was not attached to this run",
        },
        "received_fps": metrics.get("fps", 0.0),
        "frames_received": metrics.get("frames_received", 0),
        "frames_decoded": metrics.get("frames_decoded", 0),
        "receiver_packets_lost": metrics.get("receiver_packets_lost", 0),
        "receiver_jitter": metrics.get("receiver_jitter", 0.0),
    }


async def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for browser media")
        await asyncio.sleep(0.05)


async def main():
    for command in ("bwrap", "certutil"):
        if not shutil.which(command):
            raise RuntimeError(f"{command} is required for isolated browser trust testing")
    output = Path(os.environ.get("FRAMELINK_BROWSER_OUTPUT", "artifacts/browser-check")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="framelink-browser-") as directory:
        root = Path(directory)
        certificates = Certificates(root / "certificates")
        nss = root / "nssdb"
        nss.mkdir()
        subprocess.run(["certutil", "-N", "-d", "sql:" + str(nss), "--empty-password"], check=True)
        subprocess.run(
            [
                "certutil",
                "-A",
                "-d",
                "sql:" + str(nss),
                "-n",
                "FrameLink integration CA",
                "-t",
                "C,,",
                "-i",
                str(certificates.ca_path),
            ],
            check=True,
        )
        sources = Sources()
        library = Library(root / "library")
        recorder = Recorder(sources, library, output)
        qt_app = qt_window = None
        if os.environ.get("FRAMELINK_PROFILE"):
            import cProfile

            run = recorder._run

            def profile_recording():
                profile = cProfile.Profile()
                profile.runcall(run)
                profile.dump_stats(str(output / "recording.prof"))

            recorder._run = profile_recording
        events = []
        server = PhoneServer(
            sources,
            certificates,
            lambda kind, text: events.append((kind, text)),
            lambda: recorder.state.value,
        )
        server.start("127.0.0.1", 18443)
        await wait_for(lambda: server._ready.is_set())
        assert not server.error, server.error
        def attach_qt_preview():
            nonlocal qt_app, qt_window
            if qt_window is not None or os.environ.get("FRAMELINK_QT_PREVIEW") != "1":
                return
            from PySide6.QtWidgets import QApplication
            from framelink.ui.window import MainWindow

            qt_app = QApplication.instance() or QApplication([])
            qt_window = MainWindow(library, root / "qt-data", smoke=True)
            qt_window.sources = sources
            qt_window.mode = "phone"
            qt_window.server = server
            # The synthetic listener intentionally binds loopback, which is
            # not returned by the production LAN-interface selector.
            qt_window.last_network_check = time.monotonic() + 3600
            qt_window.show()

        if os.environ.get("FRAMELINK_QT_PREVIEW_EARLY") == "1":
            attach_qt_preview()

        async def wait_for_qt_preview(timeout=10):
            if qt_window is None:
                attach_qt_preview()
            if qt_window is None:
                return
            deadline = time.monotonic() + timeout
            while server.metrics.get("first_preview_displayed_monotonic") is None:
                if time.monotonic() >= deadline:
                    raise AssertionError(
                        f"Timed out waiting for Qt preview: metrics={server.metrics}, "
                        f"frame_sequence={qt_window.preview._frame_sequence}, "
                        f"frame_null={qt_window.preview._frame.isNull()}"
                    )
                qt_app.processEvents()
                qt_window.tick()
                qt_window.preview.grab()
                await asyncio.sleep(0.02)
        errors = []
        try:
            async with async_playwright() as playwright:
                executable = Path(playwright.chromium.executable_path)
                wrapper = root / "chromium-isolated"
                # Bind a private trust store inside this process namespace only.
                args = [
                    "bwrap",
                    "--bind",
                    "/",
                    "/",
                    "--dev-bind",
                    "/dev",
                    "/dev",
                    "--bind",
                    str(nss),
                    str(Path.home() / ".pki/nssdb"),
                    "--bind",
                    str(nss),
                    str(Path.home() / ".local/share/pki/nssdb"),
                    str(executable),
                ]
                wrapper.write_text("#!/bin/sh\nexec " + shlex.join(args) + ' "$@"\n')
                wrapper.chmod(0o755)
                browser = await playwright.chromium.launch(
                    executable_path=str(wrapper),
                    headless=True,
                    args=[
                        "--use-fake-device-for-media-stream",
                        "--use-fake-ui-for-media-stream",
                        "--disable-background-networking",
                        *(
                            [
                                "--use-file-for-fake-video-capture="
                                + str(Path(os.environ["FRAMELINK_CAMERA_FIXTURE"]).resolve())
                            ]
                            if os.environ.get("FRAMELINK_CAMERA_FIXTURE")
                            else []
                        ),
                    ],
                )
                context = await browser.new_context(
                    viewport={"width": 390, "height": 844},
                    permissions=["camera", "microphone"],
                    ignore_https_errors=False,
                )
                page = await context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                if os.environ.get("FRAMELINK_BASELINE_JS"):
                    await page.route(
                        "**/camera.js",
                        lambda route: route.fulfill(
                            path=os.environ["FRAMELINK_BASELINE_JS"], content_type="application/javascript"
                        ),
                    )
                pairing_url = server.pairing_url()
                pairing_origin = server.origin
                await page.goto(pairing_url)
                assert await page.evaluate("window.isSecureContext") is True
                assert await page.evaluate("location.hash") == ""
                await page.locator("#connect-btn").click() if not await page.evaluate(
                    "busy || !stopped"
                ) else None
                await wait_for(lambda: sources.phone.latest() is not None, 30)
                await wait_for_qt_preview()
                await asyncio.sleep(0.15)
                startup_metrics = dict(server.metrics)
                startup_network_diagnostics = server.network_diagnostics()
                await page.wait_for_function(
                    "() => document.getElementById('status').textContent .includes('Studio')"
                )
                browser_timing = await page.evaluate("window.framelinkTiming.snapshot()")
                # Capture sender/receiver quality after a short warm-up while
                # keeping the source fresh enough for the recording gate.
                await asyncio.sleep(3)
                source_settings = await page.evaluate("stream.getVideoTracks()[0].getSettings()")
                sender_stats = await page.evaluate(
                    "async () => Array.from((await pc.getStats()).values()).filter(s => s.type === 'outbound-rtp' && s.kind === 'video')"
                )
                quality_report = await page.evaluate("window.framelinkQuality || null")
                sender_parameters = await page.evaluate(
                    "pc.getSenders().find(s => s.track?.kind === 'video').getParameters()"
                )
                received = dict(server.metrics)
                recorder.start(RecordingConfig(mode="phone", phone_audio=True))
                await page.wait_for_function(
                    "() => document.getElementById('indicator').textContent === 'RECORDING ON DESKTOP'"
                )
                await wait_for(lambda: recorder.metrics["encoded"] >= 50)
                recorder.pause()
                await asyncio.sleep(0.3)
                recorder.resume()
                await wait_for(lambda: recorder.metrics["encoded"] >= 65)
                recording_receiver = dict(server.metrics)
                recorder.stop()
                await wait_for(lambda: recorder.state in {State.COMPLETED, State.ERROR})
                assert recorder.state == State.COMPLETED, recorder.error
                first_count = sources.phone.count
                await page.locator("#flip-btn").click()
                await wait_for(lambda: sources.phone.count > first_count + 3)
                await page.screenshot(path=str(output / "mobile-interface.png"), full_page=True)
                # Exercise the application's own reconnection path after peer interruption.
                asyncio.run_coroutine_threadsafe(server._close_peer(), server.loop).result(timeout=5)
                await page.evaluate("scheduleReconnect()")
                await wait_for(lambda: sources.phone.latest() is not None, 25)
                assert server.origin == pairing_origin
                assert server.session_secret is not None
                server.disconnect()
                await page.wait_for_function(
                    "() => document.getElementById('indicator').textContent === 'CAMERA OFF'"
                )
                await wait_for(lambda: server.session_secret is None)
                assert await page.evaluate("stream === null")
                # A permission/device request may resolve after Stop or pagehide.
                # Its tracks must be closed instead of resurrecting the camera.
                await page.evaluate("""() => {
                    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
                    navigator.mediaDevices.getUserMedia = async (...args) => {
                        const captured = await original(...args);
                        window.delayedCamera = captured;
                        await new Promise(resolve => setTimeout(resolve, 1500));
                        return captured;
                    };
                }""")
                await page.goto(server.pairing_url())
                await page.wait_for_function("() => !!window.delayedCamera")
                await page.evaluate("disconnect()")
                await page.wait_for_function(
                    "() => window.delayedCamera.getTracks().every(track => track.readyState === 'ended')"
                )
                assert await page.evaluate("stream === null")
                for item in library.recordings():
                    assert (item["metadata"]["width"], item["metadata"]["height"]) == (
                        received["width"],
                        received["height"],
                    )
                    if shutil.which("ffprobe"):
                        probe = subprocess.check_output(
                            [
                                "ffprobe",
                                "-v",
                                "error",
                                "-show_streams",
                                "-show_format",
                                "-of",
                                "json",
                                item["path"],
                            ],
                            text=True,
                        )
                        (output / "ffprobe.json").write_text(probe)
                assert not errors, errors
                startup_report_metrics = dict(startup_metrics)
                for key in (
                    "fps",
                    "frames_received",
                    "frames_decoded",
                    "receiver_packets_lost",
                    "receiver_jitter",
                ):
                    startup_report_metrics[key] = received.get(key, startup_report_metrics.get(key))
                report = {
                    "browser": browser.version,
                    "trust": "CA in isolated NSS DB; HTTPS verification enabled",
                    "camera": "Chromium synthetic camera and microphone; not a physical phone",
                    "verified": [
                        "secure context",
                        "one-use pairing",
                        "video/audio receive",
                        "desktop recording indicator",
                        "phone-only recording",
                        "pause/resume",
                        "camera reacquisition",
                        "reconnect",
                        "desktop disconnect stops camera",
                        "cancelled camera acquisition closes late tracks",
                        "fresh QR in an existing browser tab",
                    ],
                    "recordings": [
                        {"metadata": r["metadata"], "filename": Path(r["path"]).name}
                        for r in library.recordings()
                    ],
                    "page_errors": errors,
                    "source_settings": {
                        k: v
                        for k, v in source_settings.items()
                        if k in {"width", "height", "frameRate", "facingMode"}
                    },
                    "sender_stats": sender_stats,
                    "sender_parameters": sender_parameters,
                    "quality_report": quality_report,
                    "received": received,
                    "received_during_recording": recording_receiver,
                    "network_diagnostics": startup_network_diagnostics,
                    "startup_timing": build_startup_timing(browser_timing, startup_report_metrics),
                    "recorder_metrics": dict(recorder.metrics),
                    "recording_elapsed": recorder.clock.elapsed(),
                    "tls": certificates.audit_endpoint(server.origin),
                }
                (output / "result.json").write_text(json.dumps(report, indent=2))
                print(json.dumps(report, indent=2))
                await browser.close()
        except Exception:
            print("Server events:", events)
            print("Browser errors:", errors)
            raise
        finally:
            recorder.stop()
            if recorder.thread:
                recorder.thread.join(15)
            server.stop()
            if server.thread:
                server.thread.join(15)
            if qt_window is not None:
                qt_window.close()
                qt_window.compression.stop()
                qt_window.compression.thread.join(15)


if __name__ == "__main__":
    asyncio.run(main())
