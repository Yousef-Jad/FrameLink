import asyncio
import json
import time
from concurrent.futures import Future

import numpy as np
import pytest
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from PySide6.QtWidgets import QApplication

from framelink.media.encode import validate_media
from framelink.media.model import State
from framelink.storage.library import Library
from framelink.ui.window import MainWindow
from conftest import feed_video
from test_network import FixtureCamera, server as server


async def pump(app, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Remote recording workflow timed out")


@pytest.fixture
async def remote(server, tmp_path, monkeypatch):
    srv, client, context = server
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Library(tmp_path / "db"), tmp_path / "data", smoke=True)
    window.recorder.output = window.output = tmp_path / "videos"
    window.output.mkdir(parents=True, exist_ok=True)
    window._test_feeder = None
    window.mic_device.clear()
    window.mic_device.addItem("Test microphone", "test-mic")
    monkeypatch.setattr(window.microphone, "start", lambda device: None)
    # Avoid physical device discovery while testing real recording and Qt commands.
    window.start_phone_server_auto = lambda: None
    srv.sources = window.sources
    srv.recording_status = lambda: window.recorder.state.value
    srv.recording_snapshot = lambda: window.phone_recording_snapshot
    srv.recording_command = window.events.recording_command.emit
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    peer.addTrack(FixtureCamera())
    channel = peer.createDataChannel("framelink-status")
    messages = []
    channel.on("message", lambda message: messages.append(json.loads(message)))
    heartbeat = asyncio.create_task(srv._heartbeat())
    try:
        async with client.post(
            srv.origin + "/pair", json={"token": srv.tokens.issue()},
            headers={"Origin": srv.origin}, ssl=context,
        ) as response:
            secret = (await response.json())["session"]
        await peer.setLocalDescription(await peer.createOffer())
        async with client.post(
            srv.origin + "/offer",
            json={"type": "offer", "sdp": peer.localDescription.sdp},
            headers={"Origin": srv.origin, "X-FrameLink-Session": secret}, ssl=context,
        ) as response:
            assert response.status == 200
            await peer.setRemoteDescription(RTCSessionDescription(**await response.json()))
        await pump(app, lambda: channel.readyState == "open" and window.sources.phone.latest() is not None)
        yield app, window, srv, channel, messages
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        await srv._close_peer()
        await peer.close()
        window.recorder.stop()
        if window.recorder.thread:
            window.recorder.thread.join(10)
        window.compression.stop()
        window.compression.thread.join(15)
        window.close()
        app.processEvents()
        if window._test_feeder:
            window._test_feeder[0].set()
            window._test_feeder[1].join(2)


async def command(remote, ident, action, revision=None):
    app, window, _, channel, messages = remote
    window.update_recording_snapshot()
    payload = {"type": "recording-command", "v": 1, "id": ident, "action": action,
               "revision": window.phone_recording_snapshot["revision"] if revision is None else revision}
    previous = len(messages)
    channel.send(json.dumps(payload))
    await pump(app, lambda: any(m.get("id") == ident for m in messages[previous:]))
    return next(m for m in messages[previous:] if m.get("id") == ident)


@pytest.mark.parametrize("mode,layout,count", [
    ("screen", "pip", 1), ("phone", "pip", 1), ("both", "pip", 1), ("both", "separate", 2),
])
async def test_encrypted_remote_record_stop_saves_and_compresses_selected_mode(remote, mode, layout, count):
    app, window, srv, channel, messages = remote
    window.change_mode(mode)
    window.layout_mode.setCurrentIndex(window.layout_mode.findData(layout))
    window.sources.screen.put(np.full((180, 320, 3), 90, np.uint8))
    if mode in {"screen", "both"}:
        window._test_feeder = feed_video(window.sources, names=("screen",), fps=30)
    reply = await command(remote, "start-1", "start")
    assert reply["ok"], reply
    assert window.recorder.config.mode == mode
    assert window.recorder.config.layout == layout
    await pump(app, lambda: window.recorder.metrics.get("encoded", 0) >= 18)
    clock = window.recorder.clock
    assert (await command(remote, "start-1", "start"))["ok"]  # Same command: no second recording.
    assert not (await command(remote, "conflicting-start", "start"))["ok"]
    assert window.recorder.clock is clock
    await pump(app, lambda: any(m.get("recorder", {}).get("state") == "Recording" for m in messages))
    snapshot = next(m["recorder"] for m in reversed(messages) if m.get("recorder", {}).get("state") == "Recording")
    assert snapshot["mode"] == mode and snapshot["elapsed"] > 0
    reply = await command(remote, "stop-1", "stop")
    assert reply["ok"]
    assert (await command(remote, "stop-1", "stop"))["ok"]
    await pump(app, lambda: window.recorder.state == State.COMPLETED and not window.recorder.thread.is_alive())
    originals = [r for r in window.library.recordings() if not r["original"]]
    assert len(originals) == count
    assert all(validate_media(r["path"])["decoded_frames"] > 0 for r in originals)
    await pump(app, lambda: len(window.library.jobs()) == count and all(j["status"] == "complete" for j in window.library.jobs()))
    await pump(app, lambda: any(m.get("recorder", {}).get("state") == "Completed" for m in messages))
    # Desktop starts are authoritative too. An old Stop must never stop this recording.
    window.start_recording()
    await pump(app, lambda: window.recorder.metrics.get("encoded", 0) >= 4)
    assert (await command(remote, "stop-1", "stop"))["ok"]
    assert window.recorder.state == State.RECORDING
    window.stop_recording()
    await pump(app, lambda: window.recorder.state == State.COMPLETED)


async def test_remote_rejects_stale_state_invalid_actions_and_missing_source(remote):
    _, window, _, _, _ = remote
    window.mode = "screen"
    reply = await command(remote, "no-source", "start")
    assert not reply["ok"] and "preview" in reply["error"].lower()
    window.sources.screen.put(np.zeros((180, 320, 3), np.uint8))
    assert not (await command(remote, "bad-action", "delete"))["ok"]
    assert not (await command(remote, "stale", "start", revision=-1))["ok"]
    assert not window.recorder.state.active


async def test_screen_recording_survives_phone_disconnect_and_expired_session_cannot_stop(remote):
    app, window, srv, channel, messages = remote
    window.mode = "screen"
    window.disconnect_policy.setCurrentIndex(window.disconnect_policy.findData("stop"))
    window.sources.screen.put(np.zeros((180, 320, 3), np.uint8))
    window._test_feeder = feed_video(window.sources, names=("screen",), fps=30)
    assert (await command(remote, "screen-start", "start"))["ok"]
    await pump(app, lambda: window.recorder.metrics.get("encoded", 0) >= 4)
    srv.session_expiry = time.monotonic() - 1
    window.update_recording_snapshot()
    channel.send(json.dumps({"type": "recording-command", "v": 1, "id": "expired-stop", "action": "stop",
                             "revision": window.phone_recording_snapshot["revision"]}))
    await pump(app, lambda: srv.peer is None)
    encoded = window.recorder.metrics["encoded"]
    await pump(app, lambda: window.recorder.metrics["encoded"] > encoded + 6)
    assert window.recorder.state == State.RECORDING
    assert not any(m.get("id") == "expired-stop" and m.get("ok") for m in messages)


async def test_revoked_command_task_exits_without_cancelled_error(tmp_path):
    from framelink.networking.server import PhoneServer
    from framelink.media.buffers import Sources
    from framelink.security.certificates import Certificates

    srv = PhoneServer(Sources(), Certificates(tmp_path / "certs"))
    srv.session_secret = "valid-session"
    srv.session_expiry = time.monotonic() + 60
    peer = object()
    channel = type("Channel", (), {"readyState": "open", "send": lambda self, value: None})()
    srv.peer = peer
    srv.channel = channel
    srv._connection_generation = 1
    pending = {}

    def queue(request, future, is_current):
        pending["future"] = future

    srv.recording_command = queue
    task = asyncio.create_task(srv._handle_recording_command(
        {"type": "recording-command", "v": 1, "id": "revoked", "action": "start", "revision": 0},
        channel,
        peer,
    ))
    await asyncio.sleep(0)
    assert "future" in pending
    srv.session_secret = None
    srv._connection_generation += 1
    pending["future"].cancel()
    await task
    assert task.exception() is None


def test_queued_command_cannot_run_after_disconnect_or_timeout(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Library(tmp_path / "db"), tmp_path / "data", smoke=True)
    window.sources.screen.put(np.zeros((180, 320, 3), np.uint8))
    try:
        window.update_recording_snapshot()
        request = {"action": "start", "revision": window.phone_recording_snapshot["revision"]}
        revoked, cancelled = Future(), Future()
        cancelled.cancel()
        window.events.recording_command.emit(request, revoked, lambda: False)
        window.events.recording_command.emit(request, cancelled, lambda: True)
        app.processEvents()
        assert revoked.done() and not revoked.result()["ok"]
        assert window.recorder.state == State.IDLE
    finally:
        window.compression.stop()
        window.compression.thread.join(5)
        window.close()
