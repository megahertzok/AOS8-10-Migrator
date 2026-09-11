"""Local SQLite persistence for per-AP migration state.

This is the tracking source of truth: once an AP fully converts and leaves the AOS8
controller's own visibility, this is the only place its migration history still lives.
"""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "migration_state.db"

STATES = [
    "discovered",
    "pre_validated",
    "converting",
    "converted_unassigned",
    "assigned_to_site",
    "failed",
    "rolled_back",
]


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS aps (
            mac TEXT PRIMARY KEY,
            name TEXT,
            ap_group TEXT,
            original_ap_group TEXT,
            state TEXT NOT NULL DEFAULT 'discovered',
            last_updated REAL NOT NULL,
            notes TEXT,
            md_ip TEXT,
            md_name TEXT,
            md_config_path TEXT,
            serial TEXT,
            central_site_id TEXT,
            ap_ip TEXT,
            model TEXT
        )
        """
    )
    # Additive migration for DBs created before a column existed -- keeps existing
    # local tracking history intact across upgrades instead of requiring a wipe.
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(aps)").fetchall()}
    for column in ("original_ap_group", "central_site_id", "ap_ip", "model"):
        if column not in existing_cols:
            conn.execute(f"ALTER TABLE aps ADD COLUMN {column} TEXT")
    conn.commit()
    conn.close()


def upsert_ap(
    mac,
    name=None,
    ap_group=None,
    state=None,
    notes=None,
    md_ip=None,
    md_name=None,
    md_config_path=None,
    serial=None,
    central_site_id=None,
    ap_ip=None,
    model=None,
):
    if state is not None and state not in STATES:
        raise ValueError(f"Unknown state: {state}")
    conn = _connect()
    existing = conn.execute("SELECT * FROM aps WHERE mac = ?", (mac,)).fetchone()
    now = time.time()
    if existing:
        # original_ap_group is intentionally never overwritten here -- it's the
        # pre-migration baseline used to verify a rollback landed the AP back where
        # it started, distinct from ap_group which tracks the AP's *current* group.
        conn.execute(
            """UPDATE aps SET
                name = COALESCE(?, name),
                ap_group = COALESCE(?, ap_group),
                state = COALESCE(?, state),
                last_updated = ?,
                notes = COALESCE(?, notes),
                md_ip = COALESCE(?, md_ip),
                md_name = COALESCE(?, md_name),
                md_config_path = COALESCE(?, md_config_path),
                serial = COALESCE(?, serial),
                central_site_id = COALESCE(?, central_site_id),
                ap_ip = COALESCE(?, ap_ip),
                model = COALESCE(?, model)
               WHERE mac = ?""",
            (name, ap_group, state, now, notes, md_ip, md_name, md_config_path, serial, central_site_id, ap_ip, model, mac),
        )
    else:
        conn.execute(
            """INSERT INTO aps
                (mac, name, ap_group, original_ap_group, state, last_updated, notes,
                 md_ip, md_name, md_config_path, serial, central_site_id, ap_ip, model)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mac, name, ap_group, ap_group, state or "discovered", now, notes, md_ip, md_name, md_config_path, serial, central_site_id, ap_ip, model),
        )
    conn.commit()
    conn.close()


def set_state(mac, state, notes=None):
    upsert_ap(mac, state=state, notes=notes)


def import_rows(rows):
    """Bulk-upsert AP rows from a CSV import (or any external source). Each row is a
    dict using the same field names as upsert_ap's keyword arguments; unknown keys are
    ignored so a hand-edited CSV with extra columns doesn't break the import."""
    known_fields = {
        "mac", "name", "ap_group", "state", "notes", "md_ip", "md_name",
        "md_config_path", "serial", "central_site_id", "ap_ip", "model",
    }
    imported = 0
    errors = []
    for row in rows:
        mac = (row.get("mac") or "").strip()
        if not mac:
            errors.append({"row": row, "error": "missing mac"})
            continue
        kwargs = {k: (v.strip() if isinstance(v, str) else v) or None for k, v in row.items() if k in known_fields and k != "mac"}
        try:
            upsert_ap(mac, **kwargs)
            imported += 1
        except ValueError as exc:
            errors.append({"row": row, "error": str(exc)})
    return {"imported": imported, "errors": errors}


def list_aps(state=None, ap_group=None):
    conn = _connect()
    query = "SELECT * FROM aps"
    clauses, params = [], []
    if state:
        clauses.append("state = ?")
        params.append(state)
    if ap_group:
        clauses.append("ap_group = ?")
        params.append(ap_group)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY last_updated DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_ap(mac):
    conn = _connect()
    row = conn.execute("SELECT * FROM aps WHERE mac = ?", (mac,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_by_site(central_site_id):
    conn = _connect()
    rows = conn.execute("SELECT * FROM aps WHERE central_site_id = ?", (central_site_id,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_by_group(ap_group):
    """Matches either the AP's current group or its pre-migration original_ap_group,
    so a rollback-by-group scope still finds APs mid-migration whose live ap_group
    field may already reflect a Central group name rather than the AOS8 one."""
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM aps WHERE ap_group = ? OR original_ap_group = ?", (ap_group, ap_group)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
