"""Client for the Aruba Central REST API (OAuth2 bearer token, auto-refresh).

Reference: https://developer.arubanetworks.com/central/docs/api-reference-guide
"""

import time

import requests

import debug_log


class CentralError(Exception):
    pass


class CentralClient:
    def __init__(self, base_url, client_id, client_secret, access_token, refresh_token=None, timeout=20):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.timeout = timeout

    def refresh(self):
        if not self.refresh_token:
            raise CentralError("No refresh_token on file -- generate a new access token in Central.")
        debug_log.log("Central", f"POST {self.base_url}/oauth2/token  grant_type=refresh_token (refreshing access token)")
        resp = requests.post(
            f"{self.base_url}/oauth2/token",
            params={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token", self.refresh_token)
        return self.access_token

    def _headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    def _request(self, method, path, retry=True, **kwargs):
        debug_log.log("Central REST", f"{method} {self.base_url}{path}  {debug_log.redact(kwargs)}")
        resp = requests.request(
            method, f"{self.base_url}{path}", headers=self._headers(), timeout=self.timeout, **kwargs
        )
        if resp.status_code == 401 and retry:
            self.refresh()
            return self._request(method, path, retry=False, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def list_sites(self, sites_path):
        return self._request("GET", sites_path)

    def list_devices(self, devices_path, params=None):
        return self._request("GET", devices_path, params=params or {})

    def associate_devices_to_site(self, associate_path, serials, site_id, device_type="IAP", rate_limit_delay=0.2):
        """Assign devices to a Central site.

        Confirmed against Aruba's own aruba/central-automation-studio (UI/assets/js/site-mgmt.js):
        POST /central/v2/sites/associate, one call per device -- {device_id, device_type, site_id} --
        not a batch endpoint. A small delay between calls mirrors that tool's own rate-limit handling.
        """
        results = []
        for serial in serials:
            try:
                result = self._request(
                    "POST", associate_path, json={"device_id": serial, "device_type": device_type, "site_id": site_id}
                )
                results.append({"serial": serial, "result": result})
            except requests.HTTPError as exc:
                results.append({"serial": serial, "error": str(exc)})
            time.sleep(rate_limit_delay)
        return results
