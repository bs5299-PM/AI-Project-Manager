"""Creates the SQLite database and its two tables."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "opssignal.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    linear_id         TEXT NOT NULL UNIQUE,
    name              TEXT,
    milestone         TEXT,
    status            TEXT,
    health            TEXT,
    owner             TEXT,
    target_date       TEXT,
    last_update_date  TEXT,
    url               TEXT
);

CREATE TABLE IF NOT EXISTS issues (
    issue_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    linear_id            TEXT NOT NULL UNIQUE,
    identifier           TEXT,
    project_id           INTEGER REFERENCES projects(project_id),
    title                TEXT,
    priority             TEXT,
    owner                TEXT,
    team                 TEXT,
    status               TEXT,
    due_date             TEXT,
    blocked_by_issue_id  INTEGER REFERENCES issues(issue_id)
);

CREATE TABLE IF NOT EXISTS change_history (
    history_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id    INTEGER REFERENCES issues(issue_id),
    project_id  INTEGER REFERENCES projects(project_id),
    field_name  TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    changed_by  TEXT,
    changed_at  TEXT NOT NULL,
    source      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id        INTEGER REFERENCES issues(issue_id),
    field_name      TEXT,
    old_value       TEXT,
    new_value       TEXT,
    source_message  TEXT,
    decision        TEXT NOT NULL DEFAULT 'pending'
                    CHECK (decision IN ('pending', 'confirmed', 'rejected', 'needs_clarification')),
    decided_by      TEXT,
    decided_at      TEXT
);

CREATE TABLE IF NOT EXISTS risks (
    risk_id          TEXT PRIMARY KEY,
    rule             TEXT NOT NULL,
    issue_id         INTEGER REFERENCES issues(issue_id),
    project_id       INTEGER REFERENCES projects(project_id),
    severity         TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    title            TEXT NOT NULL,
    detail           TEXT,
    impact           TEXT,
    required_action  TEXT,
    action_owner     TEXT,
    action_due       TEXT,
    first_seen_at    TEXT NOT NULL,
    opened_at        TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    resolved_at      TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    risk_id   TEXT NOT NULL REFERENCES risks(risk_id),
    sent_at   TEXT NOT NULL,
    channel   TEXT,
    message   TEXT,
    result    TEXT NOT NULL CHECK (result IN ('sent', 'failed')),
    error     TEXT
);

CREATE TABLE IF NOT EXISTS notion_sync_state (
    linear_id     TEXT PRIMARY KEY,
    page_id       TEXT NOT NULL,
    content_hash  TEXT,
    synced_at     TEXT
);
"""


def get_connection(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    # Databases created before the url column existed get it added (existing rows are kept).
    if "url" not in [r["name"] for r in conn.execute("PRAGMA table_info(projects)")]:
        conn.execute("ALTER TABLE projects ADD COLUMN url TEXT")
    conn.commit()
