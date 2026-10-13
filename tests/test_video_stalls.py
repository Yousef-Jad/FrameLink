from types import SimpleNamespace

from framelink.networking.server import PhoneServer


def test_stall_is_independent_of_connected_transport():
    server = PhoneServer.__new__(PhoneServer)
    server.peer = SimpleNamespace(connectionState="connected")
    server.metrics = {"last_frame_time": 100, "packets_received": 50}
    server.pending_status = "preview-ready"
    messages = []
    server.notify = lambda *args: messages.append(args)
    def status(value):
        server.pending_status = value
    server._send_status = status
    server._check_video_stall(106)
    assert server.pending_status == "video-stalled"
    assert server.metrics["video_stalls"] == 1
    server._check_video_stall(107)
    assert server.metrics["video_stalls"] == 1
    assert messages


def test_first_frame_wait_and_new_connection_reset():
    server = PhoneServer.__new__(PhoneServer)
    server.peer = SimpleNamespace(connectionState="connected")
    server.metrics = {"last_frame_time": 0}
    server.pending_status = "awaiting-video"
    server.notify = lambda *args: None
    server._send_status = lambda value: setattr(server, "pending_status", value)
    server._check_video_stall(100)
    assert server.pending_status == "awaiting-video"
    server._check_video_stall(106)
    assert server.pending_status == "video-stalled"
    server.peer.connectionState = "disconnected"
    server._check_video_stall(107)
    assert server._video_connected_since is None
