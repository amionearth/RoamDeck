"""QR codes, drawn locally. Brief 02 §B3 is the specification.

Rules from the brief, implemented here:

* **Draw locally** with the ``qrcode`` package. Never depend on an online QR
  service (it would fail exactly when the WiFi is the problem).
* **Use the server's ``join_url`` unless its host is ``localhost``, ``127.0.0.1``
  or ``0.0.0.0``.** In that case rebuild it as
  ``{scheme}://{host:port of the SERVER_URL the handheld is actually connected to}/g/{code}``.
  The handheld can reach that address, so a phone on the same WiFi can too. This
  is the single most common reason "the QR does not work": the server put its own
  ``localhost`` in the URL because the handheld called it over loopback.
* Error correction level **M**, as large as the screen allows, white margin of at
  least 4 modules.
* Always show the plain address and the code under the QR.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("ffh.net.qr")

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "127.0.1.1", ""}

#: Brief 02 §B3: error correction level M, quiet zone >= 4 modules.
ERROR_CORRECTION = "M"
BORDER_MODULES = 4


@dataclass
class JoinTarget:
    """Everything the lobby screen needs to show and encode."""

    url: str
    code: str
    address: str  # the plain text a human can type
    rewrote_localhost: bool
    server_host: str
    source: str  # "server" | "rebuilt" | "local"

    def to_payload(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "code": self.code,
            "address": self.address,
            "rewrote_localhost": self.rewrote_localhost,
            "server_host": self.server_host,
            "source": self.source,
        }


def is_loopback_url(url: Optional[str]) -> bool:
    if not url:
        return True
    try:
        parsed = urlparse(url)
    except ValueError:
        return True
    host = (parsed.hostname or "").lower()
    return host in LOOPBACK_HOSTS


def _split_host_port(server_url: str) -> Tuple[str, str, Optional[int]]:
    parsed = urlparse(server_url if "://" in server_url else f"http://{server_url}")
    scheme = parsed.scheme or "http"
    host = parsed.hostname or ""
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host, port


def resolve_join_url(
    join_url: Optional[str],
    code: str,
    server_url: str,
    network_base_url: Optional[str] = None,
) -> JoinTarget:
    """Pick the address that will actually open on a phone.

    Order of preference:

    1. The server's ``join_url`` when its host is reachable (not loopback).
    2. ``GET /api/network-info``'s ``base_url``, when it is not loopback.
    3. ``{scheme}://{host:port of server_url}/g/{code}``.
    """
    scheme, host, port = _split_host_port(server_url)
    default_port = 443 if scheme == "https" else 80
    port_suffix = "" if port == default_port else f":{port}"
    fallback = f"{scheme}://{host}{port_suffix}/g/{code}"

    if join_url and not is_loopback_url(join_url):
        parsed = urlparse(join_url)
        address = join_url
        return JoinTarget(
            url=join_url,
            code=code,
            address=address,
            rewrote_localhost=False,
            server_host=parsed.hostname or host,
            source="server",
        )

    if network_base_url and not is_loopback_url(network_base_url):
        base = network_base_url.rstrip("/")
        rebuilt = f"{base}/g/{code}"
        return JoinTarget(
            url=rebuilt,
            code=code,
            address=rebuilt,
            rewrote_localhost=True,
            server_host=host,
            source="rebuilt",
        )

    logger.warning(
        "server sent a loopback join_url (%s); rebuilding from SERVER_URL (%s)",
        join_url, server_url,
    )
    return JoinTarget(
        url=fallback,
        code=code,
        address=fallback,
        rewrote_localhost=True,
        server_host=host,
        source="rebuilt",
    )


def qr_png_bytes(
    data: str,
    box_size: Optional[int] = None,
    target_px: int = 560,
    border: int = BORDER_MODULES,
) -> bytes:
    """Render a QR as PNG bytes.

    ``target_px`` is the wanted width in pixels; ``box_size`` is derived so the
    code fills the space on the panel. Rendering locally means the lobby screen
    works with the server down and the WiFi half-broken.
    """
    import qrcode

    if not data:
        raise ValueError("qr_png_bytes needs data")

    # Measure the module count first so the box size can be chosen to fill.
    probe = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=border)
    probe.add_data(data)
    probe.make(fit=True)
    modules = probe.modules_count + 2 * border
    if box_size is None:
        box_size = max(4, int(target_px // max(1, modules)))

    code = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    code.add_data(data)
    code.make(fit=True)
    image = code.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def qr_matrix(data: str, border: int = BORDER_MODULES) -> list[list[bool]]:
    """The QR as a boolean matrix, for drawing on a raw framebuffer (no PIL)."""
    import qrcode

    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=border)
    code.add_data(data)
    code.make(fit=True)
    return [[bool(cell) for cell in row] for row in code.get_matrix()]


def save_qr_png(data: str, path: Any, target_px: int = 560) -> Any:
    from pathlib import Path

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(qr_png_bytes(data, target_px=target_px))
    return out


def lobby_instructions(target: JoinTarget, wifi_ssid: str = "") -> Dict[str, str]:
    """The text block that goes under the QR (brief 02 §B2 step 4 and §B3)."""
    instructions = {
        "headline": "Scan to join",
        "code": f"Join code: {target.code}",
        "address": target.address,
        "hint": "Phones must be on the same WiFi as this handheld.",
    }
    if wifi_ssid:
        instructions["wifi"] = f"Connect your phone to the WiFi: {wifi_ssid}"
    return instructions
