"""QR and join-URL tests.

Brief 02 §B3 is a precise specification, and this is the single most common reason
"the QR does not work" (brief 02 §A1 cause 1). So each rule gets an assertion:

* use the server's ``join_url`` **unless** its host is localhost/127.0.0.1/0.0.0.0;
* in that case rebuild it from the server URL the handheld is actually using;
* draw the QR locally, error correction level M, border of at least 4 modules;
* always have a plain address and a code to read out loud.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from farfromhome_device.net.qr import (
    BORDER_MODULES,
    ERROR_CORRECTION,
    JoinTarget,
    is_loopback_url,
    lobby_instructions,
    qr_matrix,
    qr_png_bytes,
    resolve_join_url,
    save_qr_png,
)

SERVER = "http://192.168.1.50:8000"


# --------------------------------------------------------------------------- #
# loopback detection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000/g/ABC",
        "http://127.0.0.1:8000/g/ABC",
        "http://0.0.0.0:8000/g/ABC",
        "http://[::1]:8000/g/ABC",
        "http://127.0.1.1:8000/g/ABC",
        "",
        None,
        "not a url",
    ],
)
def test_loopback_urls_are_detected(url: Any) -> None:
    assert is_loopback_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.50:8000/g/ABC",
        "https://random-words.trycloudflare.com/g/ABC",
        "http://10.0.0.7:8000/g/ABC",
        "http://gizmo.local:8000/g/ABC",
    ],
)
def test_reachable_urls_are_not_flagged(url: str) -> None:
    assert is_loopback_url(url) is False


# --------------------------------------------------------------------------- #
# the rule from brief 02 §A1 and §B3
# --------------------------------------------------------------------------- #
def test_a_good_server_url_is_used_as_is() -> None:
    target = resolve_join_url("http://192.168.1.50:8000/g/QX7K2", "QX7K2", SERVER)
    assert target.url == "http://192.168.1.50:8000/g/QX7K2"
    assert target.rewrote_localhost is False
    assert target.source == "server"
    assert target.address == target.url


def test_a_localhost_join_url_is_rebuilt_from_the_server_url() -> None:
    target = resolve_join_url("http://localhost:8000/g/QX7K2", "QX7K2", SERVER)
    assert target.url == "http://192.168.1.50:8000/g/QX7K2"
    assert target.rewrote_localhost is True
    assert "localhost" not in target.url


def test_a_127_join_url_is_rebuilt() -> None:
    target = resolve_join_url("http://127.0.0.1:8000/g/ABCDE", "ABCDE", SERVER)
    assert target.url == "http://192.168.1.50:8000/g/ABCDE"


def test_a_missing_join_url_is_built_from_the_server_url() -> None:
    target = resolve_join_url(None, "ZZZZZ", SERVER)
    assert target.url == "http://192.168.1.50:8000/g/ZZZZZ"
    assert target.source == "rebuilt"


def test_network_info_is_preferred_over_the_server_url_when_it_is_usable() -> None:
    target = resolve_join_url(
        "http://localhost:8000/g/QQQQQ",
        "QQQQQ",
        SERVER,
        network_base_url="http://10.0.0.9:8000",
    )
    assert target.url == "http://10.0.0.9:8000/g/QQQQQ"


def test_a_loopback_network_info_is_ignored() -> None:
    target = resolve_join_url(
        "http://localhost:8000/g/QQQQQ",
        "QQQQQ",
        SERVER,
        network_base_url="http://127.0.0.1:8000",
    )
    assert target.url == "http://192.168.1.50:8000/g/QQQQQ"


def test_https_is_preserved_when_rebuilding() -> None:
    target = resolve_join_url("http://localhost:8000/g/TT", "TT", "https://abc.trycloudflare.com")
    assert target.url == "https://abc.trycloudflare.com/g/TT"


def test_default_ports_are_not_printed() -> None:
    target = resolve_join_url(None, "P1", "http://192.168.1.50")
    assert target.url == "http://192.168.1.50/g/P1"
    secure = resolve_join_url(None, "P2", "https://example.com")
    assert secure.url == "https://example.com/g/P2"


def test_a_non_default_port_is_printed() -> None:
    target = resolve_join_url(None, "P3", "http://192.168.1.50:8080")
    assert target.url == "http://192.168.1.50:8080/g/P3"


def test_the_code_survives_a_rebuild() -> None:
    target = resolve_join_url("http://localhost:8000/g/K7P2Q", "K7P2Q", SERVER)
    assert target.code == "K7P2Q"
    assert target.url.endswith("/g/K7P2Q")


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def test_qr_png_is_a_png() -> None:
    data = qr_png_bytes("http://192.168.1.50:8000/g/QX7K2")
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_qr_png_has_a_reasonable_size() -> None:
    data = qr_png_bytes("http://192.168.1.50:8000/g/QX7K2", target_px=560)
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        assert image.width == image.height
        assert image.width >= 200, "the QR must be big enough to scan off a small screen"


def test_error_correction_level_is_m() -> None:
    """Brief 02 §B3: "Use error correction level M"."""
    import qrcode

    assert ERROR_CORRECTION == "M"
    assert qrcode.constants.ERROR_CORRECT_M == 0  # the constant qrcode uses for M


def test_border_is_at_least_four_modules() -> None:
    """Brief 02 §B3: "a white margin of at least 4 modules"."""
    assert BORDER_MODULES >= 4


def test_qr_matrix_has_the_quiet_zone() -> None:
    matrix = qr_matrix("http://192.168.1.50:8000/g/QX7K2", border=4)
    assert len(matrix) == len(matrix[0])
    # The outer rows and columns of the quiet zone must be light.
    assert not any(matrix[0])
    assert not any(matrix[-1])
    assert not any(row[0] for row in matrix)
    assert not any(row[-1] for row in matrix)


def test_longer_data_makes_a_bigger_matrix() -> None:
    small = qr_matrix("http://a/g/AA")
    large = qr_matrix("https://a-very-long-tunnel-name.trycloudflare.com/g/QX7K2ABCDE")
    assert len(large) > len(small)


def test_qr_png_rejects_empty_data() -> None:
    with pytest.raises(ValueError):
        qr_png_bytes("")


def test_save_qr_png_writes_a_file(tmp_path: Any) -> None:
    path = save_qr_png("http://192.168.1.50:8000/g/QX7K2", tmp_path / "qr.png")
    assert path.is_file()
    assert path.read_bytes()[:4] == b"\x89PNG"


# --------------------------------------------------------------------------- #
# the lobby text block
# --------------------------------------------------------------------------- #
def test_lobby_instructions_include_the_code_address_and_wifi_hint() -> None:
    target = JoinTarget(
        url="http://192.168.1.50:8000/g/QX7K2",
        code="QX7K2",
        address="http://192.168.1.50:8000/g/QX7K2",
        rewrote_localhost=False,
        server_host="192.168.1.50",
        source="server",
    )
    instructions = lobby_instructions(target, wifi_ssid="VenueWiFi")
    assert "QX7K2" in instructions["code"]
    assert instructions["address"].startswith("http://192.168.1.50")
    assert "VenueWiFi" in instructions["wifi"]
    assert "same WiFi" in instructions["hint"]


def test_lobby_instructions_work_without_an_ssid() -> None:
    target = JoinTarget(
        url="http://192.168.1.50:8000/g/A",
        code="A",
        address="http://192.168.1.50:8000/g/A",
        rewrote_localhost=False,
        server_host="192.168.1.50",
        source="server",
    )
    instructions = lobby_instructions(target)
    assert "wifi" not in instructions
    assert instructions["hint"]
