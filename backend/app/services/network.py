import io
import sys
import socket
import logging
from typing import List, Optional
from urllib.parse import urlparse
from fastapi import Request
from backend.app.config import settings

logger = logging.getLogger("ffh.network")

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

def lan_ip() -> str:
    """
    Detect the LAN IP with the standard UDP socket trick, sending no packets.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()

def get_ip_candidates() -> List[str]:
    """
    List every non-loopback IPv4 address on the machine.
    Logs a warning if multiple candidate adapters are detected.
    """
    candidates = []
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127.") and ip != "0.0.0.0":
                if ip not in candidates:
                    candidates.append(ip)
    except Exception as e:
        logger.warning(f"Failed to enumerate network adapters via hostname: {e}")

    detected = lan_ip()
    if detected not in LOOPBACK_HOSTS and detected not in candidates:
        candidates.insert(0, detected)

    if len(candidates) > 1:
        logger.warning(
            f"Multiple network adapters / IPv4 addresses detected: {candidates}. "
            f"Using primary LAN IP: {detected}"
        )

    return candidates or [detected]

def get_base_url(request: Optional[Request] = None) -> str:
    """
    Picks the base address for every join link, QR code, and share link in this order:
    1. PUBLIC_BASE_URL from .env, if set and not localhost/loopback.
    2. The host the request arrived on, if it is not localhost, 127.0.0.1, ::1 or 0.0.0.0.
    3. The laptop's detected LAN IP with the server's port.
    """
    # 1. PUBLIC_BASE_URL from settings, if set and not loopback
    configured = settings.PUBLIC_BASE_URL.strip() if settings.PUBLIC_BASE_URL else ""
    if configured:
        try:
            parsed = urlparse(configured if "://" in configured else f"http://{configured}")
            hostname = (parsed.hostname or "").lower()
            if hostname and hostname not in LOOPBACK_HOSTS:
                # Return configured base url without trailing slash
                return configured.rstrip("/")
        except Exception:
            pass

    # 2. Host the request arrived on, if valid and not loopback
    if request:
        try:
            # Header host or request.url
            host_header = request.headers.get("host", "") or request.url.netloc
            if host_header:
                hostname = host_header.split(":")[0].strip().lower()
                if hostname and hostname not in LOOPBACK_HOSTS:
                    scheme = request.url.scheme or "http"
                    return f"{scheme}://{host_header}".rstrip("/")
        except Exception:
            pass

    # 3. Laptop's detected LAN IP with server port
    ip = lan_ip()
    port = settings.PORT
    return f"http://{ip}:{port}"

def print_startup_banner():
    """
    Prints the startup address phones and device should use,
    a terminal QR code, and a warning if bound only to 127.0.0.1.
    """
    ip = lan_ip()
    port = settings.PORT
    base_url = f"http://ip:{port}".replace("ip", ip)

    print("\n" + "=" * 60)
    print("  FarFromHome Blind Trip Platform Server")
    print(f"  Phones and the device can reach this server at:")
    print(f"  >>> {base_url} <<<")
    print("=" * 60)

    if settings.HOST in ("127.0.0.1", "localhost"):
        print("\n  [WARNING] Server is bound to 127.0.0.1!")
        print("  Phones and handheld Raspberry Pi device will NOT be able to connect.")
        print("  Bind to 0.0.0.0 using: --host 0.0.0.0\n")

    # Render Terminal QR Code
    try:
        import qrcode
        qr = qrcode.QRCode(box_size=1, border=1)
        qr.add_data(base_url)
        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=True)
        qr_str = buf.getvalue()
        # Safe print for Windows and non-UTF-8 terminals
        try:
            sys.stdout.buffer.write(qr_str.encode("utf-8"))
            sys.stdout.buffer.flush()
        except Exception:
            print(qr_str)
    except Exception as e:
        logger.debug(f"Terminal QR code generation failed: {e}")

    print("=" * 60 + "\n")
