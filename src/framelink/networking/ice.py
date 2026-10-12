"""Offline host-candidate inspection and filtering helpers."""

import ipaddress
import re


_CANDIDATE = re.compile(
    r"^a=candidate:(?P<foundation>\S+)\s+(?P<component>\d+)\s+"
    r"(?P<protocol>\S+)\s+(?P<priority>\d+)\s+(?P<address>\S+)\s+"
    r"(?P<port>\d+)\s+typ\s+(?P<type>\S+)(?:\s|$)"
)


def _candidate_lines(sdp):
    return sdp.splitlines()


def extract_ice_candidates(sdp):
    result = []
    for line in _candidate_lines(sdp):
        match = _CANDIDATE.match(line)
        if not match:
            continue
        values = match.groupdict()
        result.append(
            {
                "foundation": values["foundation"],
                "component": int(values["component"]),
                "protocol": values["protocol"].lower(),
                "priority": int(values["priority"]),
                "address": values["address"],
                "port": int(values["port"]),
                "type": values["type"],
            }
        )
    return result


def _same_host(address, selected):
    if address == selected:
        return True
    if address.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(address).is_loopback and ipaddress.ip_address(selected).is_loopback
    except ValueError:
        return False


def _filter_candidates(sdp, selected_address, rewrite_mdns=False):
    lines = []
    for line in _candidate_lines(sdp):
        match = _CANDIDATE.match(line)
        if not match:
            lines.append(line)
            continue
        address = match.group("address")
        if not _same_host(address, selected_address):
            continue
        if rewrite_mdns and address.endswith(".local"):
            fields = line.split()
            fields[4] = selected_address
            line = " ".join(fields)
        lines.append(line)
    return "\r\n".join(lines) + "\r\n"


def filter_remote_offer_candidates(sdp, peer_address):
    """Keep host candidates reachable from the HTTP peer and unmask mDNS."""
    try:
        if ipaddress.ip_address(peer_address).is_loopback:
            return sdp
    except ValueError:
        pass
    return _filter_candidates(sdp, peer_address, rewrite_mdns=True)


def filter_local_answer_candidates(sdp, selected_address):
    """Keep only the host candidates for the interface used by the QR URL."""
    try:
        if ipaddress.ip_address(selected_address).is_loopback:
            return sdp
    except ValueError:
        pass
    return _filter_candidates(sdp, selected_address)
