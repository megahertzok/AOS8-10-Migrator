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
    """Generic HTTP(S) HEAD reachability probe -- confirms a TLS/TCP handshake and an
    HTTP response, nothing about what's actually served at the path. Reused for both
    the firmware-source check (where the caveat is "doesn't confirm the image file
    exists") and the Central-reachability check (where it's just "server responded");
    callers add whichever caveat is relevant in their own detail text/GUI copy rather
    than baking one into this shared helper."""
    scheme = "https" if use_https else "http"
    url = f"{scheme}://{host}/{path.lstrip('/')}" if path else f"{scheme}://{host}/"
    try:
        resp = requests.head(url, timeout=timeout, verify=False, allow_redirects=True)
        return {"reachable": resp.status_code < 500, "status_code": resp.status_code, "detail": f"HTTP HEAD got a response (status {resp.status_code}) -- confirms the server is reachable, not what's served at this path"}
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


def check_central_reachability(central_host=None, timeout=5):
    """Best-effort check that there's a network path to Aruba's cloud onboarding
    services -- a very common real-world migration failure mode distinct from
    firmware delivery: the AP converts successfully but never appears in Central
    because its VLAN can't actually reach Central at all (DNS, outbound HTTPS,
    firewall/proxy rules).

    Important caveat, surfaced in every result: this runs from the *proxy agent's*
    network, not the AP's own VLAN -- if those differ (a management VLAN with a
    different egress path than the AP's client VLAN, for instance), a pass here
    doesn't guarantee the AP itself can reach these hosts, and a fail here doesn't
    necessarily mean the AP can't either. It's a useful signal, not a guarantee --
    the real test is watching the AP actually appear online in Central after
    conversion (see the Verify step).

    Checks device.arubanetworks.com (Aruba's Activate zero-touch provisioning
    service, well-documented as part of the onboarding path) and, if provided, the
    user's own configured Central API Gateway host -- reusing a value already
    entered rather than guessing a region-specific Central hostname.
    """
    targets = {"device.arubanetworks.com (Activate)": "device.arubanetworks.com"}
    if central_host:
        targets[f"{central_host} (your configured Central Gateway)"] = central_host
    results = {}
    for label, host in targets.items():
        results[label] = check_http(host, use_https=True, timeout=timeout)
    return results


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
