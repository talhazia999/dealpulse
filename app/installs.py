"""Stores each HubSpot account that installs DealPulse: its OAuth tokens, plus who installed it.
Every install is a qualified lead, so `python -m app.installs` prints them as CSV.

Only tokens and install details are stored here. Deal data is never stored."""

import csv
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS installs (
    hub_id        INTEGER PRIMARY KEY,
    hub_domain    TEXT,
    user_email    TEXT,
    access_token  TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at    REAL NOT NULL,
    installed_at  TEXT NOT NULL,
    updated_at    TEXT NOT NULL
)
"""


@dataclass
class Install:
    hub_id: int
    hub_domain: str | None
    user_email: str | None
    access_token: str
    refresh_token: str
    expires_at: float  # unix seconds
    installed_at: str
    updated_at: str


class InstallStore:
    def __init__(self, path: str = config.DATABASE_PATH):
        self.path = path
        with self._connect() as db:
            db.execute(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def get(self, hub_id: int) -> Install | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM installs WHERE hub_id = ?", (hub_id,)).fetchone()
        return Install(**dict(row)) if row else None

    def save(self, hub_id: int, *, access_token: str, refresh_token: str, expires_in: int,
             hub_domain: str | None = None, user_email: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO installs (hub_id, hub_domain, user_email, access_token, refresh_token,
                                      expires_at, installed_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hub_id) DO UPDATE SET
                    hub_domain    = COALESCE(excluded.hub_domain, installs.hub_domain),
                    user_email    = COALESCE(excluded.user_email, installs.user_email),
                    access_token  = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at    = excluded.expires_at,
                    updated_at    = excluded.updated_at
                """,
                (hub_id, hub_domain, user_email, access_token, refresh_token,
                 time.time() + expires_in, now, now),
            )

    def all(self) -> list[Install]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM installs ORDER BY installed_at DESC").fetchall()
        return [Install(**dict(r)) for r in rows]


if __name__ == "__main__":
    writer = csv.writer(sys.stdout)
    writer.writerow(["hub_id", "hub_domain", "user_email", "installed_at"])
    for install in InstallStore().all():
        writer.writerow([install.hub_id, install.hub_domain, install.user_email, install.installed_at])
