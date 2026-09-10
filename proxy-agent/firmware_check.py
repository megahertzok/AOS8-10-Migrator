"""Best-effort firmware-source reachability checks, run by the proxy agent itself.

AOS8 doesn't expose a documented "does this file exist on my TFTP server" API, so these
checks confirm the *server is reachable*, not that the specific image file is present --
that distinction is surfaced in every result so it's never mistaken for a guarantee.
"""

import socket
import struct
import time

import requests


def check_http(host, path="", timeout=5, use_https=False):
    scheme = "https" if use_https else "http"
    url = f"{scheme}://{host}/{path.lstrip('/')}" if path else f"{scheme}://{host}/"
    try:
        resp = requests.head(url, timeout=timeout, verify=False, allow_redirects=True)
        return {"reachable": resp.status_code < 500, "status_code": resp.status_code, "detail": "HTTP HEAD reachability check only -- does not confirm the exact image file exists"}
    except requests.RequestException as exc:
        return {"reachable": False, "error": str(exc)}


def check_tcp(host, port, timeout=5):
    """Plain TCP connect -- used for ftp/scp where a lightweight protocol-specific probe
    isn't worth the complexity. Confirms the port is open, nothing about file presence."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"reachable": True, "detail": f"TCP port {port} accepted a connection"}
    except OSError as exc:
        return {"reachable": False, "error": str(exc)}


def check_tftp(host, filename, timeout=5, port=69):
    """Send a single TFTP read request (RRQ) and see whether the server replies with a
    DATA or ERROR packet (both mean "server is there and responding") vs a timeout
    (server unreachable). Does not download the file."""
    sock = None
    try:
        rrq = b"\x00\x01" + filename.encode() + b"\x00octet\x00"
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        start = time.time()
        sock.sendto(rrq, (host, port))
        data, _ = sock.recvfrom(516)
        elapsed = time.time() - start
        opcode = struct.unpack("!H", data[0:2])[0]
        if opcode == 3:  # DATA
            return {"reachable": True, "detail": f"TFTP server responded with DATA in {elapsed:.2f}s -- file appears to exist"}
        if opcode == 5:  # ERROR
            err_msg = data[4:].split(b"\x00")[0].decode(errors="replace")
            return {"reachable": True, "detail": f"TFTP server responded (ERROR: {err_msg}) -- server is up, but check the filename/path"}
        return {"reachable": True, "detail": f"TFTP server responded with unexpected opcode {opcode}"}
    except socket.timeout:
        return {"reachable": False, "error": "No response from TFTP server (timeout)"}
    except OSError as exc:
        return {"reachable": False, "error": str(exc)}
    finally:
        if sock is not None:
            sock.close()


def check_firmware_source(server_type, host, filename=None, path=None, port=None):
    """Dispatch to the right probe based on the `ap convert active` server type."""
    server_type = (server_type or "").lower()
    if server_type in ("http", "https"):
        return check_http(host, path or "", use_https=(server_type == "https"))
    if server_type == "tftp":
        if not filename:
            return {"reachable": False, "error": "filename is required for a TFTP check"}
        return check_tftp(host, filename, port=port or 69)
    if server_type in ("ftp", "scp"):
        return check_tcp(host, port or (21 if server_type == "ftp" else 22))
    return {"reachable": False, "error": f"Unsupported server_type: {server_type!r} (expected http, https, tftp, ftp, or scp)"}
