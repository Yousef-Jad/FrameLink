import ipaddress
import os
import shutil
import socket
import subprocess
import sys
import psutil


PRIVATE_RANGES = [
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "fc00::/7",
        "fe80::/10",
    )
]


def local_address(address, allow_loopback=False):
    try:
        ip = ipaddress.ip_address(address)
        if ip.is_loopback:
            return allow_loopback
        if ip.is_unspecified or ip.is_multicast:
            return False
        return any(ip in net for net in PRIVATE_RANGES)
    except (ValueError, TypeError):
        return False


HOTSPOT_KEYWORDS = ("wl", "wi-fi", "wifi", "ap", "usb", "rndis", "iphone", "apple", "hotspot")


def interfaces():
    stats = psutil.net_if_stats()
    found = []
    for name, addresses in psutil.net_if_addrs().items():
        if name not in stats or not stats[name].isup or name.startswith(("docker", "veth", "br-", "virbr")):
            continue
        for addr in addresses:
            if addr.family == socket.AF_INET and local_address(addr.address):
                found.append({"name": name, "address": addr.address, "netmask": addr.netmask})
    return sorted(
        found,
        key=lambda item: (
            not any(kw in item["name"].lower() for kw in HOTSPOT_KEYWORDS),
            item["name"],
        ),
    )


def is_address_active(address: str) -> bool:
    if not address:
        return False
    return any(iface["address"] == address for iface in interfaces())


def has_usable_network():
    return len(interfaces()) > 0


def interface_for_address(address):
    for item in interfaces():
        if item["address"] == address:
            return item["name"]
    return None


def network_diagnostics(address=None):
    selected = next((item for item in interfaces() if item["address"] == address), None)
    return {
        "selected": selected,
        "interfaces": interfaces(),
        "private_ipv4": [item["address"] for item in interfaces()],
        "hotspot_candidate": bool(selected and any(
            keyword in selected["name"].lower() for keyword in HOTSPOT_KEYWORDS
        )),
    }


def firewall_diagnostics(port=8443):
    """Read-only firewall hints; never changes firewall policy or rules."""
    result = {"platform": sys.platform, "port": port, "backend": None, "state": "unknown", "guidance": []}
    if sys.platform == "win32":
        result["backend"] = "Windows Defender Firewall"
        result["guidance"] = [
            "Allow inbound UDP for FrameLink on the private network profile and local port range used by WebRTC; do not disable the firewall.",
            "Use Windows Security → Firewall & network protection → Advanced settings and create a narrow private-profile rule only with user approval.",
        ]
        try:
            output = subprocess.check_output(
                ["netsh", "advfirewall", "show", "currentprofile"],
                text=True,
                stderr=subprocess.STDOUT,
                timeout=3,
            )
            result["state"] = "private-profile-visible" if "State" in output else "unknown"
        except (OSError, subprocess.SubprocessError):
            result["state"] = "unavailable"
    elif sys.platform.startswith("linux"):
        for command, args in (
            ("firewall-cmd", ["--state"]),
            ("ufw", ["status"]),
            ("nft", ["list", "ruleset"]),
        ):
            if shutil.which(command):
                result["backend"] = command
                try:
                    output = subprocess.check_output(
                        [command, *args], text=True, stderr=subprocess.STDOUT, timeout=3
                    )
                    result["state"] = output.strip().splitlines()[0][:160] if output.strip() else "empty"
                except (OSError, subprocess.SubprocessError):
                    result["state"] = "restricted-or-unavailable"
                break
        result["guidance"] = [
            f"Permit TCP {port} and the local UDP WebRTC range on the active private interface only; do not flush or disable firewall rules.",
            "If a firewall is active, add a narrow interface/profile rule with administrator approval.",
        ]
    else:
        result["guidance"] = [f"Permit TCP {port} and local UDP WebRTC traffic on the active private interface."]
    return result


def open_system_wifi_settings():
    """Launch the native desktop network / Wi-Fi configuration UI without requiring terminal commands."""
    if sys.platform == "win32":
        try:
            os.startfile("ms-settings:network-wifi")
            return True
        except Exception:
            return False
    elif sys.platform.startswith("linux"):
        # Detect available GUI network manager
        candidates = [
            ["nm-connection-editor"],
            ["gnome-control-center", "wifi"],
            ["kcmshell5", "kcm_networkmanagement"],
            ["systemsettings5", "kcm_networkmanagement"],
        ]
        for cmd in candidates:
            if shutil.which(cmd[0]):
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
                except Exception:
                    pass
        # Fallback: if nmtui is installed and a terminal exists
        for term in ("xdg-terminal-exec", "kitty", "alacritty", "foot", "gnome-terminal", "konsole", "xterm"):
            if shutil.which(term) and shutil.which("nmtui"):
                try:
                    subprocess.Popen(
                        [term, "-e", "nmtui"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    return True
                except Exception:
                    pass
    elif sys.platform == "darwin":
        try:
            subprocess.Popen(["open", "/System/Library/PreferencePanes/Network.prefPane"])
            return True
        except Exception:
            pass
    return False
