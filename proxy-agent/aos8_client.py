"""Client for the AOS8 REST API (session-based auth, config-object endpoints).

Reference: https://developer.arubanetworks.com/aos8/docs/getting-started-aos8-restapi
"""

import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning

import debug_log


class AOS8Error(Exception):
    pass


class AOS8Client:
    def __init__(self, host, port=4343, verify_tls=False, timeout=15):
        self.base_url = f"https://{host}:{port}"
        self.verify_tls = verify_tls
        self.timeout = timeout
        self.session = requests.Session()
        self.uidaruba = None
        if not verify_tls:
            urllib3.disable_warnings(InsecureRequestWarning)

    def login(self, username, password):
        debug_log.log("AOS8", f"POST {self.base_url}/v1/api/login  username={username}  password=***")
        resp = self.session.post(
            f"{self.base_url}/v1/api/login",
            data={"username": username, "password": password},
            verify=self.verify_tls,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("_global_result", {})
        if str(result.get("status")) != "0":
            raise AOS8Error(result.get("status_str", "AOS8 login failed"))
        self.uidaruba = result.get("UIDARUBA")
        if not self.uidaruba:
            raise AOS8Error("AOS8 login succeeded but no UIDARUBA token was returned")
        return self.uidaruba

    def logout(self):
        if not self.uidaruba:
            return
        try:
            self._get("/v1/api/logout")
        finally:
            self.uidaruba = None

    def _params(self, config_path="/md", extra=None):
        params = {"config_path": config_path, "UIDARUBA": self.uidaruba}
        if extra:
            params.update(extra)
        return params

    def _get(self, path, config_path="/md", extra=None):
        params = self._params(config_path, extra)
        debug_log.log("AOS8 REST", f"GET {self.base_url}{path}  config_path={config_path}  {debug_log.redact(extra or {})}")
        resp = self.session.get(
            f"{self.base_url}{path}",
            params=params,
            verify=self.verify_tls,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path, body=None, config_path="/md", extra=None):
        debug_log.log("AOS8 REST", f"POST {self.base_url}{path}  config_path={config_path}  body={debug_log.redact(body or {})}")
        resp = self.session.post(
            f"{self.base_url}{path}",
            params=self._params(config_path, extra),
            json=body or {},
            verify=self.verify_tls,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def get_path(self, path, config_path="/md"):
        """GET an arbitrary path (from endpoints.yaml) relative to the controller."""
        return self._get(path, config_path)

    def post_path(self, path, body, config_path="/md"):
        """POST an arbitrary path (from endpoints.yaml) relative to the controller."""
        return self._post(path, body, config_path)

    def write_memory(self, config_path="/md"):
        return self._post("/v1/configuration/object/write_memory", {}, config_path)

    def show_command(self, command, config_path="/mm"):
        """Run a CLI "show" command via the documented showcommand passthrough API.

        https://developer.arubanetworks.com/aos8/docs/showcommand-api
        Verified real commands (per Aruba's own docs/dev-hub examples): "show switches"
        and "show ap database long". Defaults config_path to "/mm" since these are
        network-wide monitoring queries meant to run at Mobility Master scope, not
        against a single device node.
        """
        return self._get("/v1/configuration/showcommand", config_path, extra={"command": command})

    def discover(self, candidate_paths):
        """Best-effort probe of the controller's own API index/spec.

        AOS8 doesn't publicly document a single canonical "list all endpoints" path, and
        it varies by firmware. This tries a handful of common self-documenting locations
        so the real ap-convert endpoint names in endpoints.yaml can be confirmed/corrected
        against your actual controller instead of guessed.
        """
        results = {}
        for path in candidate_paths:
            try:
                resp = self.session.get(
                    f"{self.base_url}{path}",
                    params={"UIDARUBA": self.uidaruba} if self.uidaruba else {},
                    verify=self.verify_tls,
                    timeout=self.timeout,
                )
                entry = {"status_code": resp.status_code, "ok": resp.ok}
                if resp.ok:
                    try:
                        entry["body_preview"] = str(resp.json())[:2000]
                    except ValueError:
                        entry["body_preview"] = resp.text[:2000]
                results[path] = entry
            except requests.RequestException as exc:
                results[path] = {"error": str(exc)}
        return results
