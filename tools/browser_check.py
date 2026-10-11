"""Linux Chromium integration with real TLS trust and a synthetic camera device.

The CA is installed only in a private NSS database mounted into the browser's
namespace. User trust stores are never modified. No certificate-error bypass is used.
"""

import asyncio
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time

from playwright.async_api import async_playwright
from lumacapture.media.buffers import Sources
from lumacapture.media.model import RecordingConfig, State
from lumacapture.networking.server import PhoneServer
from lumacapture.recording.engine import Recorder
from lumacapture.security.certificates import Certificates
from lumacapture.storage.library import Library


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
    output = Path("artifacts/browser-check").resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lumacapture-browser-") as directory:
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
                "LumaCapture integration CA",
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
                    ],
                )
                context = await browser.new_context(
                    viewport={"width": 390, "height": 844},
                    permissions=["camera", "microphone"],
                    ignore_https_errors=False,
                )
                page = await context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(server.pairing_url())
                assert await page.evaluate("window.isSecureContext") is True
                assert await page.evaluate("location.hash") == ""
                await page.locator("#resolution").select_option("640")
                await page.locator("#audio").check()
                await page.locator("#connect").click()
                await wait_for(lambda: sources.phone.latest() is not None, 30)
                await page.wait_for_function(
                    "() => document.getElementById('status').textContent === 'Connected securely'"
                )
                recorder.start(RecordingConfig(mode="phone", phone_audio=True))
                await page.wait_for_function(
                    "() => document.getElementById('indicator').textContent === 'RECORDING ON DESKTOP'"
                )
                await wait_for(lambda: recorder.metrics["encoded"] >= 50)
                recorder.pause()
                await asyncio.sleep(0.3)
                recorder.resume()
                await wait_for(lambda: recorder.metrics["encoded"] >= 65)
                recorder.stop()
                await wait_for(lambda: recorder.state in {State.IDLE, State.ERROR})
                assert recorder.state == State.IDLE, recorder.error
                first_count = sources.phone.count
                await page.locator("#switch").click()
                await wait_for(lambda: sources.phone.count > first_count + 3)
                await page.screenshot(path=str(output / "mobile-interface.png"), full_page=True)
                # Exercise the application's own reconnection path after peer interruption.
                asyncio.run_coroutine_threadsafe(server._close_peer(), server.loop).result(timeout=5)
                await page.evaluate("scheduleReconnect()")
                await wait_for(lambda: sources.phone.latest() is not None, 25)
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
                await page.locator("#connect").click()
                await page.wait_for_function("() => !!window.delayedCamera")
                await page.evaluate("disconnect()")
                await page.wait_for_function(
                    "() => window.delayedCamera.getTracks().every(track => track.readyState === 'ended')"
                )
                assert await page.evaluate("stream === null")
                assert not errors, errors
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


if __name__ == "__main__":
    asyncio.run(main())
