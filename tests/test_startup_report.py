from tools.browser_check import build_startup_timing


def test_startup_report_keeps_clock_domains_and_stage_durations():
    report = build_startup_timing(
        {
            "clock": "performance.now",
            "marks": {
                "t0_page_opened": 10,
                "t1_permission_granted": 30,
                "t2_get_user_media_completed": 80,
                "t3_sdp_offer_created": 100,
                "t4_sdp_answer_received": 160,
                "t5_ice_connected": 240,
            },
            "availability": {"permission_observation": "supported"},
        },
        {
            "first_rtp_monotonic": 1000.0,
            "first_frame_decoded_monotonic": 1000.04,
            "first_preview_displayed_monotonic": 1000.14,
        },
    )
    assert report["clock_domains"]["browser"] == "performance.now"
    assert report["clock_domains"]["desktop"] == "time.monotonic"
    assert report["durations_ms"]["camera_to_ice"] == 160
    assert report["durations_ms"]["rtp_to_decode"] == 40
    assert report["durations_ms"]["decode_to_preview"] == 100
    assert report["first_frame_latency_ms"] == 140


def test_startup_report_marks_unavailable_qt_stage_explicitly():
    report = build_startup_timing({"marks": {}, "availability": {}}, {})
    assert report["marks"]["T8"] is None
    assert report["availability"]["T8"] == "Qt preview was not attached to this run"
