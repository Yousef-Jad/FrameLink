from tools.performance_benchmark import wait_for_capture_stop, wait_for_first_frame


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_native_benchmark_waits_for_first_frame_before_recording_clock():
    clock = FakeClock()
    pump_count = 0

    def pump_events():
        nonlocal pump_count
        pump_count += 1
        clock.now += 0.25

    result = wait_for_first_frame(
        pump_events,
        lambda: clock.now >= 101.0,
        timeout=5,
        clock=clock.time,
        sleep=lambda _seconds: None,
    )

    assert pump_count == 4
    assert result["wait_seconds"] == 1.0
    assert result["first_frame_at"] == 101.0


def test_native_benchmark_can_wait_without_deadline_for_portal_approval():
    clock = FakeClock()
    ready = [False]

    def pump_events():
        clock.now += 0.25
        if clock.now >= 101.0:
            ready[0] = True

    result = wait_for_first_frame(
        pump_events,
        lambda: ready[0],
        timeout=None,
        clock=clock.time,
        sleep=lambda _seconds: None,
    )

    assert result["wait_seconds"] == 1.0


def test_native_benchmark_waits_for_portal_session_to_close():
    clock = FakeClock()
    active = [True]

    def pump_events():
        clock.now += 0.25
        if clock.now >= 101.0:
            active[0] = False

    assert wait_for_capture_stop(
        pump_events,
        lambda: active[0],
        timeout=5,
        clock=clock.time,
        sleep=lambda _seconds: None,
    )
