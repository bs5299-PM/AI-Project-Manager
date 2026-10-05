"""Saves proposals and applies the human's decision."""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from coordination import write_lock
from db import get_connection
from proposals import ALLOWED_FIELDS

log = logging.getLogger("opssignal.webhook")

SELECT_APPROVAL = """
SELECT a.*, i.identifier, i.title, i.linear_id
FROM approvals a LEFT JOIN issues i ON i.issue_id = a.issue_id
WHERE a.approval_id = ?
"""


@dataclass
class Result:
    status: str  # confirmed, rejected, already_decided, stale, failed, not_found
    approval: Optional[dict] = None
    detail: str = ""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(conn, approval_id):
    row = conn.execute(SELECT_APPROVAL, (approval_id,)).fetchone()
    return dict(row) if row else None


def save_outcome(conn, outcome, source_message):
    """Store a proposal as pending, or a 'not sure' as needs_clarification. Returns the approval id."""
    p = outcome.proposal
    with conn:
        cur = conn.execute(
            """INSERT INTO approvals (issue_id, field_name, old_value, new_value, source_message, decision)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (p.issue_id, p.field, p.old_value, p.new_value, source_message, "pending") if p
            else (None, None, None, None, source_message, "needs_clarification"),
        )
    return cur.lastrowid


def confirm(approval_id, decided_by, linear):
    """Update Linear, record the change in history, mark the approval confirmed."""
    with write_lock:
        conn = get_connection()
        try:
            a = load(conn, approval_id)
            if a is None:
                return Result("not_found")
            if a["decision"] != "pending":
                return Result("already_decided", a)
            field = a["field_name"]
            if field not in ALLOWED_FIELDS:
                return Result("failed", a, "unsupported field")

            try:
                live = linear.get_issue_fields(a["linear_id"]).get(field)
            except Exception as exc:
                log.error("  could not read Linear before confirming: %s", exc)
                return Result("failed", a, str(exc))
            if (live or None) != (a["old_value"] or None):
                with conn:
                    conn.execute("UPDATE approvals SET decision='needs_clarification' WHERE approval_id=?",
                                 (approval_id,))
                return Result("stale", a, f"Linear now shows {live or 'nothing'}")

            try:
                linear.update_issue(a["linear_id"], field, a["new_value"])
            except Exception as exc:
                log.error("  Linear update FAILED: %s", exc)
                return Result("failed", a, str(exc))

            now = _now()
            with conn:  # our copy, the history row and the decision are saved together
                conn.execute(f"UPDATE issues SET {field} = ? WHERE issue_id = ?", (a["new_value"], a["issue_id"]))
                conn.execute(
                    """INSERT INTO change_history
                       (issue_id, project_id, field_name, old_value, new_value, changed_by, changed_at, source)
                       VALUES (?, NULL, ?, ?, ?, ?, ?, 'slack')""",
                    (a["issue_id"], field, a["old_value"], a["new_value"], decided_by, now),
                )
                conn.execute(
                    "UPDATE approvals SET decision='confirmed', decided_by=?, decided_at=? WHERE approval_id=?",
                    (decided_by, now, approval_id),
                )
            return Result("confirmed", load(conn, approval_id))
        finally:
            conn.close()


def reject(approval_id, decided_by):
    """Mark the approval rejected. Nothing else changes."""
    with write_lock:
        conn = get_connection()
        try:
            a = load(conn, approval_id)
            if a is None:
                return Result("not_found")
            if a["decision"] != "pending":
                return Result("already_decided", a)
            with conn:
                conn.execute("UPDATE approvals SET decision='rejected', decided_by=?, decided_at=? WHERE approval_id=?",
                             (decided_by, _now(), approval_id))
            return Result("rejected", load(conn, approval_id))
        finally:
            conn.close()
