"""Weekly digest: what changed, what is at risk, what happens next. Reads opssignal.db, no AI.

Run:  python digest.py          prints the digest to the terminal (a test; nothing is sent)
      python digest.py --send   also posts it to the Slack alert channel (ALERT_CHANNEL in .env)
"""
import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv

from db import get_connection, init_db
from slack_client import SlackClient

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2}
CLOSED_STATUSES = {"done", "canceled", "cancelled", "duplicate"}


def _day(d):
    return f"{d:%b} {d.day}"


def _when(iso):
    d = datetime.fromisoformat(iso).astimezone(timezone.utc)
    return f"{_day(d)} {d:%H:%M} UTC"


def _value(v):
    return v if v not in (None, "") else "none"


def build_digest(conn, today=None, now=None, days=7):
    """Returns the digest as plain text."""
    now = now or datetime.now(timezone.utc)
    today = today or date.today()
    since = (now - timedelta(days=days)).isoformat(timespec="seconds")
    horizon = today + timedelta(days=days)
    lines = [f"WEEKLY DIGEST — {_day(today)}, {today.year}", ""]

    # --- what changed ---
    changes = conn.execute(
        """SELECT h.*, i.identifier, p.name AS project_name
           FROM change_history h
           LEFT JOIN issues i ON i.issue_id = h.issue_id
           LEFT JOIN projects p ON p.project_id = h.project_id
           WHERE h.changed_at >= ? ORDER BY h.changed_at DESC, h.history_id DESC""", (since,)).fetchall()
    lines.append(f"WHAT CHANGED (last {days} days)")
    for h in changes:
        item = h["identifier"] or h["project_name"] or "Project"
        by = h["changed_by"] or "unknown"
        lines.append(f"- {item} {h['field_name']}: {_value(h['old_value'])} → {_value(h['new_value'])}"
                     f" · by {by} ({h['source']}) · {_when(h['changed_at'])}")
    if not changes:
        lines.append("- No changes recorded.")
    lines.append("")

    # --- what's at risk ---
    risks = sorted((dict(r) for r in conn.execute(
        """SELECT r.*, i.identifier, p.name AS project_name FROM risks r
           LEFT JOIN issues i ON i.issue_id = r.issue_id
           LEFT JOIN projects p ON p.project_id = r.project_id
           WHERE r.status = 'open'""")),
        key=lambda r: (SEVERITY_RANK.get(r["severity"], 9), r["first_seen_at"]))
    lines.append(f"WHAT'S AT RISK ({len(risks)} open)")
    for r in risks:
        where = r["identifier"] or r["project_name"] or "Project"
        lines.append(f"- [{r['severity']}] {r['title']} ({where}): {r['detail']}")
    if not risks:
        lines.append("- No open risks.")
    lines.append("")

    # --- what happens next ---
    lines.append("WHAT HAPPENS NEXT")
    pending = conn.execute(
        """SELECT a.*, i.identifier FROM approvals a JOIN issues i ON i.issue_id = a.issue_id
           WHERE a.decision = 'pending' ORDER BY a.approval_id""").fetchall()
    lines.append("Waiting for approval in Slack:")
    for a in pending:
        lines.append(f"- {a['identifier']} {a['field_name']} → {a['new_value']} (approval #{a['approval_id']})")
    if not pending:
        lines.append("- Nothing waiting.")
    due = [r for r in conn.execute(
        """SELECT identifier, title, due_date, owner, status FROM issues
           WHERE due_date IS NOT NULL AND due_date >= ? AND due_date <= ? ORDER BY due_date, identifier""",
        (today.isoformat(), horizon.isoformat()))
        if (r["status"] or "").lower() not in CLOSED_STATUSES]
    lines.append(f"Due in the next {days} days:")
    for r in due:
        lines.append(f"- {r['identifier']} {r['title']} — due {_day(date.fromisoformat(r['due_date']))}"
                     f" (owner: {r['owner'] or 'Unassigned'})")
    if not due:
        lines.append("- Nothing due.")
    return "\n".join(lines)


def send_digest(text, slack=None, channel=None):
    """Post the digest to Slack. Raises if Slack does not accept it."""
    load_dotenv()
    channel = channel or os.getenv("ALERT_CHANNEL", "").strip()
    if not channel:
        raise RuntimeError("ALERT_CHANNEL is empty in .env")
    slack = slack or SlackClient(os.getenv("SLACK_BOT_TOKEN", "").strip())
    resp = slack.post_text(channel, text)
    if not (resp and resp.get("ok")):
        raise RuntimeError(f"Slack did not accept the digest: {(resp or {}).get('error', 'no response')}")
    return channel


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the weekly digest.")
    parser.add_argument("--send", action="store_true", help="post to Slack instead of only printing")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    conn = get_connection()
    try:
        init_db(conn)
        text = build_digest(conn)
    finally:
        conn.close()
    print(text)
    if args.send:
        channel = send_digest(text)
        print(f"\n[sent to {channel}]")
    else:
        print("\n[test only: nothing sent. Use --send to post to Slack]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
