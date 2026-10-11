import ipaddress
import socket
import psutil


PRIVATE_RANGES = [
    ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")
]


def local_address(address, allow_loopback=False):
    try:
        ip = ipaddress.ip_address(address)
        return (allow_loopback and ip.is_loopback) or any(ip in net for net in PRIVATE_RANGES)
    except ValueError:
        return False


def interfaces():
    stats = psutil.net_if_stats()
    found = []
    for name, addresses in psutil.net_if_addrs().items():
        if name not in stats or not stats[name].isup or name.startswith(("docker", "veth", "br-")):
            continue
        for addr in addresses:
            if addr.family == socket.AF_INET and local_address(addr.address):
                found.append({"name": name, "address": addr.address, "netmask": addr.netmask})
    return sorted(
        found, key=lambda item: (not item["name"].lower().startswith(("wl", "wi-fi", "wifi")), item["name"])
    )
