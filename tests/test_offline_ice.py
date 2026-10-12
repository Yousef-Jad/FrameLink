from framelink.networking.ice import (
    extract_ice_candidates,
    filter_local_answer_candidates,
    filter_remote_offer_candidates,
)


OFFER = """v=0
a=candidate:1 1 UDP 2130706431 172.20.10.2 50000 typ host
a=candidate:2 1 UDP 2130706431 192.168.0.44 50001 typ host
a=candidate:3 1 UDP 2130706431 phone-uuid.local 50002 typ host
a=end-of-candidates
"""


def test_offline_offer_keeps_only_the_tcp_peer_network_candidate():
    filtered = filter_remote_offer_candidates(OFFER, "172.20.10.2")
    candidates = extract_ice_candidates(filtered)
    assert [candidate["address"] for candidate in candidates] == [
        "172.20.10.2",
        "172.20.10.2",
    ]
    assert [candidate["port"] for candidate in candidates] == [50000, 50002]


def test_offline_answer_advertises_only_selected_desktop_interface():
    answer = """v=0
a=candidate:1 1 UDP 2130706431 192.168.0.106 40000 typ host
a=candidate:2 1 UDP 2130706431 172.20.10.7 40001 typ host
a=candidate:3 1 UDP 2130706431 127.0.0.1 40002 typ host
a=end-of-candidates
"""
    filtered = filter_local_answer_candidates(answer, "172.20.10.7")
    candidates = extract_ice_candidates(filtered)
    assert [candidate["address"] for candidate in candidates] == ["172.20.10.7"]


def test_candidate_summary_exposes_type_protocol_and_address_without_credentials():
    candidates = extract_ice_candidates(OFFER)
    assert candidates[0] == {
        "foundation": "1",
        "component": 1,
        "protocol": "udp",
        "priority": 2130706431,
        "address": "172.20.10.2",
        "port": 50000,
        "type": "host",
    }
    assert all("token" not in candidate and "session" not in candidate for candidate in candidates)
