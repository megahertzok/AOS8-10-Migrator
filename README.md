# AOS8 → AOS10/Central AP Migrator

A GUI tool for the **AP-convert step** of migrating from an AOS8 controller-managed
network to AOS10, managed by Aruba Central. It does not touch WLAN/auth/config —
that stays manual in Central. It covers, end to end: AP inventory (live or CSV),
pre-flight validation, batch conversion, post-migration verification, scoped rollback
(single AP / group / site), migration tracking, and Central site assignment.

> **This is an unofficial, community-built tool.** It is not produced, reviewed, or
> supported by Hewlett Packard Enterprise or Aruba Networks, and does not use HPE's
> logo or Element mark. It follows the color, type, and voice guidance published at
> [design-system.hpe.design](https://design-system.hpe.design) as a courtesy to a
> consistent, familiar experience for HPE Aruba Networking engineers — that's a style
> choice, not a claim of affiliation. I'm just a guy trying to solve a problem and wanted to share it with the world.

## How this is put together

- **The site (`docs/`) is the entire GUI.** It's hosted on GitHub Pages at
  [https://megahertzok.github.io/AOS8-10-Migrator/](https://megahertzok.github.io/AOS8-10-Migrator/). If you're just using the tool,
  you never install or run anything — open that URL.
- **The proxy agent (`proxy-agent/`) is the only thing anyone runs locally**, and only
  because a browser page can't reach an AOS8 controller directly (self-signed cert,
  no CORS headers), call Central's API from a foreign origin, or open an SSH socket.
  It's a small Flask app that the GUI talks to over `localhost`, and it's the only
  thing that ever talks to your controller, Central, or an AP directly. One instance
  covers a whole site/engagement — it doesn't need to run per-person, just somewhere
  with network reach to the controller and APs (a jump box, or an engineer's laptop
  while on-site/VPN'd in). Point the GUI's "Proxy agent URL" field at wherever it's
  running.

## Running the proxy agent

1. Get it onto the machine that has network access to your controller/APs: the
   Connect step (step 1 in the GUI) has a **Download proxy agent (.zip)** link — it
   pulls the whole repo as a zip via GitHub's own archive endpoint, no separate build
   step to maintain. Unzip it and open the `proxy-agent` folder. (Or `git clone` the
   repo if you'd rather.)
2. **macOS**: double-click `start_proxy_agent.command`.  
**Windows**: double-click `start_proxy_agent.bat`.
  
   **Linux**: coming-soon
   
   **macOS Note:** When you run start_proxy_agent.command from finder, it will bug you about it not being trusted and wanting to send it to the recycle bin. You'll have to go into system preferences, Privacy & Security then run it from the Security tab. This is only because I'm too poor for a code signing certificate.

3. Nothing to type — it creates a virtualenv, upgrades pip (needed for `pystray`'s
   macOS dependencies to install from prebuilt wheels instead of failing to compile —
   see "Known gaps" if this still fails for you), installs dependencies, and starts
   itself on `http://127.0.0.1:8765`.
4. **Look for its icon in your menu bar (Mac) or system tray (Windows).** That's the
   confirmation it's running — this isn't a silent background process. Click the icon
   for a menu: **Open GUI** (opens the tool in your browser — the proxy agent serves
   the GUI itself at `http://127.0.0.1:8765/`, so this works even without GitHub Pages),
   **Debug mode**, and **Quit**. On a headless machine with no display (e.g. a
   server-room jump box), it automatically falls back to console-only mode — set
   `proxy_agent.tray_icon: false` in `config.yaml` to skip the tray attempt entirely.
5. Open the GUI (the tray's "Open GUI," `docs/index.html` locally, or the live Pages
   URL) and, on the Connect step, confirm the Proxy Agent URL matches, then connect to
   your AOS8 Mobility Master and to Central. If you plan to use rollback, also fill in
   AP SSH credentials (or leave them blank to reuse your AOS8 login).
6. Everything else happens in the browser.

To let a team share one proxy agent instance, edit `proxy-agent/config.yaml`
(copied from `config.example.yaml` on first run) and set `proxy_agent.host: 0.0.0.0`,
then point everyone's GUI at `http://<that-machine's-ip>:8765`.

### Debug console

Click **Debug console** (top right of the GUI) any time to open a drawer showing every
REST call and SSH command the proxy agent makes in real time — method, URL, config_path,
SSH target host, the actual command run — with credentials/tokens always redacted
(`password=***`, `UIDARUBA=***`, etc). Toggle it on with the checkbox in the drawer, or
from the tray icon's menu. Off by default (`proxy_agent.debug: false` in `config.yaml`),
since it's meant for troubleshooting a specific run, not left on all the time.

### Live log viewer (proxy agent's own window)

Separately from the browser's Debug console, the proxy agent keeps a persistent,
color-coded log of what it's actually doing — connects, conversions, rollbacks, CSV
imports, errors — always on (not gated by Debug mode, which only adds the noisier
per-REST-call trace on top). Three ways to see it:

- **Tray icon → View Logs** opens a small native window (dark theme, matching the
  GUI) that tails the log live: <span style="color:#05cc93">green/bold</span> for
  success, plain gray for info, <span style="color:#ec8c25">orange</span> for
  warnings, <span style="color:#fc6161">red/bold</span> for errors.
- Run it directly: `python3 proxy-agent/log_viewer.py` (works even if the tray icon
  itself isn't available).
- Or just watch the raw file — no GUI needed: `tail -f proxy-agent/proxy_agent.log`.
  Each line is `TIMESTAMP [LEVEL] [category] message`, and it rotates automatically
  (3 backups, 2&nbsp;MB each) so it won't grow unbounded.

`proxy-agent/proxy_agent.log*` is gitignored, same as `config.yaml` and the tracking
database — it's local runtime state, never committed.

## Security model

- Controller, Central, and AP SSH credentials are entered in the browser and sent
  **only** to the proxy agent, over `http://<proxy-host>:8765`. They never reach
  GitHub or any third party.
- The proxy agent holds sessions/tokens in memory for the life of the process only —
  nothing is written to disk unless you deliberately add it to `config.yaml`.
- CORS on the proxy agent is restricted to the Pages origin plus localhost
  (`proxy_agent.allowed_origins` in `config.yaml`).
- `proxy-agent/config.yaml` and `proxy-agent/migration_state.db` are gitignored —
  never commit real credentials or migration history.
- SSH to APs uses `paramiko`'s `AutoAddPolicy` (accepts the AP's host key on first
  connect without a known_hosts prompt) — reasonable given the proxy agent already
  operates inside your trusted management network, but worth knowing.

### What the browser remembers

So you're not retyping hostnames/URLs every visit, the GUI saves a few fields to
`localStorage` (this browser only, never sent anywhere but the proxy agent you
configure): controller host, AOS8/AP SSH *usernames* (not passwords), Central base
URL and Client ID (not secrets/tokens), firmware source host/path, and the
tracking auto-refresh interval — plus the active session IDs so a page refresh
doesn't orphan a live connection. **Every password, secret, and token field is
deliberately excluded** — those live only in the form while you're using it and in
the proxy agent's memory for that request. **Clear saved data** (top right of the
GUI) wipes all of it and reloads the page. This is separate from — and doesn't
touch — actual migration history, which lives in the proxy agent's tracking
database (`proxy-agent/migration_state.db`), not the browser.

## The migration workflow, step by step

The GUI's left sidebar is a numbered, vertical checklist — follow it top to bottom.
This mirrors HPE's own documented procedure (VSG "Upgrading from ArubaOS 8 to 10" and
the AOS10 "Migrating controller managed APs" guide); each step maps to one of their
documented CLI stages. **Rollback** and the **Tracking dashboard** sit below a divider
in the sidebar, outside the numbered flow — rollback is only for when something needs
to be undone, tracking is available any time.

| # | Sidebar step | What it does | Underlying command |
|---|---|---|---|
| 1 | Connect | Logs into AOS8, Central, and (optionally) sets AP SSH creds for rollback | `POST /v1/api/login`, Central OAuth2 |
| 2 | AP inventory | Loads topology, then builds the AP database — live or from a CSV | `show switches`, `show ap database long` |
| 3 | Pre-flight checks | Stages APs, pre-validates licensing/group, tests the firmware source | `ap convert add`, `ap convert pre-validate` |
| 4 | Execute conversion | Reboots the selected APs into AOS10 | `ap convert active ... server/local-flash ...` |
| 5 | Verify in Central | Confirms online status, name, and site match | Central device inventory |
| 6 | Assign to site | Places a converted-but-unassigned AP into its target site | `POST /central/v2/sites/associate` |
| &mdash; | Rollback *(optional)* | Cancels in-flight jobs, or SSHes to already-converted APs and reverts them | `ap convert cancel` / `convert-aos-ap cap <controller>` |
| &mdash; | Tracking *(always available)* | Dashboard of every AP's current state, with auto-refresh (15s&ndash;5min) | reads the local tracking store |

Fields marked with a red **\*** are required to move on; everything else (TLS
verification, refresh tokens, AP SSH credentials, firmware server auth) is optional —
rollback in particular is entirely optional and never blocks the main flow.

## Getting your Aruba Central API credentials

The Connect step's Aruba Central card needs four things: **API Gateway base URL**,
**Client ID**, **Client secret**, and **Access token** (plus an optional **Refresh
token**). Per Aruba's own [Central API
FAQ](https://arubanetworking.hpe.com/techdocs/Archived/central/2.5.5/content/faqs/api.htm):

1. In Central, set the filter to **Global**, then go to **Maintain → Organization →
   Platform Integration → Rest API**.
2. Open the **My Apps & Tokens** tab and click **+ Add Apps & Token** to create an
   application — this is where you get the **Client ID** and **Client secret**.
3. Select that application and click **Generate** to create a token, then **Download
   Token** to get the **Access token** and **Refresh token**.
4. The **API Gateway base URL** is shown on the same page — it's region-specific
   (e.g. `apigw-uswest4.central.arubanetworks.com`).

Access tokens expire after **2 hours**; the refresh token is valid for **14 days** and
lets the proxy agent refresh automatically without you regenerating anything by hand —
worth filling in even though the GUI marks it optional. These same steps are also
shown inline in the GUI itself (Connect step → "How do I get these values?").

## Mobility Conductor / Mobility Device hierarchy

AOS 8 is not built around a single, all-powerful controller. Instead, it uses a hierarchical architecture where a **Mobility Conductor** (formerly known as a **Mobility Master**) manages one or more **Mobility Devices (MDs)**. Aruba updated the terminology as part of its modernization effort, but if you still say "Mobility Master," nobody on the networking team is going to look at you funny. Most of us know exactly what you mean.

The distinction matters because the Mobility Conductor is responsible for orchestration, configuration management, and providing a centralized view of the deployment. It is **not** where APs actually terminate. Every AP is anchored to a specific Mobility Device, and operational commands targeting an AP must be executed against the MD that owns that AP.

This can be confusing when you're looking at the environment from the top of the hierarchy. The AP is visible from the Conductor, the configuration is visible from the Conductor, and the AP may even *appear* to belong to the Conductor. But when it comes time to perform AP-local operations, Aruba expects those commands to be sent to the correct Mobility Device. 

If topology hasn't been loaded yet, AP rows fall back to `config_path: "/md"`, which only works correctly for a standalone controller (not a real MM with multiple MDs) — always load topology first in a real MM deployment.


## Pre-flight checks

Before executing a conversion, the Convert & Rollback tab surfaces:

- **Licensing and group assignment** — `ap convert pre-validate` itself checks that
  each AP is licensed on Central and reports which Central group it will land in.
  This *is* the licensing check; there's no separate Central API call for it. Run it
  and read the result before Execute.
- **Firmware source reachability** — pick a delivery method (local-flash, or a
  tftp/ftp/http/https/scp server) and click "Test firmware source." This confirms the
  server/flash is *reachable*, not that the exact image file is present — AOS8 has no
  documented API for the latter. For local-flash, it runs a best-effort `show storage`
  query and shows you the raw listing to eyeball the filename yourself.
- **Configuration-retention warnings** — always shown, from HPE's "Configuration
  retained or migrated" doc: the AP's native/management VLAN is never retained after
  conversion (it always assumes VLAN 1 on the uplink); AP1X, HTTP proxy, PPPoE, and
  mesh settings stay on the AP but are **not** migrated into Central, and a mismatch
  can make the AP flap and auto-restore. If your APs use any of these, configure the
  equivalent settings in the target Central AP group *before* converting.

## Post-migration verification

"Verify selected in Central" (Convert & Rollback tab) checks each selected AP's
serial against Central's device inventory and reports: found, online, whether its
Central name matches what was recorded pre-migration, and whether its site matches
the intended target (once assigned). It doesn't block anything — it's a checklist, not
a gate — so you can also cross-check the older way HPE's own guide suggests: look at
`show ap lldp neighbors` from the AP's switch port; a still-AOS8 AP shows as a CAP,
a converted one shows as an IAP.

## Rollback — single AP, group, or site

HPE's docs are explicit that reverting AOS10 back to AOS8 **"cannot be performed at
the group level"** — it's always a per-AP command (`convert-aos-ap cap
<controller-address>`) issued on the AP's own console. This tool's "group" and "site"
rollback scopes are a GUI convenience: pick a scope, "Load matching APs" expands it to
the concrete AP list (via the local tracking store), and "Roll back scope" loops the
correct per-AP action for each one:

- If an AP is still mid-conversion (`converting`/`pre_validated`/`discovered`), it
  cancels the pending job (`ap convert cancel`) rather than attempting a revert.
- If an AP has already converted, the proxy agent SSHes directly to the AP's
  management IP and runs `convert-aos-ap cap <controller-address>`. Central's Remote
  Console was considered for this but turned out to be an interactive WebSocket
  terminal with no documented single-command API — direct SSH is the reliable path.
  The controller address is auto-filled from the AP's recorded anchor-MD IP.
- **IP staleness**: an AP typically gets a new DHCP lease once it's running AOS10, so
  the pre-migration IP AOS8 reported may no longer be current. If a Central session is
  connected, the rollback flow looks up the AP's *current* IP from Central's device
  inventory first and only falls back to the older recorded IP if that lookup fails.
- After a successful revert, re-run "Refresh AP list" on the Inventory tab and check
  the AP reappears under its `original_ap_group` (a column the tracking store
  preserves from first discovery, separate from the live `ap_group` field) to confirm
  it landed back where it started.

## CSV workflows

Every data table (AP Inventory, Tracking, unassigned-APs list on Site Assignment) has
an **Export CSV** button producing a real file download with the same columns as the
tracking store. AP Inventory also has **Import CSV**, which seeds/merges the tracking
store from a hand-edited or externally-exported CSV — useful for planning a migration
offline, entirely before connecting to a controller. Expected columns: `mac` (required
— the row key), plus any of `name`, `ap_group`, `state`, `notes`, `md_ip`, `md_name`,
`md_config_path`, `serial`, `central_site_id`, `ap_ip` (all optional; unknown extra
columns are ignored, so you can keep your own notes columns in the same file). CSV
parsing/writing is hand-rolled in `docs/assets/app.js` (RFC4180-ish, handles quoted
fields with embedded commas) — no external library, so the page stays dependency-free
and works offline once loaded.

## Migration tracking

The proxy agent keeps a local SQLite table (`proxy-agent/migration_state.db`) of every
AP it's touched, since a fully-converted AP eventually leaves the AOS8 controller's own
visibility. State machine:

```
discovered → pre_validated → converting → converted_unassigned → assigned_to_site
                                    ↓
                               rolled_back (via cancel, or revert-to-campus)
                                    ↓
                                 failed
```

## Known gaps — read before relying on this against a real controller

AOS8's REST API mirrors its CLI hierarchy, but the exact object/action path names are
generated per firmware/controller and aren't fully enumerable from public docs. AP
inventory, topology, and conversion status are sourced through the documented
`showcommand` passthrough (`show switches`, `show ap database long`,
`show ap convert-status`, `show storage`), which is on solid ground — the CLI verb set
for `ap convert add/pre-validate/active/cancel/delete/clear-all` is also confirmed
against HPE's own docs. What's still `UNVERIFIED` in
[`proxy-agent/endpoints.yaml`](proxy-agent/endpoints.yaml) is the exact **REST object
name** each of those write actions maps to (no public doc confirms them) and the exact
JSON field names in showcommand responses (`app.py`'s `_first()`/`_extract_rows()`
helpers try several likely variants). Before relying on this against a real controller:

1. Connect to the MM/controller in the GUI.
2. Hit `GET /api/aos8/discover` (probes the controller's own live API index at `/api`
   after login) to find the real endpoint names.
3. Correct `proxy-agent/endpoints.yaml` to match, and adjust the field-name lists in
   `app.py` if the "Controller (MD)" column or AP serials don't populate correctly.

**SSH rollback** (`proxy-agent/ssh_client.py`) uses a non-interactive `exec_command`,
which works for many Aruba CLI single-shot commands over SSH but not necessarily all —
some Aruba CLIs expect an interactive PTY instead, and a command that triggers an
immediate reboot may close the channel before output flushes back. If
`convert-aos-ap cap` doesn't behave as expected against a real AP, switch to
`client.invoke_shell()` there instead (see the docstring in that file).

**Firmware pre-flight checks** (`proxy-agent/firmware_check.py`) confirm a server is
*reachable*, never that the specific image file exists — AOS8 doesn't expose an API
for that. Read each result's `detail`/`error` field, don't just trust `reachable: true`.

**Tray icon dependencies** (`pystray` + `Pillow`, for the menu-bar/system-tray icon):
on macOS, `pystray`'s Objective-C bindings (`pyobjc-core`) fail to *compile* against
recent Xcode if `pip` tries to build them from source — this happened in testing with
an old bundled `pip` (21.x). The launcher scripts now run `pip install --upgrade pip`
before installing requirements specifically to avoid this (a modern pip picks the
prebuilt wheel instead of trying to compile). If `start_proxy_agent.command` still
fails on this, run `pip install --upgrade pip` manually in the `venv` and retry.
Separately, `pystray` 0.19.x's macOS backend references `PIL.Image.ANTIALIAS`, which
Pillow &ge;10 removed — `tray.py` applies the standard compatibility shim
(`PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS`) before importing `pystray`, so
this shouldn't surface, but it's the fix if a similar `AttributeError` reappears after
a dependency bump.

## Keeping the downloadable zip in sync

The Connect step's "Download proxy agent (.zip)" link points at a **committed** file,
[`docs/downloads/proxy-agent.zip`](docs/downloads/proxy-agent.zip) — not a live GitHub
archive — because GitHub's own repo-archive endpoint always zips the *entire* repo
(docs/ included), and there's no equivalent for just a subfolder. This keeps the
download scoped to exactly what you need to run, but it means the zip has to be
regenerated by hand whenever files under `proxy-agent/` change, before pushing:

```bash
cd AOS8-10-Migrator
rm -f docs/downloads/proxy-agent.zip
git ls-files --cached --others --exclude-standard proxy-agent | zip -q docs/downloads/proxy-agent.zip -@
```

That lists exactly the files git would track (same `.gitignore` rules — `config.yaml`,
`venv/`, `migration_state.db` stay excluded) and zips them with the `proxy-agent/`
path prefix intact, so unzipping produces a clean `proxy-agent/` folder.

## Explicitly out of scope

WLAN, authentication/security profiles, and general configuration migration into
Central — those stay manual.
