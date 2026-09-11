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
        """Exchange the refresh token for a new access token. Called automatically by
        _request() on a 401 -- the access token's 2-hour expiry is handled reactively
        rather than tracking a timer, which is simpler and self-correcting. The
        refresh token itself lasts 14 days; once *that* expires this raises a
        CentralError clearly distinct from a plain request failure, since the fix is
        different (re-generate a token in Central) from a transient network error."""
        if not self.refresh_token:
            raise CentralError("No refresh_token on file -- generate a new access token in Central.")
        debug_log.log("Central", f"POST {self.base_url}/oauth2/token  grant_type=refresh_token (refreshing access token)")
        try:
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
        except requests.HTTPError as exc:
            debug_log.event("Central", f"Access token refresh failed: {exc} -- refresh token may itself be expired (14-day limit)", level="error")
            raise CentralError(
                f"Refreshing the Central access token failed ({exc}) -- the refresh token itself may have expired "
                "(valid 14 days) or been revoked. Generate a new access token in Central and reconnect."
            ) from exc
        data = resp.json()
        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token", self.refresh_token)
        debug_log.event("Central", "Access token refreshed automatically", level="success")
        return self.access_token

    def _headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    def _request(self, method, path, retry=True, retries_429=3, **kwargs):
        """A 429 means Central's own rate limiter, not a bug -- back off and retry a
        bounded number of times (Retry-After header if Central sends one, else a
        fixed 2s) rather than surfacing a spurious failure to the GUI mid-batch."""
        debug_log.log("Central REST", f"{method} {self.base_url}{path}  {debug_log.redact(kwargs)}")
        resp = requests.request(
            method, f"{self.base_url}{path}", headers=self._headers(), timeout=self.timeout, **kwargs
        )
        if resp.status_code == 401 and retry:
            self.refresh()
            return self._request(method, path, retry=False, retries_429=retries_429, **kwargs)
        if resp.status_code == 429 and retries_429 > 0:
            delay = float(resp.headers.get("Retry-After", 2))
            debug_log.event(
                "Central", f"Rate limited (429) on {method} {path} -- waiting {delay:.1f}s, {retries_429} retr{'y' if retries_429 == 1 else 'ies'} left",
                level="warning",
            )
            time.sleep(delay)
            return self._request(method, path, retry=retry, retries_429=retries_429 - 1, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _get_paginated(self, path, params=None, limit=1000, max_pages=200):
        """Page through a Central list endpoint using its offset/limit convention,
        combining every page's rows into one response shaped like a single page.

        The exact top-level key holding the row list (e.g. "sites", "devices") isn't
        confirmed for every endpoint, so this finds it generically -- the first list
        value in page one's response -- rather than hardcoding a guess per endpoint.
        Stops when a page returns fewer than `limit` rows, which is correct regardless
        of whether the endpoint also exposes a "total" count field (which also varies
        and isn't relied on here). `max_pages` is a sanity cap against an unexpected
        response shape causing an infinite loop, not a real-world limit -- 200 pages
        at the default limit of 1000 covers 200,000 rows.
        """
        params = dict(params or {})
        params.setdefault("limit", limit)
        offset = 0
        combined = None
        list_key = None
        for _ in range(max_pages):
            params["offset"] = offset
            page = self._request("GET", path, params=params)
            if not isinstance(page, dict):
                return page  # unexpected shape -- don't try to paginate something we don't understand
            if combined is None:
                combined = dict(page)
                for key, value in page.items():
                    if isinstance(value, list):
                        list_key = key
                        break
                if list_key is None:
                    return page  # no list found in the response -- nothing to paginate
            else:
                page_rows = page.get(list_key)
                if isinstance(page_rows, list):
                    combined[list_key].extend(page_rows)
            page_rows = page.get(list_key) if list_key else None
            if not isinstance(page_rows, list) or len(page_rows) < limit:
                break
            offset += limit
        return combined

    def list_sites(self, sites_path):
        return self._get_paginated(sites_path)

    def list_devices(self, devices_path, params=None):
        return self._get_paginated(devices_path, params=params)

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
