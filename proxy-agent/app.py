"""Local proxy agent: bridges the GitHub Pages GUI to on-prem AOS8 controllers and Central.

Run via start_proxy_agent.command (macOS) / start_proxy_agent.bat (Windows), or
`python app.py` directly. Binds to the host/port in config.yaml (127.0.0.1:8765
by default). Nothing here is reachable from the public internet unless you
explicitly change `proxy_agent.host` to 0.0.0.0 in config.yaml.
"""

import re
import uuid
from pathlib import Path

import yaml
from flask import Flask, jsonify, request
from flask_cors import CORS

import debug_log
import firmware_check
import migration_store as store
import ssh_client
from aos8_client import AOS8Client, AOS8Error
from central_client import CentralClient, CentralError

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR.parent / "docs"
CONFIG_PATH = BASE_DIR / "config.yaml"
EXAMPLE_CONFIG_PATH = BASE_DIR / "config.example.yaml"
ENDPOINTS_PATH = BASE_DIR / "endpoints.yaml"


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


config = load_yaml(CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_CONFIG_PATH)
endpoints = load_yaml(ENDPOINTS_PATH)

# The proxy agent also serves the GUI itself (the same docs/ folder GitHub Pages
# hosts), so "Open GUI" always works -- including for purely local use with no
# GitHub Pages setup at all, and same-origin means no CORS entanglement either.
app = Flask(__name__, static_folder=str(DOCS_DIR), static_url_path="")
allowed_origins = config.get("proxy_agent", {}).get("allowed_origins", ["http://localhost:8765"])
CORS(app, origins=allowed_origins)


@app.get("/")
def serve_gui():
    return app.send_static_file("index.html")

store.init_db()
debug_log.enabled = bool(config.get("proxy_agent", {}).get("debug", False))
debug_log.event("Proxy Agent", f"Started -- log file: {debug_log.LOG_PATH}")

# In-memory session registries. Credentials/tokens live only in this process's memory
# for the life of the run -- they are never written to disk unless you add them to
# config.yaml yourself.
aos8_sessions = {}
central_sessions = {}

# Cached topology (list of managed devices from "show switches") per AOS8 session, so
# AP inventory lookups can map each AP's anchor "Switch IP" to that MD's hierarchy
# config_path without re-querying "show switches" on every call. Refreshed whenever
# /api/aos8/topology is hit.
aos8_topology = {}


def _first(d, *keys, default=None):
    """Return the first present key's value -- AOS8 showcommand JSON key casing/naming
    varies by firmware, so field lookups try a few known variants rather than one guess."""
    for key in keys:
        if key in d:
            return d[key]
    return default


def _extract_rows(data, *container_keys):
    """AOS8 showcommand responses wrap the actual row list under a firmware-specific key
    (things like "All Switches", "AP Database", etc). Try known variants, else fall back
    to the first list-of-dicts value found anywhere in the response."""
    for key in container_keys:
        if key in data and isinstance(data[key], list):
            return data[key]
    for value in data.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    return []


# `ap convert` was introduced in ArubaOS 8.6.0.0 -- on older firmware the command
# doesn't exist and conversion attempts fail confusingly. See README "Known gaps".
MIN_AP_CONVERT_VERSION = (8, 6, 0)


def _parse_version(version_str):
    """Best-effort major.minor.patch extraction from a `show switches` "Version"
    string (e.g. "8.10.0.5_88245" or "8.6.0.4"). Returns None if it doesn't parse,
    which callers treat as "unknown", not "fails the check"."""
    if not version_str:
        return None
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", str(version_str))
    if not match:
        return None
    return tuple(int(g) for g in match.groups())


def _meets_min_firmware(version_str, minimum=MIN_AP_CONVERT_VERSION):
    """True/False if the version parses, else None ("unknown, verify manually")."""
    parsed = _parse_version(version_str)
    if parsed is None:
        return None
    return parsed >= minimum


def error_response(exc, status=400):
    debug_log.event("Error", f"({status}) {exc}", level="error")
    return jsonify({"error": str(exc)}), status


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "debug_mode": debug_log.enabled})


@app.get("/api/debug/log")
def debug_get_log():
    since = request.args.get("since", type=float)
    return jsonify({"enabled": debug_log.enabled, "entries": debug_log.get_entries(since)})


@app.post("/api/debug/mode")
def debug_set_mode():
    body = request.get_json(force=True)
    debug_log.enabled = bool(body.get("enabled"))
    if debug_log.enabled:
        debug_log.log("Debug", "Debug mode enabled -- REST calls and SSH commands (never credentials) will be logged here.")
    return jsonify({"enabled": debug_log.enabled})


# ---------------------------------------------------------------------------
# AOS8
# ---------------------------------------------------------------------------


@app.post("/api/aos8/connect")
def aos8_connect():
    body = request.get_json(force=True)
    host = body.get("host")
    username = body.get("username")
    password = body.get("password")
    verify_tls = body.get("verify_tls", config.get("aos8", {}).get("verify_tls", False))
    if not all([host, username, password]):
        return error_response("host, username, and password are required")

    client = AOS8Client(host, verify_tls=verify_tls)
    try:
        client.login(username, password)
    except (AOS8Error, Exception) as exc:  # noqa: BLE001 -- surface any transport error to the GUI
        return error_response(exc, 502)

    session_id = str(uuid.uuid4())
    aos8_sessions[session_id] = client
    debug_log.event("AOS8", f"Connected to {host} as {username}", level="success")
    return jsonify({"session_id": session_id})


def _aos8_client(session_id):
    client = aos8_sessions.get(session_id)
    if not client:
        raise KeyError("Unknown or expired AOS8 session -- reconnect")
    return client


@app.post("/api/aos8/disconnect")
def aos8_disconnect():
    session_id = (request.get_json(force=True) or {}).get("session_id")
    client = aos8_sessions.pop(session_id, None)
    aos8_topology.pop(session_id, None)
    if client:
        try:
            client.logout()
        except Exception:  # noqa: BLE001 -- best-effort cleanup
            pass
    return jsonify({"status": "ok"})


@app.get("/api/aos8/discover")
def aos8_discover():
    try:
        client = _aos8_client(request.args.get("session_id"))
    except KeyError as exc:
        return error_response(exc, 401)
    candidates = endpoints["aos8"].get("discovery_candidates", [])
    return jsonify(client.discover(candidates))


@app.get("/api/aos8/country-code")
def aos8_country_code():
    """Best-effort lookup of the controller's configured regulatory domain / country
    code, for the pre-flight warning that `ap convert` permanently writes this onto
    every AP it converts. Non-fatal on failure -- the GUI still shows a static warning
    even if this specific lookup doesn't work against a given firmware/profile name."""
    session_id = request.args.get("session_id")
    try:
        client = _aos8_client(session_id)
    except KeyError as exc:
        return error_response(exc, 401)
    config_path = request.args.get("config_path", "/mm")
    try:
        data = client.show_command(endpoints["aos8"]["show_regulatory_domain_command"], config_path=config_path)
    except Exception as exc:  # noqa: BLE001 -- best-effort, never blocks the pre-flight step
        return jsonify({"country_code": None, "error": str(exc)})

    rows = _extract_rows(data, "Regulatory Domain Profile", "AP Regulatory Domain Profile")
    row = rows[0] if rows else data
    country_code = _first(row, "Country Code", "country-code", "Country")
    return jsonify({"country_code": country_code, "raw": data if not country_code else None})


@app.get("/api/aos8/topology")
def aos8_topology_view():
    """Discover every Mobility Controller (MD) the Mobility Master manages, via
    `show switches`. This is what lets AP-convert actions later target the right MD's
    hierarchy node instead of the MM itself (which never terminates APs)."""
    session_id = request.args.get("session_id")
    try:
        client = _aos8_client(session_id)
    except KeyError as exc:
        return error_response(exc, 401)
    try:
        data = client.show_command(endpoints["aos8"]["show_switches_command"], config_path="/mm")
    except Exception as exc:  # noqa: BLE001
        return error_response(exc, 502)

    rows = _extract_rows(data, "All Switches", "Switches")
    switches = []
    for row in rows:
        switches.append(
            {
                "name": _first(row, "Name"),
                "ip": _first(row, "IP Address", "IPAddress"),
                "location": _first(row, "Location"),
                "type": _first(row, "Type"),
                "status": _first(row, "Status"),
                "model": _first(row, "Model"),
                "version": _first(row, "Version"),
                # True/False if we could parse the version and compare it to the
                # 8.6.0.0 minimum `ap convert` requires; None if the version string
                # didn't parse -- the GUI treats that as "verify manually", not a pass.
                "firmware_ok": _meets_min_firmware(_first(row, "Version")),
            }
        )
    aos8_topology[session_id] = switches
    return jsonify({"raw": data, "switches": switches})


def _md_config_path_for_ip(session_id, switch_ip):
    """Map an AP's anchor "Switch IP" (from show ap database) to that MD's hierarchy
    config_path (the "Location" field from show switches), using the cached topology.
    Falls back to "/md" if topology hasn't been fetched yet or the IP isn't found --
    call GET /api/aos8/topology first for accurate per-MD targeting."""
    for switch in aos8_topology.get(session_id, []):
        if switch.get("ip") == switch_ip and switch.get("location"):
            return switch["location"], switch.get("name")
    return "/md", None


@app.get("/api/aos8/aps")
def aos8_aps():
    session_id = request.args.get("session_id")
    try:
        client = _aos8_client(session_id)
    except KeyError as exc:
        return error_response(exc, 401)
    try:
        data = client.show_command(endpoints["aos8"]["show_ap_database_command"], config_path="/mm")
    except Exception as exc:  # noqa: BLE001
        return error_response(exc, 502)

    # Best-effort normalization -- AOS8's showcommand JSON key names vary by firmware;
    # _first()/_extract_rows() try known variants. Adjust once confirmed against a
    # real controller (see README "Mobility Master / Mobility Controller hierarchy").
    rows = _extract_rows(data, "AP Database", "APs")
    for row in rows:
        mac = _first(row, "Wired MAC Address", "AP Wired MAC Address", "MAC Address")
        if not mac:
            continue
        switch_ip = _first(row, "Switch IP", "Switch IP Address")
        md_config_path, md_name = _md_config_path_for_ip(session_id, switch_ip) if switch_ip else ("/md", None)
        store.upsert_ap(
            mac,
            name=_first(row, "Name", "AP Name"),
            ap_group=_first(row, "Group", "AP Group"),
            md_ip=switch_ip,
            md_name=md_name,
            md_config_path=md_config_path,
            serial=_first(row, "AP Serial #", "Serial #", "Serial"),
            # The AP's OWN management IP -- distinct from switch_ip (its anchor MD's IP).
            # This is what rollback SSHes into. Note it can go stale: once an AP converts
            # to AOS10 it gets a new DHCP lease, so this pre-migration value may no longer
            # be current -- the rollback flow re-checks Central's inventory first when a
            # Central session is available (see docs/assets/app.js rollback flow).
            ap_ip=_first(row, "IP Address", "AP IP Address"),
        )
    return jsonify({"raw": data, "tracked": store.list_aps()})


def _convert_action(action_key, session_id, groups, build_payload):
    """Run a config-object POST once per (config_path, ap_names) group, since a batch of
    selected APs can legitimately span multiple MDs and each action has to land on the
    AP's own anchor MD, not the Mobility Master."""
    try:
        client = _aos8_client(session_id)
    except KeyError as exc:
        return error_response(exc, 401)
    results = []
    for group in groups:
        config_path = group.get("config_path") or "/md"
        ap_names = group.get("ap_names", [])
        try:
            result = client.post_path(endpoints["aos8"][action_key], build_payload(ap_names), config_path=config_path)
            results.append({"config_path": config_path, "ap_names": ap_names, "result": result})
            debug_log.event(
                "Convert", f"{action_key} @ {config_path} succeeded for {len(ap_names)} AP(s): {ap_names}", level="success"
            )
        except Exception as exc:  # noqa: BLE001
            results.append({"config_path": config_path, "ap_names": ap_names, "error": str(exc)})
            debug_log.event("Convert", f"{action_key} @ {config_path} FAILED for {ap_names}: {exc}", level="error")
    return jsonify({"groups": results})


def _groups_from_body(body):
    """Accept either pre-grouped {config_path, ap_names} batches (preferred -- lets the
    GUI split a multi-MD selection automatically) or a flat ap_names/config_path pair
    for simple single-controller use."""
    groups = body.get("groups")
    if groups:
        return groups
    return [{"config_path": body.get("config_path", "/md"), "ap_names": body.get("ap_names", [])}]


@app.post("/api/aos8/convert/add")
def convert_add():
    body = request.get_json(force=True)
    groups = _groups_from_body(body)
    for group in groups:
        for mac in group.get("ap_names", []):
            store.upsert_ap(mac, state="discovered", md_config_path=group.get("config_path"))
    return _convert_action("ap_convert_add", body.get("session_id"), groups, lambda ap_names: {"ap_names": ap_names})


@app.post("/api/aos8/convert/prevalidate")
def convert_prevalidate():
    body = request.get_json(force=True)
    groups = _groups_from_body(body)
    for group in groups:
        for mac in group.get("ap_names", []):
            store.set_state(mac, "pre_validated")
    return _convert_action("ap_convert_prevalidate", body.get("session_id"), groups, lambda ap_names: {})


@app.post("/api/aos8/convert/execute")
def convert_execute():
    """Trigger `ap convert active`. Firmware delivery is either `local-flash <image>`
    or `server {ftp|tftp|http|https|scp} ... <image>` per HPE's docs -- both shapes are
    sent through in the payload since the exact REST object schema is UNVERIFIED."""
    body = request.get_json(force=True)
    groups = _groups_from_body(body)
    firmware = body.get("firmware", {})
    for group in groups:
        for mac in group.get("ap_names", []):
            store.set_state(mac, "converting")

    def build_payload(ap_names):
        payload = {"ap_names": ap_names, "mode": "specific-aps", "filename": firmware.get("filename")}
        if firmware.get("server_type") == "local-flash":
            payload["local_flash"] = firmware.get("filename")
        else:
            payload["server"] = {
                "type": firmware.get("server_type"),
                "host": firmware.get("host"),
                "port": firmware.get("port"),
                "path": firmware.get("path"),
                "username": firmware.get("username"),
                "password": firmware.get("password"),
            }
        return payload

    return _convert_action("ap_convert_active", body.get("session_id"), groups, build_payload)


@app.post("/api/aos8/convert/cancel")
def convert_cancel():
    body = request.get_json(force=True)
    groups = _groups_from_body(body)
    for group in groups:
        for mac in group.get("ap_names", []):
            store.set_state(mac, "rolled_back", notes="Cancelled in-flight conversion")
    return _convert_action("ap_convert_cancel", body.get("session_id"), groups, lambda ap_names: {})


@app.get("/api/aos8/convert/status")
def convert_status():
    session_id = request.args.get("session_id")
    try:
        client = _aos8_client(session_id)
    except KeyError as exc:
        return error_response(exc, 401)
    config_path = request.args.get("config_path", "/md")
    try:
        data = client.show_command(endpoints["aos8"]["ap_convert_status_command"], config_path=config_path)
    except Exception as exc:  # noqa: BLE001
        return error_response(exc, 502)
    return jsonify(data)


@app.post("/api/aos8/firmware-check")
def aos8_firmware_check():
    """Best-effort pre-flight check that the chosen firmware source is reachable
    before running `ap convert active`. Does not confirm the exact image file exists
    (AOS8 has no documented API for that) -- see firmware_check.py for what each
    server type's check actually verifies."""
    body = request.get_json(force=True)
    server_type = (body.get("server_type") or "").lower()

    if server_type == "local-flash":
        session_id = body.get("session_id")
        config_path = body.get("config_path", "/md")
        try:
            client = _aos8_client(session_id)
        except KeyError as exc:
            return error_response(exc, 401)
        try:
            data = client.show_command(endpoints["aos8"]["show_storage_command"], config_path=config_path)
        except Exception as exc:  # noqa: BLE001
            return error_response(exc, 502)
        return jsonify({"reachable": True, "detail": "Controller responded to a storage listing -- review it below to confirm the image filename is present", "raw": data})

    result = firmware_check.check_firmware_source(
        server_type,
        host=body.get("host"),
        filename=body.get("filename"),
        path=body.get("path"),
        port=body.get("port"),
    )
    return jsonify(result)


@app.post("/api/aos8/rollback")
def aos8_rollback_to_campus():
    """Revert a fully-converted AP back to Campus/AOS8 mode.

    Per HPE's documented procedure, this is issued on the AP's own console -- the
    proxy agent SSHes directly to the AP's management IP and runs
    `convert-aos-ap cap <controller-address>`. Before that, if an AOS8 session and the
    AP's anchor-MD config_path are supplied, any pending conversion job on that MD is
    cancelled first (a documented prerequisite -- a pending job would otherwise try to
    re-convert the AP back to AOS10).
    """
    body = request.get_json(force=True)
    ap_host = body.get("ap_host")
    ap_username = body.get("ap_username")
    ap_password = body.get("ap_password")
    controller_address = body.get("controller_address")
    mac = body.get("mac")
    if not all([ap_host, ap_username, ap_password, controller_address]):
        return error_response("ap_host, ap_username, ap_password, and controller_address are required")

    precancel_result = None
    aos8_session_id = body.get("aos8_session_id")
    config_path = body.get("config_path")
    if aos8_session_id and config_path:
        try:
            client = _aos8_client(aos8_session_id)
            status = client.show_command(endpoints["aos8"]["ap_convert_status_command"], config_path=config_path)
            status_rows = _extract_rows(status, "Convert Status", "AP Convert Status")
            if any(str(_first(row, "Upgrade Status", "Status", default="")).lower() == "active" for row in status_rows):
                precancel_result = client.post_path(endpoints["aos8"]["ap_convert_cancel"], {}, config_path=config_path)
        except Exception as exc:  # noqa: BLE001 -- best-effort; still attempt the SSH revert
            precancel_result = {"error": str(exc)}

    try:
        result = ssh_client.revert_to_campus_ap(ap_host, ap_username, ap_password, controller_address)
    except ssh_client.SSHCommandError as exc:
        if mac:
            store.set_state(mac, "failed", notes=f"Rollback SSH failed: {exc}")
        return error_response(exc, 502)

    if mac:
        store.set_state(mac, "rolled_back", notes=f"Reverted via SSH: convert-aos-ap cap {controller_address}")
    debug_log.event("Rollback", f"{mac or ap_host}: reverted to Campus AP via {ap_host} -> {controller_address}", level="success")
    return jsonify({"ssh_result": result, "precancel": precancel_result})


# ---------------------------------------------------------------------------
# Central
# ---------------------------------------------------------------------------


@app.post("/api/central/connect")
def central_connect():
    body = request.get_json(force=True)
    base_url = body.get("base_url") or config.get("central", {}).get("default_base_url")
    client_id = body.get("client_id")
    client_secret = body.get("client_secret")
    access_token = body.get("access_token")
    refresh_token = body.get("refresh_token")
    if not all([base_url, client_id, client_secret, access_token]):
        return error_response("base_url, client_id, client_secret, and access_token are required")

    client = CentralClient(base_url, client_id, client_secret, access_token, refresh_token)
    session_id = str(uuid.uuid4())
    central_sessions[session_id] = client
    debug_log.event("Central", f"Connected via {base_url}", level="success")
    return jsonify({"session_id": session_id})


def _central_client(session_id):
    client = central_sessions.get(session_id)
    if not client:
        raise KeyError("Unknown or expired Central session -- reconnect")
    return client


@app.post("/api/central/disconnect")
def central_disconnect():
    session_id = (request.get_json(force=True) or {}).get("session_id")
    central_sessions.pop(session_id, None)
    return jsonify({"status": "ok"})


@app.get("/api/central/sites")
def central_sites():
    try:
        client = _central_client(request.args.get("session_id"))
    except KeyError as exc:
        return error_response(exc, 401)
    try:
        data = client.list_sites(endpoints["central"]["sites"])
    except Exception as exc:  # noqa: BLE001
        return error_response(exc, 502)
    return jsonify(data)


@app.get("/api/central/devices")
def central_devices():
    try:
        client = _central_client(request.args.get("session_id"))
    except KeyError as exc:
        return error_response(exc, 401)
    params = {k: v for k, v in request.args.items() if k != "session_id"}
    try:
        data = client.list_devices(endpoints["central"]["devices"], params)
    except Exception as exc:  # noqa: BLE001
        return error_response(exc, 502)
    return jsonify(data)


@app.post("/api/central/devices/assign-site")
def central_devices_assign_site():
    body = request.get_json(force=True)
    serials = body.get("serials", [])
    site_id = body.get("site_id")
    device_type = body.get("device_type", "IAP")
    macs = body.get("macs", [])
    try:
        client = _central_client(body.get("session_id"))
    except KeyError as exc:
        return error_response(exc, 401)
    if not serials or site_id is None:
        return error_response("serials and site_id are required")
    try:
        results = client.associate_devices_to_site(endpoints["central"]["sites_associate"], serials, site_id, device_type)
    except Exception as exc:  # noqa: BLE001
        return error_response(exc, 502)
    ok_serials = {r["serial"] for r in results if "error" not in r}
    for mac, serial in zip(macs, serials):
        if serial in ok_serials:
            store.upsert_ap(mac, state="assigned_to_site", notes=f"Assigned to site {site_id}", central_site_id=site_id)
    failed = len(results) - len(ok_serials)
    debug_log.event(
        "Site Assignment", f"{len(ok_serials)} of {len(results)} AP(s) assigned to site {site_id}",
        level="success" if not failed else "warning",
    )
    return jsonify({"results": results})


@app.post("/api/central/verify")
def central_verify():
    """Post-migration verification: confirm a device is online in Central, its name
    matches what was recorded before migration, and (if assigned) it's on the intended
    site. Central's device inventory field names are UNVERIFIED beyond what
    aruba/central-automation-studio's own JS showed (serial, macaddr, name, site,
    group_name) -- _first() tries a few likely variants for status."""
    body = request.get_json(force=True)
    serial = body.get("serial")
    expected_name = body.get("expected_name")
    expected_site_id = body.get("expected_site_id")
    try:
        client = _central_client(body.get("session_id"))
    except KeyError as exc:
        return error_response(exc, 401)
    if not serial:
        return error_response("serial is required")
    try:
        data = client.list_devices(endpoints["central"]["devices"], {"serial": serial})
    except Exception as exc:  # noqa: BLE001
        return error_response(exc, 502)

    devices = _extract_rows(data, "devices", "result") or ([data] if isinstance(data, dict) and _first(data, "serial") else [])
    match = next((d for d in devices if _first(d, "serial", "sn") == serial), None)
    if not match:
        return jsonify({"found": False, "online": False, "name_match": False, "site_match": False})

    actual_name = _first(match, "name", "hostname")
    actual_site = _first(match, "site", "site_name", "site_id")
    status = str(_first(match, "status", "state", default="")).lower()
    return jsonify(
        {
            "found": True,
            "online": status in ("up", "online", "connected"),
            "status": status,
            # Current IP as Central sees it -- more trustworthy than the pre-migration
            # ap_ip in the tracking store for reaching an already-converted AP over SSH,
            # since the AP typically gets a new DHCP lease after converting to AOS10.
            "current_ip": _first(match, "ip_address", "ip"),
            "actual_name": actual_name,
            "name_match": (expected_name is None) or (actual_name == expected_name),
            "actual_site": actual_site,
            "site_match": (expected_site_id is None) or (str(actual_site) == str(expected_site_id)),
        }
    )


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------


@app.get("/api/tracking")
def tracking_list():
    return jsonify(store.list_aps(state=request.args.get("state"), ap_group=request.args.get("ap_group")))


@app.post("/api/tracking/import")
def tracking_import():
    """Bulk-import AP rows from a CSV parsed client-side (docs/assets/app.js) -- lets
    the AP database be seeded/planned offline without a live AOS8 session, per your
    CSV workflow request. Rows use the same field names as the live discovery flow."""
    body = request.get_json(force=True)
    rows = body.get("rows", [])
    if not isinstance(rows, list):
        return error_response("rows must be a list of objects")
    result = store.import_rows(rows)
    debug_log.event(
        "CSV Import", f"Imported {result['imported']} AP(s), {len(result['errors'])} row(s) skipped",
        level="success" if not result["errors"] else "warning",
    )
    return jsonify(result)


@app.post("/api/tracking/rollback-scope")
def tracking_rollback_scope():
    """Expand a rollback scope (single AP, AOS8 AP-group, or Central site) into the
    concrete list of tracked APs it covers, for the GUI to loop per-AP SSH rollback
    over. Group/site rollback is a GUI convenience -- the underlying AOS device
    operation is always per-AP (see README)."""
    body = request.get_json(force=True)
    scope = body.get("scope")
    value = body.get("value")
    if scope == "ap":
        ap = store.get_ap(value)
        return jsonify([ap] if ap else [])
    if scope == "group":
        return jsonify(store.list_by_group(value))
    if scope == "site":
        return jsonify(store.list_by_site(value))
    return error_response("scope must be one of: ap, group, site")


# NOTE: this catch-all <mac> route must stay registered AFTER the literal
# /api/tracking/import and /api/tracking/rollback-scope routes above, or Flask/Werkzeug
# matches those paths as mac="import" / mac="rollback-scope" instead.
@app.post("/api/tracking/<mac>")
def tracking_update(mac):
    body = request.get_json(force=True)
    store.upsert_ap(
        mac,
        name=body.get("name"),
        ap_group=body.get("ap_group"),
        state=body.get("state"),
        notes=body.get("notes"),
    )
    return jsonify(store.get_ap(mac))


if __name__ == "__main__":
    agent_config = config.get("proxy_agent", {})
    app.run(host=agent_config.get("host", "127.0.0.1"), port=agent_config.get("port", 8765), debug=False)
