"""Network diagnostics and the WiFi SSID reader.

Brief 02 §B2 step 4 names the single most valuable line on the lobby screen:

    "Connect your phone to the WiFi: {SSID}"

*"This one line prevents the 'phone is on mobile data' problem."* So reading the
SSID is a product feature, not a debug tool.

This module also produces the human reason when a game session cannot be created
(§B4: *"show the reason from the network diagnostics (WiFi down, laptop
unreachable, server not running)"*) and offers the fixes from §A2.
"""

from __future__ import annotations

import logging
import platform
import re
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ffh.net.diagnostics")


# --------------------------------------------------------------------------- #
# WiFi SSID
# --------------------------------------------------------------------------- #
def current_ssid() -> Optional[str]:
    """Best-effort SSID of the network we are on. ``None`` when unknown.

    Linux (the Pi): ``iwgetid -r``, then ``nmcli``, then ``/proc/net/wireless`` +
    ``iw``. Windows (laptop testing): ``netsh wlan show interfaces``.
    macOS: ``airport -I``.
    """
    system = platform.system()

    if system == "Linux":
        for command in (["iwgetid", "-r"], ["iw", "dev"], ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"]):
            output = _run(command)
            if not output:
                continue
            if command[0] == "nmcli":
                for line in output.splitlines():
                    if line.startswith("yes:"):
                        ssid = line.split(":", 1)[1].strip()
                        if ssid:
                            return ssid
                continue
            text = output.strip()
            if text and command[0] == "iwgetid":
                return text
        return None

    if system == "Windows":
        output = _run(["netsh", "wlan", "show", "interfaces"])
        if output:
            match = re.search(r"^\s*SSID\s*:\s*(.+)$", output, re.MULTILINE)
            if match:
                return match.group(1).strip()
        return None

    if system == "Darwin":
        output = _run(["/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport", "-I"])
        if output:
            match = re.search(r"^\s*SSID:\s*(.+)$", output, re.MULTILINE)
            if match:
                return match.group(1).strip()
    return None


def _run(command: List[str]) -> Optional[str]:
    try:
        completed = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=6.0, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        return completed.stdout.decode("utf-8", errors="ignore")
    except Exception:
        return None


def local_ipv4_addresses() -> List[str]:
    """Every non-loopback IPv4 address on this machine, best effort."""
    addresses: List[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127.") and address not in addresses:
                addresses.append(address)
    except OSError:
        pass
    primary = primary_local_ip()
    if primary and primary not in addresses:
        addresses.insert(0, primary)
    return addresses


def primary_local_ip() -> Optional[str]:
    """The address this machine would use to reach the internet (sends no packets)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #
@dataclass
class DiagnosticCheck:
    name: str
    ok: bool
    detail: str
    fix: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "fix": self.fix}


@dataclass
class Diagnostics:
    checks: List[DiagnosticCheck] = field(default_factory=list)
    ssid: Optional[str] = None
    local_ips: List[str] = field(default_factory=list)
    server_url: str = ""
    server_host: str = ""
    reachable: bool = False
    lan_ip: Optional[str] = None
    port: Optional[int] = None
    base_url: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.reachable

    def reason(self) -> str:
        """A short human reason, for the lobby's failure panel (brief 02 §B4)."""
        if self.reachable:
            return ""
        wifi = next((check for check in self.checks if check.name == "wifi"), None)
        if wifi and not wifi.ok:
            return "This handheld has no network connection."
        dns = next((check for check in self.checks if check.name == "server"), None)
        if dns and not dns.ok:
            return f"Cannot reach the server at {self.server_url}. Is it running?"
        return "The server did not answer."

    def fixes(self) -> List[str]:
        return [check.fix for check in self.checks if not check.ok and check.fix]

    def to_payload(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "reachable": self.reachable,
            "server_url": self.server_url,
            "server_host": self.server_host,
            "ssid": self.ssid,
            "local_ips": self.local_ips,
            "lan_ip": self.lan_ip,
            "port": self.port,
            "base_url": self.base_url,
            "reason": self.reason(),
            "checks": [check.to_dict() for check in self.checks],
            "fixes": self.fixes(),
        }


def run_diagnostics(
    server_url: str,
    *,
    client: Any = None,
    timeout_s: float = 3.0,
) -> Diagnostics:
    """Check the five likely causes from brief 02 §A1, in order."""
    from urllib.parse import urlparse

    parsed = urlparse(server_url if "://" in server_url else f"http://{server_url}")
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    diagnostics = Diagnostics(server_url=server_url, server_host=host)
    diagnostics.ssid = current_ssid()
    diagnostics.local_ips = local_ipv4_addresses()

    # 1. WiFi / link
    has_ip = bool(diagnostics.local_ips)
    diagnostics.checks.append(
        DiagnosticCheck(
            "wifi",
            has_ip,
            f"connected to {diagnostics.ssid}" if diagnostics.ssid else ("has a local IP" if has_ip else "no local IPv4 address"),
            fix="" if has_ip else "Join a WiFi network (or the phone hotspot) from the handheld's settings.",
        )
    )

    # 2. Is the server URL itself loopback? Then phones can never use it.
    #    This is a warning, not a fault: on a laptop talking to a server on the same
    #    machine, localhost is correct. It only breaks the phone-join flow.
    loopback = host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
    diagnostics.checks.append(
        DiagnosticCheck(
            "server_url",
            not loopback,
            f"server_url host is {host}"
            + (" (fine for testing on this machine, but phones cannot use it)" if loopback else ""),
            fix=""
            if not loopback
            else f"To let phones scan the QR code, set SERVER_URL to the laptop's WiFi IP, "
            f"for example http://192.168.1.50:{port}. Plain localhost is fine for a solo test.",
        )
    )

    # 3. TCP reachability
    tcp_ok = False
    tcp_detail = ""
    try:
        with socket.create_connection((host or "127.0.0.1", port), timeout=timeout_s):
            tcp_ok = True
            tcp_detail = f"TCP {host}:{port} is open"
    except OSError as exc:
        tcp_detail = f"TCP {host}:{port} failed: {exc}"
    diagnostics.checks.append(
        DiagnosticCheck(
            "server",
            tcp_ok,
            tcp_detail,
            fix=""
            if tcp_ok
            else f"Start the server with --host 0.0.0.0, and allow port {port} through the "
            f"firewall (netsh advfirewall firewall add rule name=\"FarFromHome {port}\" "
            f"dir=in action=allow protocol=TCP localport={port} profile=private).",
        )
    )

    # 4. HTTP health + the address the server thinks it has
    if tcp_ok and client is not None:
        health = client.health()
        diagnostics.reachable = bool(health and health.ok)
        diagnostics.checks.append(
            DiagnosticCheck(
                "health",
                diagnostics.reachable,
                "GET /api/health answered ok"
                if diagnostics.reachable
                else "GET /api/health did not answer",
                fix="" if diagnostics.reachable else "Check the server logs for a startup error.",
            )
        )
        info = client.network_info() if diagnostics.reachable else None
        if info:
            diagnostics.lan_ip = info.lan_ip
            diagnostics.port = info.port
            diagnostics.base_url = info.base_url
            if info.base_url and info.lan_ip in ("127.0.0.1", "localhost"):
                diagnostics.checks.append(
                    DiagnosticCheck(
                        "public_base_url",
                        False,
                        f"the server's own base_url is {info.base_url}",
                        fix="Set PUBLIC_BASE_URL in the server's .env to its WiFi address so QR links are reachable.",
                    )
                )
    else:
        diagnostics.reachable = False

    # 5. Client isolation: we have an IP, the server answers on TCP, but nothing
    #    else does. Hard to prove, so only hint.
    if tcp_ok and not diagnostics.reachable and has_ip:
        diagnostics.checks.append(
            DiagnosticCheck(
                "isolation",
                False,
                "TCP connects but HTTP does not answer",
                fix="This WiFi may isolate clients. Use a phone hotspot for every device, or a "
                "cloudflared tunnel (cloudflared tunnel --url http://localhost:8888).",
            )
        )

    return diagnostics


def describe_for_screen(diagnostics: Diagnostics) -> Dict[str, Any]:
    """A compact dict the lobby failure panel renders."""
    return {
        "title": "Cannot reach the server" if not diagnostics.ok else "Connected",
        "reason": diagnostics.reason(),
        "fixes": diagnostics.fixes(),
        "ssid": diagnostics.ssid,
        "address": diagnostics.base_url or diagnostics.server_url,
        "checks": [
            {"name": check.name, "ok": check.ok, "detail": check.detail}
            for check in diagnostics.checks
        ],
    }
